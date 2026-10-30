#!/usr/bin/env python3
"""Extract a narrow AST inventory from PYTSA source; this is not schema validation."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

TARGETS = {
    "core.py": ["prepare_data"],
    "experimental.py": ["markers_to_events", "analyze_eda"],
    "workflow_ui.py": [
        "_workflow_collect_parameters", "_collect_prepare_config",
        "_workflow_collect_eda_parameters", "_workflow_apply_parameters_to_controls",
    ],
    "workspace_service.py": ["save_parameter_template", "export_parameter_template"],
}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    result = f"{node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        result += f" -> {ast.unparse(node.returns)}"
    return result

def config_reads(node: ast.AST) -> dict[str, list[str]]:
    get_keys: set[str] = set()
    subscript_keys: set[str] = set()
    for child in ast.walk(node):
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in {"cfg", "config"}
                and child.func.attr == "get" and child.args):
            key = child.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                get_keys.add(key.value)
        if (isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name)
                and child.value.id in {"cfg", "config"}
                and isinstance(child.slice, ast.Constant)
                and isinstance(child.slice.value, str)):
            subscript_keys.add(child.slice.value)
    return {
        "cfg_get_literal_keys": sorted(get_keys),
        "cfg_subscript_literal_keys": sorted(subscript_keys),
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, help="Optional output JSON path; parent directory must exist.")
    args = parser.parse_args()
    root = args.project_root.resolve()
    readme = root / "README.md"
    if not readme.is_file():
        parser.error(f"README.md not found under {root}")
    match = re.search(r"当前版本：\*\*([^*]+)\*\*", readme.read_text(encoding="utf-8-sig"))
    result = {
        "report_kind": "AST_STATIC_DISCOVERY_ONLY",
        "project_root": str(root),
        "readme_version": match.group(1).strip() if match else None,
        "source_files": {},
        "functions": {},
        "limits": [
            "AST output is not a complete schema.",
            "Dynamic keys, helper/UI semantics, runtime behavior and scientific appropriateness are not proven.",
            "Hashes identify a file version only; they do not prove correctness or execution."
        ],
    }
    for filename, names in TARGETS.items():
        path = root / filename
        if not path.is_file():
            result["source_files"][filename] = {"status": "MISSING"}
            continue
        result["source_files"][filename] = {"status": "READ", "sha256": sha256(path)}
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                result["functions"][f"{filename}:{node.name}"] = {
                    "signature": signature(node),
                    **config_reads(node),
                    "line": node.lineno,
                }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
