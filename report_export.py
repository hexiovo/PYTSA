"""Selective report export for PYTSA.

The scientific calculations remain in :mod:`core`; this module applies a
versioned report template to the already computed results and publishes only
the selected output formats.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import core


SECTION_ORDER = (
    "summary",
    "tests",
    "matrices",
    "forecast",
    "eda",
    "warnings",
    "methods",
    "annotations",
)
FORMAT_ORDER = ("csv", "xlsx", "json", "png", "html")
_TABLE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_XLSX_DATA_ROW_LIMIT = 1_048_575

DEFAULT_REPORT_TEMPLATE: dict[str, Any] = {
    "version": 1,
    "name": "默认报告",
    "title": "PYTSA 时间序列分析报告",
    "experiment": {},
    "sections": list(SECTION_ORDER),
    "formats": list(FORMAT_ORDER),
    "figure_titles": {},
    "figures": "all",
}


class ExportCancelledError(RuntimeError):
    """Raised when a cooperative report export is cancelled at a stage boundary."""


def _json_clone(value: Any, field: str) -> Any:
    """Return a strict JSON-compatible deep copy of a template field."""
    def validate(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not (item == item and abs(item) != float("inf")):
                raise ValueError(f"{path} 包含非有限数字。")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                validate(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError(f"{path} 的对象键必须是文本。")
                validate(child, f"{path}.{key}")
            return
        raise ValueError(f"{path} 包含非 JSON 类型。")

    try:
        validate(value, field)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"报告模板的 {field} 必须只包含有效 JSON 值。") from exc


def normalize_report_template(template: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and normalize a version-1 report template.

    The canonical form uses ordered string arrays for ``sections`` and
    ``formats``. For UI convenience, ``sections`` also accepts a boolean map.
    Missing fields use the documented default template values; unknown fields
    and enum values are rejected at this input boundary.
    """
    if template is None:
        source: Mapping[str, Any] = DEFAULT_REPORT_TEMPLATE
    elif isinstance(template, Mapping):
        source = template
    else:
        raise TypeError("报告模板必须是 JSON 对象。")

    allowed = {
        "version", "name", "title", "experiment", "sections", "formats",
        "figure_titles", "figures",
    }
    if any(not isinstance(key, str) for key in source):
        raise ValueError("报告模板字段名称必须是文本。")
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise ValueError(f"报告模板包含不支持的字段：{unknown!r}。")

    version = source.get("version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("报告模板 version 目前只支持整数 1。")

    name = source.get("name", DEFAULT_REPORT_TEMPLATE["name"])
    title = source.get("title", DEFAULT_REPORT_TEMPLATE["title"])
    if not isinstance(name, str) or not name.strip():
        raise ValueError("报告模板 name 必须是非空文本。")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("报告模板 title 必须是非空文本。")

    experiment = source.get("experiment", {})
    if not isinstance(experiment, Mapping):
        raise ValueError("报告模板 experiment 必须是对象。")
    experiment = _json_clone(dict(experiment), "experiment")

    raw_sections = source.get("sections", list(SECTION_ORDER))
    if isinstance(raw_sections, Mapping):
        if any(not isinstance(key, str) for key in raw_sections):
            raise ValueError("报告模板 sections 的章节名称必须是文本。")
        section_unknown = sorted(set(raw_sections) - set(SECTION_ORDER))
        if section_unknown:
            raise ValueError(f"报告模板 sections 包含未知章节：{section_unknown!r}。")
        if any(type(value) is not bool for value in raw_sections.values()):
            raise ValueError("报告模板 sections 布尔映射的值必须是 true 或 false。")
        selected_sections = {key for key, enabled in raw_sections.items() if enabled}
    elif isinstance(raw_sections, (list, tuple)):
        if any(not isinstance(section, str) for section in raw_sections):
            raise ValueError("报告模板 sections 必须是章节名称列表。")
        section_unknown = sorted(set(raw_sections) - set(SECTION_ORDER))
        if section_unknown:
            raise ValueError(f"报告模板 sections 包含未知章节：{section_unknown!r}。")
        selected_sections = set(raw_sections)
    else:
        raise ValueError("报告模板 sections 必须是章节名称列表或布尔映射。")
    sections = [section for section in SECTION_ORDER if section in selected_sections]

    raw_formats = source.get("formats", list(FORMAT_ORDER))
    if not isinstance(raw_formats, (list, tuple)) or any(not isinstance(item, str) for item in raw_formats):
        raise ValueError("报告模板 formats 必须是 CSV/XLSX/JSON/PNG/HTML 格式列表。")
    unknown_formats = sorted(set(raw_formats) - set(FORMAT_ORDER))
    if unknown_formats:
        raise ValueError(f"报告模板 formats 包含不支持的格式：{unknown_formats!r}。")
    selected_formats = set(raw_formats)
    if not selected_formats:
        raise ValueError("至少选择一种报告导出格式。")
    formats = [format_name for format_name in FORMAT_ORDER if format_name in selected_formats]

    raw_titles = source.get("figure_titles", {})
    if not isinstance(raw_titles, Mapping):
        raise ValueError("报告模板 figure_titles 必须是图表键到标题的对象。")
    figure_titles: dict[str, str] = {}
    for key, figure_title in raw_titles.items():
        if not isinstance(key, str) or not key:
            raise ValueError("报告模板 figure_titles 的图表键必须是非空文本。")
        if not isinstance(figure_title, str) or not figure_title.strip():
            raise ValueError(f"图表 {key!r} 的标题必须是非空文本。")
        figure_titles[key] = figure_title.strip()

    raw_figures = source.get("figures", "all")
    if raw_figures == "all":
        figures: str | list[str] = "all"
    elif isinstance(raw_figures, (list, tuple)):
        if any(not isinstance(key, str) or not key for key in raw_figures):
            raise ValueError("报告模板 figures 必须是图表键列表或字符串 'all'。")
        figures = list(dict.fromkeys(raw_figures))
    else:
        raise ValueError("报告模板 figures 必须是图表键列表或字符串 'all'。")

    return {
        "version": 1,
        "name": name.strip(),
        "title": title.strip(),
        "experiment": experiment,
        "sections": sections,
        "formats": formats,
        "figure_titles": figure_titles,
        "figures": figures,
    }


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel():
        raise ExportCancelledError("报告导出已取消。")


def _notify(progress, message: str) -> None:
    if progress is not None:
        progress(message)


def _figure_section(key: str) -> str:
    if key.startswith("matrix_"):
        return "matrices"
    if key == "forecast":
        return "forecast"
    if key == "eda_components" or key.startswith("eda_"):
        return "eda"
    return "summary"


def _select_figures(figures: Mapping[str, Any] | None, template: dict[str, Any]) -> dict[str, Any]:
    if figures is None:
        available: dict[str, Any] = {}
    elif isinstance(figures, Mapping):
        available = dict(figures)
    else:
        raise TypeError("figures 必须是图表键到 Matplotlib 图对象的映射。")

    if any(not isinstance(key, str) or not key for key in available):
        raise ValueError("图表键必须是非空文本。")
    requested = template["figures"]
    if requested == "all":
        keys = list(available)
    else:
        missing = [key for key in requested if key not in available]
        if missing:
            raise ValueError(f"所选图表在当前结果中不存在：{missing!r}。")
        keys = list(requested)

    active_sections = set(template["sections"])
    selected = {key: available[key] for key in keys if _figure_section(key) in active_sections}
    for figure in selected.values():
        if not callable(getattr(figure, "savefig", None)):
            raise TypeError("所有选中图表都必须支持 savefig()。")
    return selected


def _safe_table_key(key: Any, kind: str) -> str:
    if not isinstance(key, str) or not _TABLE_KEY_RE.fullmatch(key):
        raise ValueError(f"{kind}表格键只支持英文字母、数字、下划线和连字符：{key!r}。")
    return key


def _validate_export_inputs(prepared, analysis, forecast_result, figures) -> None:
    if not isinstance(prepared, Mapping) or "data" not in prepared or "original" not in prepared:
        raise TypeError("prepared 必须是 core.prepare_data() 返回的结果。")
    if not isinstance(analysis, Mapping):
        raise TypeError("analysis 必须是 core.analyze() 返回的结果。")
    if forecast_result is not None and not isinstance(forecast_result, Mapping):
        raise TypeError("forecast_result 必须是 core.forecast() 返回的结果或 None。")
    tables = analysis.get("tables", {})
    matrices = analysis.get("matrices", {})
    if not isinstance(tables, Mapping) or not isinstance(matrices, Mapping):
        raise TypeError("analysis.tables 和 analysis.matrices 必须是映射。")
    for key in tables:
        _safe_table_key(key, "analysis")
    for key in matrices:
        _safe_table_key(key, "matrix")
    if not isinstance(prepared["data"], pd.DataFrame) or not isinstance(prepared["original"], pd.DataFrame):
        raise TypeError("prepared.data 和 prepared.original 必须是 pandas DataFrame。")
    if any(not isinstance(frame, pd.DataFrame) for frame in tables.values()):
        raise TypeError("analysis.tables 中的表格必须是 pandas DataFrame。")
    if any(not isinstance(frame, pd.DataFrame) for frame in matrices.values()):
        raise TypeError("analysis.matrices 中的矩阵必须是 pandas DataFrame。")
    if forecast_result is not None:
        for key in ("metrics", "backtest", "future", "diagnostics", "rolling_metrics", "rolling_predictions"):
            if key in forecast_result and not isinstance(forecast_result[key], pd.DataFrame):
                raise TypeError(f"forecast_result.{key} 必须是 pandas DataFrame。")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_text(value: Any) -> str:
    return json.dumps(core._json_safe(value), ensure_ascii=False, indent=2, allow_nan=False)


def _figure_records(selected_figures: Mapping[str, Any], template: dict[str, Any], generated_dir: Path,
                    generated_metadata: list[dict[str, Any]], formats: set[str], html_selected: bool,
                    stage: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    records: list[dict[str, Any]] = []
    image_data: dict[str, str] = {}
    stage_figures = stage / "figures"
    if "png" in formats and selected_figures:
        stage_figures.mkdir(parents=True, exist_ok=True)

    for index, key in enumerate(selected_figures, start=1):
        title = template["figure_titles"].get(key, key)
        relative_path = f"figures/figure_{index:02d}.png" if "png" in formats else None
        if index > len(generated_metadata):
            raise RuntimeError("核心导出未返回所选图表的 PNG 文件。")
        if str(generated_metadata[index - 1].get("label")) != key:
            raise RuntimeError("核心导出的图表顺序与所选图表不一致。")
        generated_relative = Path(generated_metadata[index - 1]["path"])
        source = generated_dir / generated_relative
        if not source.is_file():
            raise RuntimeError(f"核心导出的临时图表文件不存在：{source}")
        image_bytes = source.read_bytes()
        if "png" in formats:
            target = stage / Path(relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if html_selected:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            image_data[key] = f"data:image/png;base64,{encoded}"
        records.append({
            "key": key,
            "title": title,
            "path": relative_path,
            "embedded_in_html": bool(html_selected),
        })
    return records, image_data


def _section_for_table(key: str) -> str:
    if key == "tests":
        return "tests"
    if key.startswith("eda_") or key == "outliers":
        return "eda"
    return "summary"


def _csv_name(section: str, key: str) -> str:
    if section == "matrices":
        return f"matrix_{key}.csv"
    if section == "forecast":
        return f"forecast_{key}.csv"
    return f"analysis_{key}.csv"


def _append_table(parts: list[str], title: str, frame: pd.DataFrame, *, section: str, key: str,
                  formats: set[str], index: bool = False) -> None:
    if frame is None:
        return
    frame_csv = _csv_name(section, key)
    shown = frame.head(200) if len(frame) > 200 else frame
    parts.append(f"<h3>{_esc(title)}</h3>")
    if len(frame) > 200:
        if "csv" in formats:
            detail = f'<a href="{_esc(frame_csv)}">完整 CSV 表格</a>。'
        elif "xlsx" in formats and len(frame) <= _XLSX_DATA_ROW_LIMIT:
            detail = '<a href="PYTSA_results.xlsx">完整表格位于 Excel 工作簿</a>。'
        elif "xlsx" in formats:
            detail = "表格超过 Excel 行数上限，当前导出未包含完整表格。"
        else:
            detail = "当前没有选择 CSV 或 XLSX，完整表格未导出。"
        parts.append(f"<p>此处显示前 200 行，共 {len(frame)} 行；{detail}</p>")
    parts.append(shown.to_html(index=index, escape=True, border=0))


def _append_figures(parts: list[str], keys: list[str], figure_records: Mapping[str, dict[str, Any]],
                    image_data: Mapping[str, str]) -> None:
    for key in keys:
        record = figure_records.get(key)
        source = image_data.get(key)
        if record is None or source is None:
            continue
        title = record["title"]
        parts.append(
            f'<figure><img class="figure" src="{_esc(source)}" alt="{_esc(title)}">'
            f"<figcaption>{_esc(title)}</figcaption></figure>"
        )


def _build_html(template: dict[str, Any], prepared, analysis, forecast_result, payload: dict[str, Any],
                figure_records: list[dict[str, Any]], image_data: Mapping[str, str], formats: set[str]) -> str:
    sections = set(template["sections"])
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_esc(template['title'])}</title>",
        "<style>body{font:15px/1.6 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#19232d}"
        "h1,h2,h3{color:#153d5b}table{border-collapse:collapse;margin:1rem 0;display:block;overflow-x:auto}"
        "th,td{border:1px solid #cbd5df;padding:.4rem .6rem;text-align:left;white-space:nowrap}"
        "th{background:#edf3f7}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7f9;padding:.8rem}"
        ".warning{background:#fff7e6;padding:.5rem 1rem;margin:.4rem 0}figure{margin:1.2rem 0}.figure{max-width:100%;height:auto}"
        "</style></head><body>",
        f"<h1>{_esc(template['title'])}</h1>",
        f"<p>数据状态：<strong>{_esc(payload.get('status', 'REAL_OR_USER_PROVIDED'))}</strong>；"
        f"采样率：{_esc(prepared.get('fs'))} Hz；时间单位：{_esc(prepared.get('time_unit', ''))}。</p>",
    ]

    downloads: list[tuple[str, str]] = []
    if "csv" in formats:
        downloads.extend([
            ("prepared_data.csv", "处理后数据 CSV"),
            ("prepared_original.csv", "原始测量值分支 CSV"),
        ])
    if "xlsx" in formats:
        downloads.append(("PYTSA_results.xlsx", "Excel 工作簿"))
    if downloads:
        parts.append("<p>数据文件：" + "；".join(
            f'<a href="{_esc(filename)}">{_esc(label)}</a>' for filename, label in downloads
        ) + "。</p>")

    if "summary" in sections:
        parts.append("<section><h2>摘要</h2>")
        experiment = template["experiment"]
        if experiment:
            parts.append("<h3>实验信息</h3><pre>" + _esc(_json_text(experiment)) + "</pre>")
        for key, frame in analysis.get("tables", {}).items():
            if frame is not None and _section_for_table(key) == "summary":
                _append_table(parts, key, frame, section="summary", key=key, formats=formats)
        _append_figures(parts, [key for key in figure_records if _figure_section(key) == "summary"],
                        figure_records, image_data)
        parts.append("</section>")

    if "tests" in sections:
        tests = analysis.get("tables", {}).get("tests")
        if tests is not None:
            parts.append("<section><h2>统计检验</h2>")
            _append_table(parts, "平稳性与残差检验", tests, section="summary", key="tests", formats=formats)
            parts.append("</section>")

    if "matrices" in sections:
        matrices = analysis.get("matrices", {})
        if matrices or any(_figure_section(key) == "matrices" for key in figure_records):
            parts.append("<section><h2>关联矩阵</h2>")
            for key, frame in matrices.items():
                if frame is not None:
                    _append_table(parts, f"{key} 矩阵", frame, section="matrices", key=key,
                                  formats=formats, index=True)
            _append_figures(parts, [key for key in figure_records if _figure_section(key) == "matrices"],
                            figure_records, image_data)
            parts.append("</section>")

    if "forecast" in sections and forecast_result is not None:
        parts.append("<section><h2>预测</h2>")
        for key in ("metrics", "backtest", "future", "diagnostics", "rolling_metrics", "rolling_predictions"):
            frame = forecast_result.get(key)
            if isinstance(frame, pd.DataFrame):
                _append_table(parts, f"预测 {key}", frame, section="forecast", key=key, formats=formats)
        _append_figures(parts, [key for key in figure_records if _figure_section(key) == "forecast"],
                        figure_records, image_data)
        parts.append("</section>")

    if "eda" in sections:
        eda_tables = [
            (key, frame) for key, frame in analysis.get("tables", {}).items()
            if frame is not None and _section_for_table(key) == "eda"
        ]
        eda_figures = [key for key in figure_records if _figure_section(key) == "eda"]
        if eda_tables or eda_figures:
            parts.append("<section><h2>探索性数据分析</h2>")
            for key, frame in eda_tables:
                _append_table(parts, key, frame, section="summary", key=key, formats=formats)
            _append_figures(parts, eda_figures, figure_records, image_data)
            parts.append("</section>")

    if "warnings" in sections and payload.get("warnings"):
        parts.append("<section><h2>警告与提示</h2>")
        parts.extend(f'<p class="warning">{_esc(item)}</p>' for item in payload["warnings"])
        parts.append("</section>")

    if "methods" in sections:
        method_metadata = {
            "prepared": prepared.get("meta", {}),
            "analysis": analysis.get("meta", {}),
            "forecast": forecast_result.get("meta", {}) if forecast_result is not None else None,
            "software_versions": payload.get("versions", {}),
            "research_notes_original": payload.get("research_notes_original"),
        }
        parts.append("<section><h2>方法与复现信息</h2><pre>" + _esc(_json_text(method_metadata)) + "</pre></section>")

    if "annotations" in sections and payload.get("annotations") is not None:
        parts.append("<section><h2>图表批注</h2><pre>" + _esc(_json_text(payload["annotations"])) + "</pre></section>")

    parts.append("</body></html>")
    return "\n".join(parts)


def _copy_core_outputs(generated_dir: Path, core_result: Mapping[str, Any], stage: Path,
                       formats: set[str]) -> list[str]:
    selected_names: list[str] = []
    for raw_path in core_result.get("files", []):
        source = Path(raw_path)
        if source.suffix.lower() == ".csv" and "csv" in formats:
            shutil.copyfile(source, stage / source.name)
            selected_names.append(source.name)
        elif source.name == "PYTSA_results.xlsx" and "xlsx" in formats:
            shutil.copyfile(source, stage / source.name)
            selected_names.append(source.name)
    return selected_names


def _publish_directory(stage: Path, parent: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"PYTSA_results_{stamp}"
    suffix = 0
    while True:
        name = base if suffix == 0 else f"{base}_{suffix:02d}"
        destination = parent / name
        try:
            os.rename(stage, destination)
            return destination
        except FileExistsError:
            suffix += 1


def export_report(folder, prepared, analysis, forecast_result=None, figures=None, research_path=None,
                  template=None, annotations=None, progress=None, cancel=None) -> dict[str, Any]:
    """Export selected report formats and sections without changing analysis results.

    ``progress`` is called with one status string at stage boundaries. ``cancel``
    may return a truthy value (for example ``threading.Event.is_set``); a truthy
    value raises :class:`ExportCancelledError`. Exceptions raised by either
    callback are propagated unchanged. Work is staged in a private temporary
    sibling directory and only atomically published when all selected outputs
    are ready.
    """
    if progress is not None and not callable(progress):
        raise TypeError("progress 必须是 progress(message) 可调用对象或 None。")
    if cancel is not None and not callable(cancel):
        raise TypeError("cancel 必须是 cancel() 可调用对象或 None。")
    normalized = normalize_report_template(template)
    selected_figures = _select_figures(figures, normalized)
    _validate_export_inputs(prepared, analysis, forecast_result, figures)
    formats = set(normalized["formats"])
    if annotations is not None:
        annotations_json = core._json_safe(annotations)
        # Ensure report annotations remain serializable in the manifest.
        json.dumps(annotations_json, ensure_ascii=False, allow_nan=False)
    else:
        annotations_json = None

    destination_parent = Path(folder).expanduser()
    destination_parent.mkdir(parents=True, exist_ok=True)
    _check_cancel(cancel)
    _notify(progress, "正在准备报告导出")

    with tempfile.TemporaryDirectory(prefix=".pytsa_report_tmp_", dir=str(destination_parent)) as temp_name:
        temp_root = Path(temp_name)
        stage = temp_root / "publish"
        stage.mkdir()
        _check_cancel(cancel)
        _notify(progress, "正在生成临时导出文件")
        _check_cancel(cancel)
        core_result = core.export_results(
            temp_root,
            prepared,
            analysis,
            forecast_result=forecast_result,
            figures=selected_figures,
            research_path=research_path,
        )
        generated_dir = Path(core_result["folder"])
        core_payload_path = generated_dir / "results.json"
        core_payload = json.loads(core_payload_path.read_text(encoding="utf-8"))
        _check_cancel(cancel)
        _notify(progress, "正在按模板整理报告内容")

        selected_names = _copy_core_outputs(generated_dir, core_result, stage, formats)
        generated_figure_metadata = core_payload.get("figures", [])
        figure_records, image_data = _figure_records(
            selected_figures, normalized, generated_dir, generated_figure_metadata,
            formats, "html" in formats, stage,
        )
        for record in figure_records:
            if record["path"]:
                selected_names.append(record["path"])
        figure_record_by_key = {record["key"]: record for record in figure_records}

        output_names = list(selected_names)
        if "json" in formats:
            output_names.append("results.json")
        if "html" in formats:
            output_names.append("report.html")

        payload = dict(core_payload)
        payload.update({
            "report_template": normalized,
            "experiment": normalized["experiment"],
            "annotations": annotations_json,
            "warnings": list(core_payload.get("warnings", [])),
            "figures": figure_records,
            "outputs": output_names,
        })
        if "json" in formats:
            (stage / "results.json").write_text(
                json.dumps(core._json_safe(payload), ensure_ascii=False, indent=2,
                           allow_nan=False, default=core._json_default),
                encoding="utf-8",
            )

        if "html" in formats:
            report_html = _build_html(
                normalized, prepared, analysis, forecast_result, payload,
                figure_record_by_key, image_data, formats,
            )
            (stage / "report.html").write_text(report_html, encoding="utf-8")

        _check_cancel(cancel)
        _notify(progress, "正在完成报告文件")
        _check_cancel(cancel)
        published = _publish_directory(stage, destination_parent)

    files = [str((published / name).resolve()) for name in output_names]
    return {
        "files": files,
        "folder": str(published.resolve()),
        "template": normalized["name"],
        "formats": list(normalized["formats"]),
        "sections": list(normalized["sections"]),
        "figures": figure_records,
        "warnings": list(core_payload.get("warnings", [])),
    }


__all__ = [
    "DEFAULT_REPORT_TEMPLATE",
    "ExportCancelledError",
    "normalize_report_template",
    "export_report",
]
