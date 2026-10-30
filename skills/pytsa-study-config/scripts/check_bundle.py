#!/usr/bin/env python3
"""Strictly check the study-config JSON envelope; never grants READY."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOP_KEYS = {
    "schema", "study_id", "status", "software", "design", "decision_ledger",
    "input_data", "native_template", "conversion", "statistics", "operator_card",
    "data_dictionary", "preparation_checklist", "evidence_manifest", "validation",
}
VALID_STATUS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_APPLICABLE"}
STAGES = [
    "template_shell", "native_fields", "native_execution", "conversion_execution",
    "statistical_design", "statistical_execution", "end_to_end",
]

class BundleError(ValueError):
    pass

def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise BundleError(f"duplicate JSON key: {key}")
        output[key] = value
    return output

def reject_constant(value: str) -> None:
    raise BundleError(f"invalid JSON number: {value}")

def require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{name} must be an object")
    return value

def require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise BundleError(f"{name} must be an array")
    return value

def require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BundleError(f"{name} must be nonempty text")
    return value

def load_strict(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream, object_pairs_hook=no_duplicate_keys, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise BundleError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    return require_object(data, "root")

def inspect_bundle(data: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(TOP_KEYS - set(data))
    unknown = sorted(set(data) - TOP_KEYS)
    if missing or unknown:
        raise BundleError(f"top-level keys mismatch; missing={missing}, unknown={unknown}")
    if data["schema"] != "pytsa-study-config/1":
        raise BundleError("unsupported envelope schema")
    if data["status"] not in {"DRAFT", "PENDING", "READY"}:
        raise BundleError("status must be DRAFT, PENDING or READY")
    require_text(data["study_id"], "study_id")
    require_object(data["software"], "software")
    design = require_object(data["design"], "design")
    ledger = require_list(data["decision_ledger"], "decision_ledger")
    inputs = require_object(data["input_data"], "input_data")
    native = require_object(data["native_template"], "native_template")
    conversion = require_object(data["conversion"], "conversion")
    statistics = require_object(data["statistics"], "statistics")
    operator = require_object(data["operator_card"], "operator_card")
    require_list(data["data_dictionary"], "data_dictionary")
    require_list(data["preparation_checklist"], "preparation_checklist")
    evidence = require_object(data["evidence_manifest"], "evidence_manifest")
    validation = require_object(data["validation"], "validation")
    for i, row in enumerate(ledger):
        item = require_object(row, f"decision_ledger[{i}]")
        for key in ("parameter_path", "unit", "source_status", "source", "rationale", "branch"):
            require_text(item.get(key), f"decision_ledger[{i}].{key}")
        if item["source_status"] not in {"KNOWN", "RECOMMENDED", "MISSING"}:
            raise BundleError(f"decision_ledger[{i}].source_status is invalid")
        require_object(item.get("validation"), f"decision_ledger[{i}].validation")

    shell_status = "PASS"
    shell_note = "Only the outer template envelope is checked, not nested parameter acceptance or execution."
    if (native.get("schema") != "pyt-workflow-template"
            or native.get("schema_version") != 1
            or native.get("kind") not in {"prepare", "analysis", "forecast", "eda", "combined"}
            or not isinstance(native.get("parameters"), dict)):
        shell_status = "FAIL"
    require_text(conversion.get("status"), "conversion.status")
    require_list(conversion.get("steps"), "conversion.steps")
    require_list(conversion.get("scripts"), "conversion.scripts")
    stats_keys = (
        "status", "outcome", "independent_unit", "model", "contrasts", "effect_size",
        "confidence_interval", "multiplicity", "missing_policy", "assumptions", "diagnostics", "failure_plan",
    )
    absent_stats = [key for key in stats_keys if key not in statistics]
    if absent_stats:
        raise BundleError(f"statistics fields missing: {absent_stats}")
    require_list(operator.get("steps"), "operator_card.steps")
    require_list(evidence.get("items"), "evidence_manifest.items")

    stages: dict[str, Any] = {}
    for name in STAGES:
        record = require_object(validation.get(name), f"validation.{name}")
        status = record.get("status")
        if status not in VALID_STATUS:
            raise BundleError(f"validation.{name}.status is invalid")
        refs = require_list(record.get("evidence"), f"validation.{name}.evidence")
        if status == "PASS" and not refs:
            raise BundleError(f"validation.{name} is PASS without an evidence reference")
        if status == "NOT_APPLICABLE":
            require_text(record.get("reason"), f"validation.{name}.reason")
        stages[name] = {"status": status, "evidence_references": len(refs)}

    missing_fields = []
    if not (design.get("status") == "KNOWN" and design.get("question") and design.get("independent_unit")):
        missing_fields.append("design.status/question/independent_unit")
    if not (inputs.get("status") == "KNOWN" and inputs.get("files")):
        missing_fields.append("input_data.status/files")
    return {
        "report_kind": "STRUCTURE_AND_FIELD_PRESENCE_ONLY",
        "strict_json_parse": "PASS",
        "top_level_schema": "PASS",
        "template_shell": {"status": shell_status, "note": shell_note},
        "field_presence_review": {"status": "PASS" if not missing_fields else "PENDING", "missing": missing_fields},
        "validation_stages_as_recorded": stages,
        "claimed_bundle_status": data["status"],
        "readiness_authorized": False,
        "limits": [
            "This script never grants READY.",
            "Template-shell PASS is not a complete PYTSA schema or parameter check.",
            "Evidence references are not opened or independently verified.",
            "Field presence does not establish sound statistical design or successful execution.",
            "Native execution, conversion execution, statistical design review, statistical execution and end-to-end validation remain separate."
        ],
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        report = inspect_bundle(load_strict(args.bundle))
    except (OSError, BundleError, TypeError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
