"""Execution settings and numeric matrix kernels for supported backends.

Only selected channel-association matrices use CuPy. Signal processing,
statistical tests, forecasting, and EDA algorithms continue to run on CPU.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import numpy as np


def _runtime_settings():
    try:
        from runtime_settings import load_runtime_settings
    except ModuleNotFoundError as exc:
        if exc.name == "runtime_settings":
            return {}, None
        return {}, f"Runtime settings unavailable: {exc}"
    try:
        settings = load_runtime_settings()
    except Exception as exc:  # retain analysis availability if settings are unreadable
        return {}, f"Runtime settings could not be loaded: {exc}"
    return (dict(settings) if isinstance(settings, Mapping) else {}), None


def _as_workers(value, *, explicit):
    try:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError
        numeric = float(value)
        if not np.isfinite(numeric) or numeric != math.floor(numeric):
            raise ValueError
        workers = int(numeric)
    except (TypeError, ValueError, OverflowError) as exc:
        if explicit:
            raise ValueError("workers 必须是 1–32 的整数。") from exc
        return 1, "Runtime worker count was invalid; using 1 worker."
    if not 1 <= workers <= 32:
        if explicit:
            raise ValueError("workers 必须在 1–32 之间。")
        return 1, "Runtime worker count was outside 1–32; using 1 worker."
    return workers, None


def _try_cupy():
    import cupy as cp

    count = int(cp.cuda.runtime.getDeviceCount())
    if count < 1:
        raise RuntimeError("no CUDA devices are visible to CuPy")
    # Force a tiny device operation so driver/runtime errors are discovered
    # before selecting the backend for a full analysis.
    cp.asarray([1.0], dtype=cp.float64).sum().item()
    properties = cp.cuda.runtime.getDeviceProperties(0)
    device_name = properties.get("name", "unknown CUDA device")
    if isinstance(device_name, bytes):
        device_name = device_name.decode("utf-8", errors="replace")
    return cp, str(device_name)


def resolve_compute_backend(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve config overrides over persisted runtime settings and defaults."""
    cfg = dict(config or {})
    runtime, runtime_error = _runtime_settings()
    has_backend_override = "backend" in cfg or "compute_backend" in cfg
    requested_value = cfg.get("backend", cfg.get("compute_backend", runtime.get("backend", "cpu")))
    requested = str(requested_value).strip().lower()
    if requested not in {"cpu", "auto", "gpu"}:
        raise ValueError("backend 须为 cpu、auto 或 gpu。")
    if "workers" in cfg:
        workers, worker_error = _as_workers(cfg["workers"], explicit=True)
    else:
        workers, worker_error = _as_workers(runtime.get("workers", 1), explicit=False)
    fallback = (runtime_error if (not has_backend_override or "workers" not in cfg) else None) or worker_error
    resolved = {"requested": requested, "actual": "cpu", "workers": workers,
                "fallback_reason": fallback, "library_version": None,
                "device": None, "_cupy": None}
    if requested == "cpu":
        return resolved
    try:
        cp, device_name = _try_cupy()
    except Exception as exc:
        reason = f"CuPy/CUDA unavailable: {exc}"
        if requested == "gpu":
            raise RuntimeError(f"GPU backend requested but unavailable. {reason}") from exc
        resolved["fallback_reason"] = "; ".join(filter(None, (fallback, reason)))
        return resolved
    resolved.update(actual="gpu", library_version=str(cp.__version__),
                    device=device_name, _cupy=cp)
    return resolved


def _association_matrices(values, xp):
    array = xp.asarray(values, dtype=xp.float64)
    n = int(array.shape[0])
    if n < 2:
        size = int(array.shape[1])
        missing = xp.full((size, size), xp.nan, dtype=xp.float64)
        return missing, missing.copy(), missing.copy()
    centered = array - xp.mean(array, axis=0, keepdims=True)
    covariance = centered.T @ centered / (n - 1)
    std = xp.sqrt(xp.maximum(xp.diag(covariance), 0.0))
    denominator = std[:, None] * std[None, :]
    correlation = xp.where(denominator > 0, covariance / xp.where(denominator > 0, denominator, 1.0), xp.nan)
    standardized = xp.where(std[None, :] > 0,
                            centered / xp.where(std[None, :] > 0, std[None, :], 1.0), xp.nan)
    nonconstant = std > 0
    gram = standardized.T @ standardized / n
    mean_square = xp.mean(standardized * standardized, axis=0)
    squared_distance = mean_square[:, None] + mean_square[None, :] - 2.0 * gram
    euclidean = xp.sqrt(xp.maximum(squared_distance, 0.0))
    euclidean = xp.where(nonconstant[:, None] & nonconstant[None, :], euclidean, xp.nan)
    diagonal = xp.eye(int(array.shape[1]), dtype=bool) & nonconstant[:, None]
    euclidean = xp.where(diagonal, 0.0, euclidean)
    return correlation, covariance, euclidean


def association_matrices(values, resolved: dict[str, Any]):
    """Compute Pearson, sample covariance and standardized Euclidean matrices."""
    if resolved["actual"] == "gpu":
        try:
            matrices = _association_matrices(values, resolved["_cupy"])
            return tuple(resolved["_cupy"].asnumpy(matrix) for matrix in matrices)
        except Exception as exc:
            if resolved["requested"] == "gpu":
                raise RuntimeError(f"CuPy GPU matrix computation failed: {exc}") from exc
            reason = f"CuPy matrix computation failed: {exc}"
            resolved["actual"] = "cpu"
            resolved["_cupy"] = None
            prior = resolved.get("fallback_reason")
            resolved["fallback_reason"] = "; ".join(filter(None, (prior, reason)))
    return _association_matrices(values, np)


def public_backend_metadata(resolved: Mapping[str, Any]) -> dict[str, Any]:
    return {"requested": resolved.get("requested", "cpu"),
            "actual": resolved.get("actual", "cpu"),
            "workers": int(resolved.get("workers", 1)),
            "fallback_reason": resolved.get("fallback_reason"),
            "library_version": resolved.get("library_version"),
            "device": resolved.get("device"),
            "scope": "CuPy accelerates Pearson correlation, sample covariance, and standardized Euclidean matrices only; other analyses remain on CPU."}
