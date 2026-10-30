"""Persistent runtime preferences and optional-package environment support."""

from __future__ import annotations

import importlib
import os
import platform
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping


DEFAULT_RUNTIME_SETTINGS = {"workers": 4, "backend": "auto"}
BACKENDS = {"cpu", "auto", "gpu"}
APPDATA_DIR = "PYTSA"
CUDA_WHEELS = {
    "12x": "cupy-cuda12x[ctk]",
    "13x": "cupy-cuda13x[ctk]",
}
PACKAGE_SPECS = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "statsmodels": "statsmodels",
    "scikit-learn": "scikit-learn",
    "neurokit2": "neurokit2",
    "matplotlib": "matplotlib",
    "openpyxl": "openpyxl",
    "PyWavelets": "PyWavelets",
    "Pillow": "Pillow",
    "cvxopt": "cvxopt",
}
ALLOWED_PACKAGES = frozenset((*PACKAGE_SPECS.values(), *CUDA_WHEELS.values()))
_DLL_DIRECTORY_HANDLES = []
_DLL_DIRECTORIES_REGISTERED: set[str] = set()


def _user_data_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _user_config_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def runtime_settings_path() -> Path:
    return _user_config_root() / APPDATA_DIR / "runtime_settings.json"


def runtime_environment_root() -> Path:
    abi = f"py{sys.version_info.major}{sys.version_info.minor}-{platform.machine().lower()}"
    return _user_data_root() / APPDATA_DIR / "runtime" / abi


def runtime_environment_python() -> Path:
    if os.name == "nt":
        return runtime_environment_root() / "Scripts" / "python.exe"
    return runtime_environment_root() / "bin" / "python"


def runtime_environment_site_packages() -> Path:
    if os.name == "nt":
        return runtime_environment_root() / "Lib" / "site-packages"
    return runtime_environment_root() / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def _validated_settings(settings: Mapping) -> dict[str, int | str]:
    if not isinstance(settings, Mapping):
        raise ValueError("runtime settings must be a JSON object")
    workers = settings.get("workers", DEFAULT_RUNTIME_SETTINGS["workers"])
    backend = settings.get("backend", DEFAULT_RUNTIME_SETTINGS["backend"])
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 32:
        raise ValueError("workers must be an integer from 1 to 32")
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise ValueError("backend must be cpu, auto, or gpu")
    return {"workers": workers, "backend": backend}


def validate_runtime_settings(settings: Mapping) -> dict[str, int | str]:
    """Validate externally supplied runtime settings at their input boundary."""
    return _validated_settings(settings)


def load_runtime_settings() -> dict[str, int | str]:
    """Load the shared settings schema consumed by core.py."""
    path = runtime_settings_path()
    if not path.is_file():
        return dict(DEFAULT_RUNTIME_SETTINGS)
    import json

    return _validated_settings(json.loads(path.read_text(encoding="utf-8")))


def save_runtime_settings(settings: Mapping) -> dict[str, int | str]:
    """Validate and persist the runtime settings as a single JSON document."""
    import json

    validated = _validated_settings(settings)
    path = runtime_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return validated


def activate_external_environment() -> Path | None:
    """Expose optional packages installed into the frozen app's isolated venv."""
    if not getattr(sys, "frozen", False):
        return None
    packages = runtime_environment_site_packages()
    if not packages.is_dir():
        return None
    site.addsitedir(str(packages))
    # cuda.pathfinder searches libraries under site.getsitepackages(), which
    # derives its entries from site.PREFIXES rather than sys.path. addsitedir()
    # alone therefore makes Python imports work while leaving CUDA wheel DLLs
    # invisible to CuPy's dynamic-library resolver in frozen builds.
    runtime_prefix = str(runtime_environment_root())
    normalized_prefix = os.path.normcase(os.path.abspath(runtime_prefix))
    if not any(
        os.path.normcase(os.path.abspath(prefix)) == normalized_prefix
        for prefix in site.PREFIXES
    ):
        site.PREFIXES.append(runtime_prefix)
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        nvidia_dirs = packages / "nvidia"
        dll_dirs = [
            *packages.glob("*.libs"),
            *nvidia_dirs.glob("*/bin"),
            *nvidia_dirs.glob("*/*/bin"),
        ]
        for dll_dir in dll_dirs:
            normalized_dir = os.path.normcase(os.path.abspath(dll_dir))
            if normalized_dir not in _DLL_DIRECTORIES_REGISTERED:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(dll_dir)))
                _DLL_DIRECTORIES_REGISTERED.add(normalized_dir)
    return packages


def _run_streamed(command: list[str], emit: Callable[[str], None]) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    for line in process.stdout:
        emit(line.rstrip())
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"Package installation exited with code {return_code}.")


def _ensure_frozen_venv(emit: Callable[[str], None]) -> Path:
    python = runtime_environment_python()
    if python.is_file():
        return python
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher is None:
        raise RuntimeError(
            f"Install CPython {sys.version_info.major}.{sys.version_info.minor} with the Python Launcher, "
            "then restart PYTSA and retry."
        )
    environment = runtime_environment_root()
    environment.parent.mkdir(parents=True, exist_ok=True)
    emit(f"Creating isolated persistent environment at {environment}")
    _run_streamed(
        [launcher, f"-{sys.version_info.major}.{sys.version_info.minor}", "-m", "venv", str(environment)],
        emit,
    )
    return python


def install_optional_packages(packages: list[str], emit: Callable[[str], None]) -> None:
    """Install only app-approved optional packages without invoking a shell."""
    selected = list(dict.fromkeys(packages))
    if not selected or any(package not in ALLOWED_PACKAGES for package in selected):
        raise ValueError("The selected package list is empty or contains an unsupported package.")
    if getattr(sys, "frozen", False):
        python = _ensure_frozen_venv(emit)
        site_packages = runtime_environment_site_packages()
        site_packages.mkdir(parents=True, exist_ok=True)
        command = [str(python), "-m", "pip", "install", "--target", str(site_packages), *selected]
        emit(f"Installing into the isolated PYTSA environment: {site_packages}")
    else:
        command = [sys.executable, "-m", "pip", "install", *selected]
        emit(f"Installing into the active Python: {sys.executable}")
    _run_streamed(command, emit)
    if getattr(sys, "frozen", False):
        emit("Installed into the persistent isolated environment. Restart PYTSA, then run environment detection to confirm imports.")


def _run_nvidia_smi() -> tuple[str | None, str | None, str | None, str | None]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None, None, None, "nvidia-smi was not found."
    devices = subprocess.run(
        [executable, "--query-gpu=name,driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if devices.returncode:
        return None, None, None, devices.stderr.strip() or f"nvidia-smi exited with code {devices.returncode}"
    rows = [line.strip().split(",", 1) for line in devices.stdout.splitlines() if line.strip()]
    if not rows:
        return None, None, None, "nvidia-smi found no NVIDIA device."
    device = "; ".join(row[0].strip() for row in rows)
    driver = rows[0][1].strip() if len(rows[0]) > 1 else None
    result = subprocess.run(
        [executable],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        return device, driver, None, result.stderr.strip() or f"nvidia-smi exited with code {result.returncode}"
    text = result.stdout
    cuda = re.search(r"CUDA Version:\s*([0-9.]+)", text)
    return device, driver, cuda.group(1) if cuda else None, None


def detect_environment(emit: Callable[[str], None] | None = None) -> dict:
    """Import supported packages and run a small synchronized CuPy calculation."""
    if emit is None:
        emit = lambda _line: None
    result = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "cpu": platform.processor() or platform.machine() or "unknown",
        "cpu_count": os.cpu_count() or 1,
        "packages": {},
        "nvidia": {"device": None, "driver": None, "cuda": None, "error": None},
        "cupy": {"imported": False, "functional": False, "version": None, "error": None},
    }
    for module_name, distribution in (
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("scipy", "scipy"),
        ("statsmodels", "statsmodels"),
        ("sklearn", "scikit-learn"),
        ("neurokit2", "neurokit2"),
        ("matplotlib", "matplotlib"),
        ("openpyxl", "openpyxl"),
        ("pywt", "PyWavelets"),
        ("PIL", "Pillow"),
        ("cvxopt", "cvxopt"),
    ):
        emit(f"Importing {module_name}…")
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            result["packages"][distribution] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            result["packages"][distribution] = {"ok": True, "version": str(getattr(module, "__version__", "unknown"))}

    emit("Detecting NVIDIA driver and CUDA compatibility…")
    try:
        device, driver, cuda, error = _run_nvidia_smi()
        result["nvidia"].update(device=device, driver=driver, cuda=cuda, error=error)
    except Exception as exc:
        result["nvidia"]["error"] = f"{type(exc).__name__}: {exc}"

    emit("Importing CuPy and checking a synchronized matrix operation…")
    try:
        cupy = importlib.import_module("cupy")
        result["cupy"]["imported"] = True
        result["cupy"]["version"] = str(getattr(cupy, "__version__", "unknown"))
        device_count = int(cupy.cuda.runtime.getDeviceCount())
        if device_count < 1:
            raise RuntimeError("CuPy imported, but no CUDA device is visible.")
        matrix = cupy.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=cupy.float32)
        matrix_sum = float(matrix.sum().get())
        product = matrix @ matrix
        cupy.cuda.Stream.null.synchronize()
        observed = product.get().tolist()
        if matrix_sum != 10.0 or observed != [[7.0, 10.0], [15.0, 22.0]]:
            raise RuntimeError(f"Unexpected CuPy calculation result: sum={matrix_sum}, product={observed}")
        result["cupy"]["functional"] = True
        result["cupy"]["devices"] = device_count
    except Exception as exc:
        result["cupy"]["error"] = f"{type(exc).__name__}: {exc}"
    return result


def compatible_cuda_wheels(environment: Mapping) -> tuple[str, ...]:
    cuda_text = environment.get("nvidia", {}).get("cuda")
    if not cuda_text:
        return ()
    version_parts = [int(part) for part in str(cuda_text).split(".")[:2]]
    cuda_version = tuple((version_parts + [0, 0])[:2])
    compatible = []
    if cuda_version >= (12, 0):
        compatible.append("12x")
    if cuda_version >= (13, 0):
        compatible.append("13x")
    return tuple(compatible)


def selected_cupy_package(environment: Mapping, wheel: str) -> str:
    compatible = compatible_cuda_wheels(environment)
    if not compatible:
        raise ValueError("An NVIDIA driver with CUDA 12.x or 13.x support was not detected; no CuPy wheel can be selected.")
    chosen = compatible[-1] if wheel == "自动（依驱动）" else wheel
    if chosen not in compatible:
        raise ValueError(f"The selected CUDA {chosen} wheel is incompatible with the detected driver.")
    return CUDA_WHEELS[chosen]
