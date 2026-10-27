"""Persistent, safe workflow services for the PYTSA desktop application."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

PROJECT_FORMAT = "PYTSA_PROJECT"
PROJECT_VERSION = 1
TEMPLATE_SCHEMA = "pyt-workflow-template"
TEMPLATE_VERSION = 1
REPORT_TEMPLATE_VERSION = 1
_PARAMETER_KINDS = {"prepare", "analysis", "forecast", "eda", "combined"}
_REPORT_SECTIONS = {"summary", "tests", "matrices", "forecast", "eda", "warnings", "methods", "annotations"}
_REPORT_FORMATS = {"csv", "xlsx", "json", "png", "html"}
_RUN_KINDS = {"analysis", "forecast", "eda", "batch_item"}
_MAX_JSON_BYTES = 2 * 1024 * 1024 * 1024


class WorkspaceFormatError(ValueError):
    """A saved or imported workspace file is malformed, unsupported, or unsafe."""


class BatchCancelled(Exception):
    """A worker's cooperative cancellation at a safe boundary."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_path(value: str | os.PathLike[str]) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _safe_token(value: str, label: str = "identifier") -> str:
    token = str(value)
    if token in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", token):
        raise ValueError(f"{label}格式无效。")
    return token


def _safe_name(value: str) -> str:
    name = str(value).strip()
    if not name or len(name) > 120 or any(ord(ch) < 32 for ch in name) or any(ch in name for ch in "\\/:*?\"<>|"):
        raise ValueError("名称不能为空，也不能包含路径分隔符或Windows文件名保留字符。")
    return name


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkspaceFormatError(f"JSON包含重复字段：{key}")
        result[key] = value
    return result


def _json_loads(text: str | bytes) -> Any:
    def reject_constant(value: str) -> Any:
        raise WorkspaceFormatError(f"JSON包含非法数值：{value}")
    try:
        return json.loads(text, object_pairs_hook=_strict_object, parse_constant=reject_constant)
    except WorkspaceFormatError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorkspaceFormatError(f"JSON文件无法读取：{exc}") from exc


def _json_dumps(value: Any, *, pretty: bool = True) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None,
                          separators=None if pretty else (",", ":"))
        return text + ("\n" if pretty else "")
    except (TypeError, ValueError) as exc:
        raise WorkspaceFormatError(f"内容无法安全写入JSON：{exc}") from exc


def _encode(value: Any) -> Any:
    """Tag known scientific values; reject arbitrary classes and executable formats."""
    if isinstance(value, pd.DataFrame):
        try:
            table = value.to_json(orient="table", date_format="iso", date_unit="ns", force_ascii=False, index=True)
        except Exception as exc:
            raise WorkspaceFormatError(f"数据表无法安全序列化：{exc}") from exc
        return {"$pyt": "dataframe", "table": table, "attrs": _encode(dict(value.attrs))}
    if isinstance(value, pd.Series):
        frame = value.to_frame(name=value.name if value.name is not None else "value")
        return {"$pyt": "series", "table": _encode(frame), "name": value.name}
    if isinstance(value, pd.Timestamp):
        return {"$pyt": "timestamp", "value": value.isoformat()}
    if isinstance(value, np.ndarray):
        data = [_encode(item) for item in value.tolist()] if value.dtype.kind == "O" else _encode(value.tolist())
        return {"$pyt": "ndarray", "dtype": str(value.dtype), "data": data}
    if isinstance(value, np.generic):
        return _encode(value.item())
    if isinstance(value, Path):
        return {"$pyt": "path", "value": str(value)}
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return {"$pyt": "datetime", "kind": type(value).__name__, "value": value.isoformat()}
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return {"$pyt": "float", "value": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise WorkspaceFormatError("保存内容的对象字段必须使用文本名称。")
        if "$pyt" in value:
            return {"$pyt": "mapping", "items": [[key, _encode(item)] for key, item in value.items()]}
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return {"$pyt": "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    raise WorkspaceFormatError(f"不支持保存此类数据：{type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("$pyt")
    if tag == "mapping":
        return {key: _decode(item) for key, item in value.get("items", [])}
    if tag == "tuple":
        return tuple(_decode(item) for item in value.get("items", []))
    if tag == "path":
        return Path(value["value"])
    if tag == "float":
        return {"nan": float("nan"), "inf": float("inf"), "-inf": -float("inf")}[value["value"]]
    if tag == "datetime":
        kind, raw = value.get("kind"), value.get("value")
        if kind == "date":
            return dt.date.fromisoformat(raw)
        if kind == "time":
            return dt.time.fromisoformat(raw)
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if tag == "timestamp":
        return pd.Timestamp(value["value"])
    if tag == "ndarray":
        try:
            return np.asarray(_decode(value.get("data")), dtype=value.get("dtype"))
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("保存的数值数组格式无效。") from exc
    if tag == "dataframe":
        table = value.get("table")
        if not isinstance(table, str):
            raise WorkspaceFormatError("保存的数据表格式无效。")
        try:
            frame = pd.read_json(io.StringIO(table), orient="table")
            attrs = _decode(value.get("attrs", {}))
            if isinstance(attrs, dict):
                frame.attrs = attrs
            return frame
        except WorkspaceFormatError:
            raise
        except Exception as exc:
            raise WorkspaceFormatError(f"保存的数据表无法恢复：{exc}") from exc
    if tag == "series":
        frame = _decode(value.get("table"))
        if not isinstance(frame, pd.DataFrame) or frame.shape[1] != 1:
            raise WorkspaceFormatError("保存的序列格式无效。")
        series = frame.iloc[:, 0]
        series.name = value.get("name")
        return series
    return {key: _decode(item) for key, item in value.items()}


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, _json_dumps(_encode(value)).encode("utf-8"))


def _read_json(path: Path) -> Any:
    try:
        if path.stat().st_size > _MAX_JSON_BYTES:
            raise WorkspaceFormatError("文件超过安全读取上限。")
        return _json_loads(path.read_text(encoding="utf-8-sig"))
    except WorkspaceFormatError:
        raise
    except (OSError, UnicodeError) as exc:
        raise WorkspaceFormatError(f"无法读取文件 {path.name}：{exc}") from exc


def _source_path(source: Any) -> Path | None:
    candidate = source if isinstance(source, (str, os.PathLike)) else (source.get("path") or source.get("source_path")) if isinstance(source, Mapping) else None
    return Path(candidate).expanduser() if candidate is not None and str(candidate).strip() else None


def _file_identity(path: Path) -> str:
    stat = path.stat()
    return f"{_normalize_path(path)}|{stat.st_size}|{stat.st_mtime_ns}"


def _number(value: Any) -> float | None:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        output.update(_flatten(item, name) if isinstance(item, Mapping) else {name: _encode(item)})
    return output


class WorkspaceStore:
    """Local persistence boundary for the UI and its worker threads."""

    LOG_MAX_BYTES = 5 * 1024 * 1024
    LOG_RETAIN_FILES = 3
    RECENT_MAX_ITEMS = 100

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        if root is None:
            root = os.environ.get("PYTSA_HOME")
            if root is None and os.name == "nt":
                root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PYTSA"
            elif root is None:
                root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "PYTSA"
        self.root = Path(root).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def _inside(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceFormatError("工作区内部路径越界。") from exc
        return resolved

    def _dir(self, path: str | Path) -> Path:
        return self._inside(self.root / path)

    # ---- Safe project snapshots ----
    def save_project(self, state: Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
        if not isinstance(state, Mapping):
            raise ValueError("项目状态必须是对象。")
        saved_state = dict(state)
        saved_state.setdefault("project_id", uuid.uuid4().hex)
        source = saved_state.get("source")
        source_file = _source_path(source)
        if source_file is not None:
            source = dict(source) if isinstance(source, Mapping) else {"path": str(source_file)}
            try:
                if source_file.is_file():
                    stat = source_file.stat()
                    source.setdefault("size_bytes", stat.st_size)
                    source.setdefault("mtime_ns", stat.st_mtime_ns)
            except OSError:
                pass
            saved_state["source"] = source
        payload = _json_dumps({"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "saved_at": _utc_now(), "state": _encode(saved_state)}).encode("utf-8")
        if path is None:
            directory = self._dir("projects")
            directory.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(saved_state.get("name") or "project")).strip("-_")[:48] or "project"
            target = directory / f"{slug}-{uuid.uuid4().hex[:10]}.pytproject"
        else:
            target = Path(path).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".json":
            _atomic_bytes(target, payload)
        else:
            plain = archive_path = None
            try:
                with tempfile.NamedTemporaryFile(prefix=".pyt-project.", suffix=".json.tmp", dir=target.parent, delete=False) as stream:
                    plain = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                with tempfile.NamedTemporaryFile(prefix=f".{target.name}.", suffix=".zip.tmp", dir=target.parent, delete=False) as stream:
                    archive_path = Path(stream.name)
                with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                    archive.write(plain, arcname="project.json")
                os.replace(archive_path, target)
                archive_path = None
            finally:
                if plain:
                    plain.unlink(missing_ok=True)
                if archive_path:
                    archive_path.unlink(missing_ok=True)
        return target

    def _read_project(self, path: Path) -> dict[str, Any]:
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    if archive.namelist() != ["project.json"]:
                        raise WorkspaceFormatError("项目压缩包必须只包含project.json。")
                    info = archive.getinfo("project.json")
                    if info.file_size > _MAX_JSON_BYTES or info.flag_bits & 1:
                        raise WorkspaceFormatError("项目文件大小或压缩格式不受支持。")
                    with archive.open(info) as stream:
                        payload = _json_loads(stream.read(_MAX_JSON_BYTES + 1))
            else:
                payload = _read_json(path)
        except WorkspaceFormatError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise WorkspaceFormatError(f"项目文件无法读取：{exc}") from exc
        if not isinstance(payload, dict) or payload.get("format") != PROJECT_FORMAT or payload.get("version") != PROJECT_VERSION or not isinstance(payload.get("state"), dict):
            raise WorkspaceFormatError("项目格式或版本不受支持。")
        return payload

    @staticmethod
    def _source_status(source: Any) -> str:
        path = _source_path(source)
        if path is None:
            return "unknown"
        try:
            stat = path.stat()
        except OSError:
            return "missing"
        if not path.is_file():
            return "missing"
        if not isinstance(source, Mapping) or source.get("size_bytes") is None or source.get("mtime_ns") is None:
            return "unknown"
        return "unchanged" if (source["size_bytes"], source["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns) else "changed"

    def load_project(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        state = _decode(self._read_project(Path(path).expanduser().resolve(strict=False))["state"])
        if not isinstance(state, dict):
            raise WorkspaceFormatError("项目状态恢复后格式无效。")
        state["source_status"] = self._source_status(state.get("source"))
        return state

    # ---- Parameter templates ----
    def _parameter_dir(self, kind: str, create: bool = False) -> Path:
        if kind not in _PARAMETER_KINDS:
            raise ValueError("参数模板类型无效。")
        path = self._dir(Path("templates") / "parameters" / kind)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _validate_parameter_record(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict) or record.get("schema") != TEMPLATE_SCHEMA or record.get("schema_version") != TEMPLATE_VERSION:
            raise WorkspaceFormatError("参数模板格式或版本不受支持。")
        if record.get("kind") not in _PARAMETER_KINDS or not isinstance(record.get("name"), str) or not isinstance(record.get("parameters"), dict):
            raise WorkspaceFormatError("参数模板缺少有效名称、类型或参数。")
        for key in ("id", "created_at", "updated_at"):
            if not isinstance(record.get(key), str):
                raise WorkspaceFormatError(f"参数模板缺少字段：{key}")
        _safe_token(record["id"], "模板ID")
        return record

    def _parameter_records(self, kind: str | None = None) -> list[dict[str, Any]]:
        kinds = [kind] if kind is not None else sorted(_PARAMETER_KINDS)
        records = []
        for item_kind in kinds:
            directory = self._parameter_dir(item_kind)
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                records.append(self._validate_parameter_record(_decode(_read_json(path))))
        return records

    def save_parameter_template(self, name: str, parameters: Mapping[str, Any], *, kind: str = "combined", overwrite: bool = False) -> dict[str, Any]:
        template_name = _safe_name(name)
        if kind not in _PARAMETER_KINDS or not isinstance(parameters, Mapping):
            raise ValueError("参数模板类型或参数格式无效。")
        with self._lock:
            matches = [item for item in self._parameter_records(kind) if item["name"].casefold() == template_name.casefold()]
            if matches and not overwrite:
                raise FileExistsError("同名参数模板已存在；如需覆盖请显式设置overwrite=True。")
            previous = matches[0] if matches else None
            now = _utc_now()
            record = {"schema": TEMPLATE_SCHEMA, "schema_version": TEMPLATE_VERSION,
                      "id": previous["id"] if previous else uuid.uuid4().hex, "name": template_name,
                      "kind": kind, "parameters": dict(parameters),
                      "created_at": previous["created_at"] if previous else now, "updated_at": now}
            target = self._parameter_dir(kind, create=True) / f"{record['id']}.json"
            _atomic_json(target, record)
            return self._validate_parameter_record(_decode(_read_json(target)))

    def list_parameter_templates(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        return sorted(self._parameter_records(kind), key=lambda item: (item["kind"], item["name"].casefold()))

    def get_parameter_template(self, template_id: str) -> dict[str, Any]:
        token = _safe_token(template_id, "模板ID")
        for kind in sorted(_PARAMETER_KINDS):
            path = self._parameter_dir(kind) / f"{token}.json"
            if path.is_file():
                return self._validate_parameter_record(_decode(_read_json(path)))
        raise FileNotFoundError("找不到该参数模板。")

    def copy_parameter_template(self, template_id: str, new_name: str) -> dict[str, Any]:
        source = self.get_parameter_template(template_id)
        return self.save_parameter_template(new_name, source["parameters"], kind=source["kind"])

    def import_parameter_template(self, path: str | os.PathLike[str], *, overwrite: bool = False) -> dict[str, Any]:
        record = self._validate_parameter_record(_decode(_read_json(Path(path).expanduser())))
        return self.save_parameter_template(record["name"], record["parameters"], kind=record["kind"], overwrite=overwrite)

    def export_parameter_template(self, template_id: str, path: str | os.PathLike[str]) -> Path:
        record = self.get_parameter_template(template_id)
        target = Path(path).expanduser().resolve(strict=False)
        _atomic_json(target, record)
        return target

    def delete_parameter_template(self, template_id: str) -> None:
        token = _safe_token(template_id, "模板ID")
        for kind in sorted(_PARAMETER_KINDS):
            path = self._parameter_dir(kind) / f"{token}.json"
            if path.is_file():
                with self._lock:
                    path.unlink()
                return
        raise FileNotFoundError("找不到该参数模板。")

    def check_template_compatibility(self, template: Mapping[str, Any], data_context: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(template, Mapping) or not isinstance(data_context, Mapping):
            raise ValueError("模板和数据上下文必须是对象。")
        params = template.get("parameters", template)
        groups = [(str(key), value) for key, value in params.items() if isinstance(value, Mapping)] if isinstance(params, Mapping) else []
        if not groups:
            groups = [("parameters", params)]
        columns = set(str(item) for item in data_context.get("columns", []))
        issues: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for group_name, config in groups:
            if not isinstance(config, Mapping):
                continue
            for key in ("time_col", "time_column", "column", "forecast_column", "eda_column"):
                value = config.get(key)
                if value not in (None, "") and columns and str(value) not in columns:
                    field = f"{group_name}.{key}"
                    if (field, str(value)) not in seen:
                        issues.append({"field": field, "message": f"模板列“{value}”不在当前数据中。"})
                        seen.add((field, str(value)))
            for key in ("value_cols", "value_columns"):
                value = config.get(key)
                if isinstance(value, (list, tuple)) and columns:
                    for column in value:
                        if str(column) not in columns:
                            field = f"{group_name}.{key}"
                            if (field, str(column)) not in seen:
                                issues.append({"field": field, "message": f"模板列“{column}”不在当前数据中。"})
                                seen.add((field, str(column)))
            for key in ("fs", "sampling_rate"):
                wanted = self._as_number(config.get(key))
                current = self._as_number(data_context.get("sampling_rate", data_context.get("fs")))
                if wanted is not None and current is not None and not math.isclose(wanted, current, rel_tol=1e-6, abs_tol=1e-9):
                    field = f"{group_name}.{key}"
                    if (field, str(current)) not in seen:
                        issues.append({"field": field, "message": f"模板采样率 {wanted:g} 与当前数据 {current:g} 不同。"})
                        seen.add((field, str(current)))
            template_units, current_units = config.get("units"), data_context.get("units")
            if isinstance(template_units, Mapping) and isinstance(current_units, Mapping):
                for column, unit in template_units.items():
                    if column in current_units and str(unit) != str(current_units[column]):
                        field = f"{group_name}.units.{column}"
                        if (field, str(current_units[column])) not in seen:
                            issues.append({"field": field, "message": f"列“{column}”的单位与模板不一致。"})
                            seen.add((field, str(current_units[column])))
        return {"compatible": not issues, "issues": issues}

    @staticmethod
    def _as_number(value: Any) -> float | None:
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    # ---- Version-1 report templates (normalization is shared with report_export) ----
    @staticmethod
    def _normalize_report_template(template: Mapping[str, Any] | None) -> dict[str, Any]:
        try:
            from report_export import normalize_report_template
        except ImportError as exc:
            raise RuntimeError("report_export.normalize_report_template尚未就绪。") from exc
        try:
            canonical = normalize_report_template(dict(template) if template is not None else None)
        except Exception as exc:
            raise WorkspaceFormatError(f"报告模板无效：{exc}") from exc
        if not isinstance(canonical, dict) or canonical.get("version") != REPORT_TEMPLATE_VERSION or not isinstance(canonical.get("name"), str):
            raise WorkspaceFormatError("报告模板版本或名称无效。")
        if not isinstance(canonical.get("sections"), list) or not isinstance(canonical.get("formats"), list):
            raise WorkspaceFormatError("报告模板规范化结果无效。")
        if set(canonical["sections"]) - _REPORT_SECTIONS or set(canonical["formats"]) - _REPORT_FORMATS or not canonical["formats"]:
            raise WorkspaceFormatError("报告模板章节或格式无效。")
        return canonical

    def _report_template_path(self, name: str, create: bool = False) -> Path:
        safe_name = _safe_name(name)
        key = hashlib.sha256(safe_name.casefold().encode("utf-8")).hexdigest()[:24]
        directory = self._dir(Path("templates") / "reports")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{key}.json"

    def save_report_template(self, template: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
        canonical = self._normalize_report_template(template)
        path = self._report_template_path(canonical["name"], create=True)
        with self._lock:
            if path.exists() and not overwrite:
                raise FileExistsError("同名报告模板已存在；如需覆盖请显式设置overwrite=True。")
            _atomic_json(path, canonical)
            return self._normalize_report_template(_decode(_read_json(path)))

    def list_report_templates(self) -> list[dict[str, Any]]:
        directory = self._report_template_path("placeholder").parent
        if not directory.exists():
            return []
        records = [self._normalize_report_template(_decode(_read_json(path))) for path in directory.glob("*.json")]
        return sorted(records, key=lambda item: item["name"].casefold())

    def get_report_template(self, name: str) -> dict[str, Any]:
        path = self._report_template_path(name)
        if not path.is_file():
            raise FileNotFoundError("找不到该报告模板。")
        return self._normalize_report_template(_decode(_read_json(path)))

    def copy_report_template(self, name: str, new_name: str) -> dict[str, Any]:
        template = self.get_report_template(name)
        template["name"] = _safe_name(new_name)
        return self.save_report_template(template)

    def import_report_template(self, path: str | os.PathLike[str], *, overwrite: bool = False) -> dict[str, Any]:
        template = self._normalize_report_template(_decode(_read_json(Path(path).expanduser())))
        return self.save_report_template(template, overwrite=overwrite)

    def export_report_template(self, name: str, path: str | os.PathLike[str]) -> Path:
        target = Path(path).expanduser().resolve(strict=False)
        _atomic_json(target, self.get_report_template(name))
        return target

    def delete_report_template(self, name: str) -> None:
        path = self._report_template_path(name)
        if not path.is_file():
            raise FileNotFoundError("找不到该报告模板。")
        with self._lock:
            path.unlink()

    # ---- Recent projects and data ----
    def _recent_path(self) -> Path:
        return self._dir("recent.json")

    def _read_recent(self) -> list[dict[str, Any]]:
        path = self._recent_path()
        if not path.exists():
            return []
        payload = _decode(_read_json(path))
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("items"), list):
            raise WorkspaceFormatError("最近使用记录格式无效。")
        if any(not isinstance(item, dict) or item.get("kind") not in {"project", "data"} or not isinstance(item.get("path"), str) for item in payload["items"]):
            raise WorkspaceFormatError("最近使用记录包含无效条目。")
        return payload["items"]

    def add_recent_item(self, kind: str, path: str | os.PathLike[str], *, label: str | None = None) -> None:
        if kind not in {"project", "data"}:
            raise ValueError("最近项目类型必须是project或data。")
        normalized = _normalize_path(path)
        record = {"kind": kind, "path": str(Path(path).expanduser()), "normalized_path": normalized,
                  "label": str(label or Path(path).name), "last_used": _utc_now()}
        with self._lock:
            items = self._read_recent()
            items = [item for item in items if not (item["kind"] == kind and item.get("normalized_path", _normalize_path(item["path"])) == normalized)]
            items.insert(0, record)
            self.root.mkdir(parents=True, exist_ok=True)
            _atomic_json(self._recent_path(), {"version": 1, "items": items[:self.RECENT_MAX_ITEMS]})

    def list_recent_items(self, *, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if kind is not None and kind not in {"project", "data"}:
            raise ValueError("最近项目类型必须是project或data。")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("最近记录条数必须是正整数。")
        items = self._read_recent()
        if kind:
            items = [item for item in items if item["kind"] == kind]
        result = []
        for item in items[:min(limit, self.RECENT_MAX_ITEMS)]:
            record = dict(item)
            record.pop("normalized_path", None)
            record["exists"] = Path(record["path"]).expanduser().exists()
            result.append(record)
        return result

    def remove_recent_item(self, kind: str, path: str | os.PathLike[str]) -> None:
        if kind not in {"project", "data"}:
            raise ValueError("最近项目类型必须是project或data。")
        normalized = _normalize_path(path)
        with self._lock:
            items = self._read_recent()
            retained = [item for item in items if not (item["kind"] == kind and item.get("normalized_path", _normalize_path(item["path"])) == normalized)]
            if retained != items:
                _atomic_json(self._recent_path(), {"version": 1, "items": retained})

    # ---- Immutable run history and comparisons ----
    @staticmethod
    def _canonical_run_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, Mapping):
            raise ValueError("运行快照必须是对象。")
        kind, source = snapshot.get("kind"), snapshot.get("source")
        params, metrics = snapshot.get("parameters", {}), snapshot.get("metrics", {})
        curves, results = snapshot.get("curves", {}), snapshot.get("results", {})
        if kind not in _RUN_KINDS or not isinstance(source, Mapping) or not isinstance(params, Mapping) or not isinstance(metrics, Mapping) or not isinstance(curves, Mapping) or not isinstance(results, Mapping):
            raise ValueError("运行快照缺少kind、source、parameters、metrics、curves或results对象。")
        if not isinstance(source.get("identity"), str) or not isinstance(source.get("path"), str):
            raise ValueError("运行快照source必须包含identity和path。")
        clean_metrics: dict[str, Any] = {}
        for name, entry in metrics.items():
            if not isinstance(name, str) or not isinstance(entry, Mapping):
                raise ValueError("运行指标必须使用名称和值/单位对象。")
            value = entry.get("value")
            number = _number(value)
            if value is not None and number is None:
                try:
                    non_finite = not math.isfinite(float(value))
                except (TypeError, ValueError):
                    non_finite = False
                if not non_finite:
                    raise ValueError(f"指标{name}不是有效数值。")
            unit = entry.get("unit")
            clean_metrics[name] = {"value": number, "unit": str(unit) if unit is not None else None}
        clean_curves: dict[str, Any] = {}
        for key, entry in curves.items():
            if not isinstance(key, str) or not isinstance(entry, Mapping):
                raise ValueError("运行曲线必须使用名称和曲线对象。")
            x, y, time_basis = entry.get("x"), entry.get("y"), entry.get("time_basis")
            if not isinstance(x, (list, tuple, np.ndarray)) or not isinstance(y, (list, tuple, np.ndarray)) or len(x) != len(y) or not isinstance(time_basis, Mapping):
                raise ValueError(f"曲线{key}的x/y长度或time_basis无效。")
            clean_x, clean_y = [], []
            for axis, sequence in (("x", x), ("y", y)):
                target = clean_x if axis == "x" else clean_y
                for item in sequence:
                    number = _number(item)
                    if item is not None and number is None:
                        raise ValueError(f"曲线{key}的{axis}轴包含非数值。")
                    target.append(number)
            clean_curves[key] = {"label": str(entry.get("label", key)), "x": clean_x, "y": clean_y,
                                 "x_unit": str(entry.get("x_unit", "")), "y_unit": str(entry.get("y_unit", "")),
                                 "time_basis": dict(time_basis)}
        canonical = {"kind": kind, "source": dict(source), "parameters": dict(params),
                     "metrics": clean_metrics, "curves": clean_curves, "results": dict(results)}
        return _decode(_encode(canonical))

    def _history_path(self, run_id: str, create: bool = False) -> Path:
        token = _safe_token(run_id, "运行ID")
        directory = self._dir("history")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{token}.json"

    def _history_index_path(self) -> Path:
        return self._dir(Path("history") / "index.json")

    def _read_history_index(self) -> list[dict[str, Any]]:
        path = self._history_index_path()
        if not path.exists():
            return []
        payload = _decode(_read_json(path))
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("runs"), list):
            raise WorkspaceFormatError("运行历史索引格式无效。")
        return payload["runs"]

    def record_run(self, snapshot: Mapping[str, Any]) -> str:
        canonical = self._canonical_run_snapshot(snapshot)
        run_id = uuid.uuid4().hex
        path = self._history_path(run_id, create=True)
        record = {"run_id": run_id, "saved_at": _utc_now(), **canonical}
        summary = {key: record[key] for key in ("run_id", "saved_at", "kind", "source", "parameters", "metrics")}
        with self._lock:
            if path.exists():
                raise FileExistsError("运行ID冲突，未覆盖历史。")
            _atomic_json(path, record)
            try:
                runs = self._read_history_index()
                runs.insert(0, summary)
                _atomic_json(self._history_index_path(), {"version": 1, "runs": runs})
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return run_id

    def list_run_history(self, *, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if kind is not None and kind not in _RUN_KINDS:
            raise ValueError("运行类型无效。")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("历史记录条数必须是正整数。")
        records = [item for item in self._read_history_index() if kind is None or item.get("kind") == kind]
        return records[:min(limit, 1000)]

    def get_run_snapshot(self, run_id: str) -> dict[str, Any]:
        path = self._history_path(run_id)
        if not path.is_file():
            raise FileNotFoundError("找不到该运行历史。")
        record = _decode(_read_json(path))
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            raise WorkspaceFormatError("运行历史格式无效。")
        return record

    def compare_runs(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        left, right = self.get_run_snapshot(left_run_id), self.get_run_snapshot(right_run_id)
        a_params, b_params = _flatten(left["parameters"]), _flatten(right["parameters"])
        parameter_rows = []
        for key in sorted(set(a_params) | set(b_params)):
            a, b = a_params.get(key), b_params.get(key)
            parameter_rows.append({"path": key, "left": a, "right": b, "changed": a != b})
        metric_rows = []
        for name in sorted(set(left["metrics"]) | set(right["metrics"])):
            a, b = left["metrics"].get(name), right["metrics"].get(name)
            if a is None or b is None:
                row = {"left_value": a.get("value") if a else None, "right_value": b.get("value") if b else None,
                       "delta": None, "unit": None, "comparable": False, "reason": "缺少一侧指标。"}
            elif a.get("unit") != b.get("unit"):
                row = {"left_value": a.get("value"), "right_value": b.get("value"), "delta": None,
                       "unit": None, "comparable": False, "reason": "两次运行的指标单位不同。"}
            elif a.get("value") is None or b.get("value") is None:
                row = {"left_value": a.get("value"), "right_value": b.get("value"), "delta": None,
                       "unit": a.get("unit"), "comparable": False, "reason": "指标值缺失或非有限。"}
            else:
                row = {"left_value": a["value"], "right_value": b["value"], "delta": b["value"] - a["value"],
                       "unit": a.get("unit"), "comparable": True, "reason": None}
            metric_rows.append({"name": name, **row})
        same_kind = left["kind"] == right["kind"]
        curve_rows, incompatibilities = [], []
        for key in sorted(set(left["curves"]) | set(right["curves"])):
            a, b = left["curves"].get(key), right["curves"].get(key)
            if a is None or b is None:
                reason = "缺少一侧曲线。"
            elif not same_kind:
                reason = "两次运行的分析类型不同。"
            elif (a.get("x_unit"), a.get("y_unit")) != (b.get("x_unit"), b.get("y_unit")):
                reason = "两次运行的曲线单位不同。"
            elif a.get("time_basis") != b.get("time_basis"):
                reason = "两次运行的时间基准不同。"
            else:
                reason = None
            curve_rows.append({"key": key, "compatible": reason is None, "reason": reason, "left": a, "right": b})
            if reason and a is not None and b is not None:
                incompatibilities.append(f"{key}：{reason}")
        if not same_kind:
            incompatibilities.insert(0, "两次运行的分析类型不同。")
        return {"left_run_id": left_run_id, "right_run_id": right_run_id, "parameters": parameter_rows,
                "metrics": metric_rows, "curves": curve_rows, "incompatibilities": incompatibilities}

    # ---- Persistent JSONL logs ----
    def _log_files(self, run_id: str) -> list[Path]:
        token = _safe_token(run_id, "运行ID")
        directory = self._dir("logs")
        if not directory.exists():
            return []
        names = [f"{token}.jsonl", *(f"{token}.{i}.jsonl" for i in range(1, self.LOG_RETAIN_FILES))]
        return [directory / name for name in names if (directory / name).is_file()]

    def _rotate_logs(self, run_id: str) -> None:
        directory = self._dir("logs")
        for index in range(self.LOG_RETAIN_FILES - 1, 0, -1):
            source = directory / (f"{run_id}.jsonl" if index == 1 else f"{run_id}.{index - 1}.jsonl")
            target = directory / f"{run_id}.{index}.jsonl"
            if source.exists():
                os.replace(source, target)

    def write_run_log(self, run_id: str, level: str, message: str, *, details: Any = None,
                      exception: BaseException | str | None = None) -> None:
        token = _safe_token(run_id, "运行ID")
        normalized_level = str(level).upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("日志级别无效。")
        trace = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__)) if isinstance(exception, BaseException) else (str(exception) if exception is not None else None)
        record = {"timestamp": _utc_now(), "run_id": token, "level": normalized_level,
                  "message": str(message), "details": details, "exception": trace}
        line = _json_dumps(_encode(record), pretty=False).encode("utf-8") + b"\n"
        directory = self._dir("logs")
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{token}.jsonl"
            if path.exists() and path.stat().st_size and path.stat().st_size + len(line) > self.LOG_MAX_BYTES:
                self._rotate_logs(token)
            with path.open("ab") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())

    def list_log_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("日志记录条数必须是正整数。")
        directory = self._dir("logs")
        if not directory.exists():
            return []
        pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.\d+)?\.jsonl$")
        recent: dict[str, dict[str, Any]] = {}
        for path in directory.glob("*.jsonl"):
            match = pattern.fullmatch(path.name)
            if not match:
                continue
            run_id, stamp = match.group(1), path.stat().st_mtime_ns
            item = recent.setdefault(run_id, {"run_id": run_id, "_stamp": stamp, "files": 0})
            item["_stamp"] = max(item["_stamp"], stamp)
            item["files"] += 1
        records = sorted(recent.values(), key=lambda item: item["_stamp"], reverse=True)[:min(limit, 1000)]
        for item in records:
            item["last_updated"] = dt.datetime.fromtimestamp(item.pop("_stamp") / 1e9, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
        return records

    def read_run_logs(self, run_id: str | None = None, *, limit: int = 500) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("日志条数必须是正整数。")
        paths = self._log_files(run_id) if run_id is not None else [path for row in self.list_log_runs(limit=1000) for path in self._log_files(row["run_id"])]
        paths.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        records = []
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise WorkspaceFormatError(f"日志文件无法读取：{exc}") from exc
            for line in reversed(lines):
                if not line.strip():
                    continue
                row = _decode(_json_loads(line))
                if not isinstance(row, dict) or not isinstance(row.get("timestamp"), str) or not isinstance(row.get("run_id"), str):
                    raise WorkspaceFormatError("日志记录格式无效。")
                records.append(row)
                if len(records) >= limit:
                    break
            if len(records) >= limit:
                break
        records.sort(key=lambda row: row["timestamp"], reverse=True)
        return records[:min(limit, 5000)]

    def format_run_logs(self, run_id: str | None = None, *, limit: int = 500) -> str:
        output = []
        for row in self.read_run_logs(run_id, limit=limit):
            details = " | " + json.dumps(_encode(row["details"]), ensure_ascii=False, separators=(",", ":")) if row.get("details") is not None else ""
            output.append(f"{row['timestamp']} [{row['level']}] run={row['run_id']} {row['message']}{details}")
            if row.get("exception"):
                output.append(str(row["exception"]))
        return "\n".join(output)

    # ---- Boundary-only, read-only data quality preflight ----
    @staticmethod
    def _row_locations(mask: np.ndarray, labels: list[Any] | None = None) -> tuple[list[int], dict[str, int] | None, dict[str, Any] | None, int]:
        positions = np.flatnonzero(mask)
        sample = [int(item) for item in positions[:20]]
        row_range = {"start": int(positions[0]), "end": int(positions[-1])} if len(positions) else None
        time_range = None
        if labels is not None and len(positions):
            selected = [labels[int(index)] for index in positions if int(index) < len(labels) and labels[int(index)] is not None]
            if selected:
                time_range = {"start": selected[0], "end": selected[-1]}
        return sample, row_range, time_range, int(len(positions))

    @staticmethod
    def _parse_times(series: pd.Series, time_kind: str, time_scale="s", timezone="UTC") -> tuple[np.ndarray, list[Any], np.ndarray]:
        from analysis_options import numeric_seconds, datetime_utc
        if time_kind not in {"auto", "numeric", "datetime"}:
            raise ValueError("时间类型必须是auto、numeric或datetime。")
        if time_kind in {"auto", "numeric"}:
            numeric = pd.to_numeric(series, errors="coerce")
            if time_kind == "numeric" or numeric.notna().sum() >= series.notna().sum():
                values = numeric_seconds(numeric.to_numpy(dtype=float, na_value=np.nan), time_scale)
                valid = np.isfinite(values)
                return values, [float(v) if ok else None for v, ok in zip(values, valid)], valid
        # Preflight still reports malformed dates rather than failing the whole scan.
        parsed = datetime_utc(series, timezone, errors="coerce")
        valid = parsed.notna().to_numpy()
        values = np.full(len(parsed), np.nan, dtype=float)
        if valid.any():
            values[valid] = parsed[valid].astype("int64").to_numpy(dtype=float) / 1e9
        labels = [parsed.iloc[i].isoformat() if valid[i] else None for i in range(len(parsed))]
        return values, labels, valid

    def preflight_dataframe(
        self,
        frame: pd.DataFrame,
        *,
        time_col: str | None = None,
        value_cols: Sequence[str] | None = None,
        sampling_rate: float | None = None,
        sampling_tolerance_seconds: float | None = None,
        outlier_iqr_multiplier: float = 1.5,
        time_kind: str = "auto",
        time_scale: str = "s",
        timezone: str = "UTC",
    ) -> list[dict[str, Any]]:
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("预检输入必须是DataFrame。")
        if time_col is not None and time_col not in frame.columns:
            raise ValueError("预检时间列不存在。")
        columns = list(value_cols) if value_cols is not None else [column for column in frame.select_dtypes(include=["number"]).columns if column != time_col]
        absent = [column for column in columns if column not in frame.columns]
        if absent:
            raise ValueError(f"预检数值列不存在：{', '.join(map(str, absent))}")
        if sampling_rate is not None and (not math.isfinite(float(sampling_rate)) or float(sampling_rate) <= 0):
            raise ValueError("采样率必须是有限正数。")
        if sampling_tolerance_seconds is not None and (not math.isfinite(float(sampling_tolerance_seconds)) or float(sampling_tolerance_seconds) < 0):
            raise ValueError("采样间隔容差不能为负数。")
        if not math.isfinite(float(outlier_iqr_multiplier)) or float(outlier_iqr_multiplier) <= 0:
            raise ValueError("异常值IQR倍数必须是有限正数。")
        findings: list[dict[str, Any]] = []
        # One boundary scan; callers reuse these findings for this unchanged source/config.
        for position, column in enumerate(frame.columns):
            mask = frame.iloc[:, position].isna().to_numpy(dtype=bool)
            if mask.any():
                rows, row_range, _, count = self._row_locations(mask)
                findings.append({"check": "missing_values", "severity": "warning", "message": f"列“{column}”有{count}个缺失值。",
                                 "column": str(column), "count": count, "row_indices": rows, "row_range": row_range, "time_range": None})
        time_values = None
        time_labels = None
        valid_times = None
        if time_col is not None:
            if int(np.sum(np.asarray(frame.columns, dtype=object) == time_col)) != 1:
                raise ValueError("数据中存在重名时间列，无法进行可靠预检。")
            time_values, time_labels, valid_times = self._parse_times(frame[time_col], time_kind, time_scale, timezone)
            invalid = ~valid_times
            if invalid.any():
                rows, row_range, _, count = self._row_locations(invalid)
                findings.append({"check": "invalid_time", "severity": "warning", "message": f"时间列“{time_col}”有{count}个无法解析的值。",
                                 "column": str(time_col), "count": count, "row_indices": rows, "row_range": row_range, "time_range": None})
            if valid_times.any() and not invalid.any():
                duplicate = pd.Series(time_values).duplicated(keep=False).to_numpy()
                if duplicate.any():
                    rows, row_range, time_range, count = self._row_locations(duplicate, time_labels)
                    findings.append({"check": "duplicate_time", "severity": "warning", "message": f"时间列“{time_col}”有{count}行位于重复时间点。",
                                     "column": str(time_col), "count": count, "row_indices": rows, "row_range": row_range, "time_range": time_range})
                if len(time_values) > 1:
                    differences = np.diff(time_values)
                    positive = differences[np.isfinite(differences) & (differences > 0)]
                    expected = 1.0 / float(sampling_rate) if sampling_rate else (float(np.median(positive)) if len(positive) else None)
                    if expected is not None and expected > 0:
                        tolerance = float(sampling_tolerance_seconds) if sampling_tolerance_seconds is not None else max(1e-9, expected * 0.01)
                        irregular = np.zeros(len(time_values), dtype=bool)
                        irregular[1:] = (~np.isfinite(differences)) | (differences <= 0) | (np.abs(differences - expected) > tolerance)
                        if irregular.any():
                            rows, row_range, time_range, count = self._row_locations(irregular, time_labels)
                            findings.append({"check": "irregular_sampling", "severity": "warning", "message": f"时间列“{time_col}”有{count}处间隔偏离预期约{expected:g}秒。",
                                             "column": str(time_col), "count": count, "row_indices": rows, "row_range": row_range, "time_range": time_range})
        for column in columns:
            if int(np.sum(np.asarray(frame.columns, dtype=object) == column)) != 1:
                raise ValueError(f"数值列“{column}”有重名，无法进行可靠预检。")
            series = pd.to_numeric(frame[column], errors="coerce")
            raw = series.to_numpy(dtype=float, na_value=np.nan)
            finite = raw[np.isfinite(raw)]
            if not finite.size:
                continue
            if np.unique(finite).size <= 1:
                mask = np.isfinite(raw)
                rows, row_range, time_range, count = self._row_locations(mask, time_labels)
                findings.append({"check": "constant_column", "severity": "warning", "message": f"数值列“{column}”为常量或仅有一个有效值。",
                                 "column": str(column), "count": count, "row_indices": rows, "row_range": row_range, "time_range": time_range})
            q1, q3 = np.quantile(finite, [0.25, 0.75])
            spread = float(q3 - q1)
            lower, upper = float(q1 - outlier_iqr_multiplier * spread), float(q3 + outlier_iqr_multiplier * spread)
            outlier = np.isfinite(raw) & ((raw < lower) | (raw > upper))
            if outlier.any():
                rows, row_range, time_range, count = self._row_locations(outlier, time_labels)
                findings.append({"check": "outlier_range", "severity": "warning", "message": f"数值列“{column}”有{count}个值超出{outlier_iqr_multiplier:g}×IQR提示范围[{lower:g}, {upper:g}]。",
                                 "column": str(column), "count": count, "row_indices": rows, "row_range": row_range, "time_range": time_range})
        return findings

    # ---- Per-file batch execution and manifest-bound retries ----
    @staticmethod
    def _event_policy(policy: Mapping[str, Any] | None, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if policy is None:
            return {"mode": "none"}
        if not isinstance(policy, Mapping):
            raise ValueError("事件文件策略必须是对象。")
        mode = policy.get("mode")
        if mode == "none":
            return {"mode": "none"}
        if mode == "shared":
            path = Path(policy.get("path", "")).expanduser().resolve(strict=False)
            if not path.is_file():
                raise FileNotFoundError("共用事件文件不存在。")
            return {"mode": "shared", "path": str(path)}
        if mode == "per_file":
            mapping = policy.get("mapping")
            if not isinstance(mapping, Mapping):
                raise ValueError("逐文件事件策略缺少mapping。")
            normalized = {_normalize_path(key): str(Path(value).expanduser().resolve(strict=False)) for key, value in mapping.items()}
            item_paths = [_normalize_path(item["path"]) for item in items]
            if len(item_paths) != len(set(item_paths)):
                raise ValueError("逐文件事件映射暂不接受同一数据文件重复出现。")
            if set(normalized) != set(item_paths):
                raise ValueError("逐文件事件映射必须明确包含本批次的每个数据文件。")
            if any(not Path(path).is_file() for path in normalized.values()):
                raise FileNotFoundError("逐文件映射中的事件文件不存在。")
            return {"mode": "per_file", "mapping": normalized}
        raise ValueError("事件文件必须明确选择none、shared或per_file策略。")

    def _batch_path(self, batch_id: str, create: bool = False) -> Path:
        directory = self._dir(Path("history") / "batches")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{_safe_token(batch_id, '批次ID')}.json"

    def _write_batch(self, batch_id: str, manifest: Mapping[str, Any]) -> None:
        with self._lock:
            _atomic_json(self._batch_path(batch_id, create=True), dict(manifest))

    def _read_batch(self, batch_id: str) -> dict[str, Any]:
        path = self._batch_path(batch_id)
        if not path.is_file():
            raise FileNotFoundError("找不到该批次记录。")
        manifest = _decode(_read_json(path))
        if not isinstance(manifest, dict) or manifest.get("batch_id") != batch_id or not isinstance(manifest.get("items"), list):
            raise WorkspaceFormatError("批次记录格式无效。")
        return manifest

    def run_batch(
        self,
        files: Sequence[str | os.PathLike[str]],
        worker: Callable[..., Mapping[str, Any]],
        *,
        parameters: Mapping[str, Any],
        output_dir: str | os.PathLike[str] | None = None,
        event_policy: Mapping[str, Any] | None = None,
        cancel_event: Any = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not callable(worker) or not isinstance(parameters, Mapping):
            raise ValueError("批量运行需要处理函数和参数对象。")
        paths = list(files)
        if not paths:
            raise ValueError("请选择至少一个数据文件。")
        items = []
        for index, item_path in enumerate(paths, 1):
            path = Path(item_path).expanduser().resolve(strict=False)
            if not path.is_file():
                raise FileNotFoundError(f"数据文件不存在：{path}")
            digest = hashlib.sha256(_normalize_path(path).encode("utf-8")).hexdigest()[:10]
            items.append({"item_id": f"{index:03d}-{digest}", "path": str(path), "display_name": path.name,
                          "source_identity": _file_identity(path), "event_source_identity": None})
        policy = self._event_policy(event_policy, items)
        frozen = _decode(_encode(dict(parameters)))
        return self._execute_batch(items, worker, frozen, policy, output_dir, cancel_event, progress_callback, None)

    def _execute_batch(self, items: Sequence[Mapping[str, Any]], worker: Callable[..., Mapping[str, Any]],
                       parameters: Mapping[str, Any], event_policy: Mapping[str, Any],
                       output_dir: str | os.PathLike[str] | None, cancel_event: Any,
                       progress_callback: Callable[[dict[str, Any]], None] | None,
                       parent_batch_id: str | None) -> dict[str, Any]:
        from core import load_table

        batch_id = uuid.uuid4().hex
        started = time.monotonic()
        cancellation = cancel_event if cancel_event is not None else threading.Event()
        base = Path(output_dir).expanduser().resolve(strict=False) if output_dir is not None else self._dir("batch-output")
        batch_output = base / batch_id
        batch_output.mkdir(parents=True, exist_ok=False)
        run_items = [dict(item) for item in items]
        policy = self._event_policy(event_policy, run_items) if parent_batch_id is None else dict(event_policy)
        for item in run_items:
            item.update(status="not_run", output_dir=None, history_run_id=None, result=None, error=None,
                        elapsed_seconds=0.0, preflight_issues=[])
        manifest: dict[str, Any] = {"batch_id": batch_id, "parent_batch_id": parent_batch_id,
                                    "created_at": _utc_now(), "status": "running", "parameters": dict(parameters),
                                    "event_policy": policy, "output_dir": str(batch_output), "items": run_items,
                                    "summary_csv": None, "cancelled": False}
        self._write_batch(batch_id, manifest)
        completed = 0
        cancelled = False
        event_cache: dict[str, pd.DataFrame] = {}
        shared_event = None
        if policy["mode"] == "shared":
            try:
                shared_event = load_table(policy["path"])
                shared_identity = _file_identity(Path(policy["path"]))
                for item in run_items:
                    item["event_source_identity"] = shared_identity
            except Exception as exc:
                manifest.update(status="setup_failed", finished_at=_utc_now())
                self._write_batch(batch_id, manifest)
                self.write_run_log(batch_id, "ERROR", "批次共用事件文件无法载入。", details={"path": policy["path"]}, exception=exc)
                raise

        def emit(item: Mapping[str, Any], stage: str, message: str, status: str, elapsed: float) -> None:
            if progress_callback is None:
                return
            event = {"batch_id": batch_id, "item_id": item["item_id"], "path": item["path"],
                     "output_dir": item.get("output_dir"), "stage": stage, "message": message,
                     "completed": completed, "total": len(run_items), "elapsed_seconds": max(0.0, elapsed), "status": status}
            try:
                progress_callback(event)
            except Exception as exc:
                self.write_run_log(batch_id, "WARNING", "进度回调发生错误，批次继续执行。", details={"stage": stage}, exception=exc)

        for item in run_items:
            if cancellation.is_set():
                cancelled = True
                break
            item_started = time.monotonic()
            stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(item["path"]).stem)[:32] or "item"
            output = batch_output / f"{item['item_id']}-{stem}"
            output.mkdir(parents=True, exist_ok=False)
            item["output_dir"] = str(output)
            item["status"] = "running"
            emit(item, "load", "正在载入数据文件。", "running", time.monotonic() - item_started)
            terminal_message = "该文件已完成。"
            try:
                raw = load_table(item["path"])
                prepare = parameters.get("prepare", parameters)
                if not isinstance(prepare, Mapping):
                    prepare = {}
                issues = self.preflight_dataframe(raw, time_col=prepare.get("time_col"), value_cols=prepare.get("value_cols"),
                                                  sampling_rate=prepare.get("fs", prepare.get("sampling_rate")),
                                                  time_kind=prepare.get("time_kind", "auto"),
                                                  time_scale=prepare.get("time_scale", "s"), timezone=prepare.get("timezone", "UTC"))
                item["preflight_issues"] = issues
                event_frame = shared_event
                if policy["mode"] == "per_file":
                    event_path = policy["mapping"][_normalize_path(item["path"])]
                    item["event_source_identity"] = _file_identity(Path(event_path))
                    if event_path not in event_cache:
                        event_cache[event_path] = load_table(event_path)
                    event_frame = event_cache[event_path]

                def stage_callback(data: Any) -> None:
                    stage = data if isinstance(data, Mapping) else {"message": str(data)}
                    emit(item, str(stage.get("stage", "processing")), str(stage.get("message", "正在处理。")),
                         "running", time.monotonic() - item_started)

                if cancellation.is_set():
                    item["status"] = "cancelled"
                    cancelled = True
                else:
                    result = worker(dict(item), raw, event_frame, _decode(_encode(dict(parameters))), cancellation, stage_callback)
                    if not isinstance(result, Mapping):
                        raise ValueError("批次处理函数必须返回对象。")
                    if result.get("cancelled"):
                        item["status"] = "cancelled"
                        cancelled = True
                    else:
                        result = dict(result)
                        kind = result.get("kind", "analysis")
                        if kind not in _RUN_KINDS:
                            raise ValueError("批次结果的运行类型无效。")
                        history = {"kind": kind, "source": {"identity": item["source_identity"], "path": item["path"],
                                   "event_path": policy.get("path") if policy["mode"] == "shared" else policy.get("mapping", {}).get(_normalize_path(item["path"]))},
                                   "parameters": dict(parameters), "metrics": result.get("metrics", {}),
                                   "curves": result.get("curves", {}), "results": result.get("results", {k: v for k, v in result.items() if k not in {"kind", "metrics", "curves", "cancelled"}})}
                        item["history_run_id"] = self.record_run(history)
                        item["result"] = result
                        item["status"] = "success"
                item["elapsed_seconds"] = max(0.0, time.monotonic() - item_started)
                if item["status"] == "cancelled":
                    item["error"] = "用户请求取消；当前项目未标记为成功。"
                    terminal_message = "该文件已取消。"
            except BatchCancelled as exc:
                item["status"] = "cancelled"
                item["error"] = str(exc) or "用户请求取消。"
                item["elapsed_seconds"] = max(0.0, time.monotonic() - item_started)
                terminal_message = "该文件已取消。"
                cancelled = True
            except Exception as exc:
                item["status"] = "failed"
                item["error"] = f"{type(exc).__name__}: {exc}"
                item["elapsed_seconds"] = max(0.0, time.monotonic() - item_started)
                terminal_message = "该文件处理失败。"
                self.write_run_log(batch_id, "ERROR", f"批次文件失败：{Path(item['path']).name}",
                                   details={"item_id": item["item_id"], "path": item["path"]}, exception=exc)
            if item["status"] in {"success", "failed", "cancelled"}:
                completed += 1
                self._write_batch(batch_id, manifest)
                emit(item, "complete", terminal_message, item["status"], item["elapsed_seconds"])
            if item["status"] == "cancelled":
                cancelled = True
                break
        if cancellation.is_set():
            cancelled = True
        if cancelled:
            for item in run_items:
                if item["status"] == "not_run":
                    item["error"] = "批次取消后未开始。"
        statuses = {item["status"] for item in run_items}
        summary = batch_output / "summary.csv"
        fields = ["batch_id", "item_id", "path", "status", "elapsed_seconds", "history_run_id", "error"]
        with summary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for item in run_items:
                writer.writerow({"batch_id": batch_id, **item})
        manifest.update(cancelled=cancelled, finished_at=_utc_now(), elapsed_seconds=max(0.0, time.monotonic() - started),
                        status="cancelled" if cancelled else ("completed_with_failures" if "failed" in statuses else "completed"),
                        summary_csv=str(summary))
        self._write_batch(batch_id, manifest)
        returned = _decode(_encode(manifest))
        returned["results"] = returned.pop("items")
        return returned

    def retry_batch(self, batch_id: str, worker: Callable[..., Mapping[str, Any]], *, item_ids: Sequence[str] | None = None,
                    cancel_event: Any = None, progress_callback: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        parent = self._read_batch(batch_id)
        failed = [item for item in parent["items"] if item.get("status") == "failed"]
        available = {item["item_id"]: item for item in failed}
        selected_ids = set(available) if item_ids is None else set(item_ids)
        if not selected_ids:
            raise ValueError("该批次没有可重试的失败项。")
        if selected_ids - set(available):
            raise ValueError("重试只能选择此批次中标记失败的项目。")
        selected = [dict(available[key]) for key in available if key in selected_ids]
        policy = dict(parent["event_policy"])
        for item in selected:
            source_path = Path(item["path"])
            if not source_path.is_file() or _file_identity(source_path) != item.get("source_identity"):
                raise ValueError(f"数据源已缺失或变化，请创建新批次而不是重试：{source_path.name}")
            event_path = policy.get("path") if policy["mode"] == "shared" else policy.get("mapping", {}).get(_normalize_path(source_path))
            if event_path and (not Path(event_path).is_file() or _file_identity(Path(event_path)) != item.get("event_source_identity")):
                raise ValueError(f"事件文件已缺失或变化，请创建新批次而不是重试：{Path(event_path).name}")
        if policy["mode"] == "per_file":
            wanted = {_normalize_path(item["path"]) for item in selected}
            policy["mapping"] = {key: value for key, value in policy["mapping"].items() if key in wanted}
        return self._execute_batch(selected, worker, parent["parameters"], policy, None, cancel_event, progress_callback, batch_id)
