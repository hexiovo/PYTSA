from __future__ import annotations

import copy
import pickle
import zipfile
from pathlib import Path
from threading import Event

import numpy as np
import pandas as pd
import pytest

from workspace_service import WorkspaceFormatError, WorkspaceStore


def _write_csv(path: Path, values: list[float]) -> Path:
    pd.DataFrame({"time": np.arange(len(values), dtype=float), "signal": values}).to_csv(path, index=False)
    return path


def _run_snapshot(*, value: float, unit: str = "uS", fs: int = 10) -> dict:
    return {
        "kind": "analysis",
        "source": {"identity": f"source-{value}", "path": "signal.csv"},
        "parameters": {"prepare": {"fs": fs, "value_cols": ["signal"]}},
        "metrics": {"mean": {"value": value, "unit": unit}},
        "curves": {
            "signal": {
                "label": "signal",
                "x": [0.0, 0.1],
                "y": [1.0, 2.0],
                "x_unit": "s",
                "y_unit": unit,
                "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
            }
        },
        "results": {"summary": {"count": 2}},
    }


def test_project_round_trip_embeds_data_and_reports_source_state(tmp_path: Path) -> None:
    source = _write_csv(tmp_path / "input.csv", [1.0, 2.0, 3.0])
    store = WorkspaceStore(tmp_path / "workspace")
    frame = pd.DataFrame({"time": [0.0, 0.1], "signal": [1.0, np.nan]})
    state = {
        "name": "project",
        "source": {"path": str(source), "time_col": "time"},
        "raw_frame": frame,
        "params": {"prepare": {"fs": 10, "order": (1, 0, 1)}},
        "results": {"matrix": np.array([[1.0, np.nan]])},
        "annotations": [{"start_seconds": 0.2, "end_seconds": 0.5, "label": "artifact"}],
    }

    project_path = store.save_project(state)
    restored = store.load_project(project_path)

    pd.testing.assert_frame_equal(restored["raw_frame"], frame)
    assert restored["source_status"] == "unchanged"
    assert restored["params"]["prepare"]["order"] == (1, 0, 1)
    assert np.isnan(restored["results"]["matrix"][0, 1])

    _write_csv(source, [1.0, 2.0, 3.0, 4.0])
    assert store.load_project(project_path)["source_status"] == "changed"
    source.unlink()
    assert store.load_project(project_path)["source_status"] == "missing"
    assert len(store.list_recent_items()) == 0


def test_project_import_rejects_pickle_and_zip_with_extra_members(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    pickle_path = tmp_path / "unsafe.pkl"
    pickle_path.write_bytes(pickle.dumps({"code": "not loaded"}))
    with pytest.raises(WorkspaceFormatError):
        store.load_project(pickle_path)

    archive_path = tmp_path / "unsafe.pytproject"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project.json", "{}")
        archive.writestr("../outside.txt", "blocked")
    with pytest.raises(WorkspaceFormatError):
        store.load_project(archive_path)


def test_parameter_template_crud_import_export_and_compatibility(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    template = store.save_parameter_template(
        "Base settings",
        {"time_col": "time", "value_cols": ["signal", "missing"], "fs": 10, "order": (1, 0, 1)},
        kind="prepare",
    )
    assert template["parameters"]["order"] == (1, 0, 1)
    with pytest.raises(FileExistsError):
        store.save_parameter_template("base SETTINGS", {}, kind="prepare")

    copied = store.copy_parameter_template(template["id"], "Copied settings")
    exported = store.export_parameter_template(template["id"], tmp_path / "template.json")
    second_store = WorkspaceStore(tmp_path / "second-workspace")
    imported = second_store.import_parameter_template(exported)
    assert imported["name"] == "Base settings"
    assert imported["id"] != template["id"]
    assert len(store.list_parameter_templates(kind="prepare")) == 2

    compatibility = store.check_template_compatibility(
        template,
        {"columns": ["time", "signal"], "sampling_rate": 5},
    )
    assert not compatibility["compatible"]
    assert {issue["field"] for issue in compatibility["issues"]} == {"parameters.value_cols", "parameters.fs"}
    store.delete_parameter_template(copied["id"])


def test_report_template_storage_uses_shared_canonical_schema(tmp_path: Path) -> None:
    from report_export import DEFAULT_REPORT_TEMPLATE

    store = WorkspaceStore(tmp_path / "workspace")
    template = copy.deepcopy(DEFAULT_REPORT_TEMPLATE)
    template["name"] = "Short report"
    template["sections"] = ["summary", "warnings"]
    template["formats"] = ["json", "html"]
    saved = store.save_report_template(template)
    assert saved["sections"] == ["summary", "warnings"]
    assert store.get_report_template("Short report") == saved
    duplicate = store.copy_report_template("Short report", "Short report copy")
    assert duplicate["name"] == "Short report copy"
    exported = store.export_report_template("Short report", tmp_path / "report-template.json")
    imported = WorkspaceStore(tmp_path / "other").import_report_template(exported)
    assert imported["name"] == "Short report"
    with pytest.raises((ValueError, WorkspaceFormatError)):
        store.save_report_template({**template, "formats": []}, overwrite=True)


def test_recent_items_persist_deduplicate_and_show_missing_paths(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = WorkspaceStore(root)
    data_path = tmp_path / "gone.csv"
    store.add_recent_item("data", data_path, label="gone")
    store.add_recent_item("data", data_path, label="gone again")

    recent = WorkspaceStore(root).list_recent_items(kind="data")
    assert len(recent) == 1
    assert recent[0]["label"] == "gone again"
    assert recent[0]["exists"] is False
    store.remove_recent_item("data", data_path)
    assert store.list_recent_items() == []


def test_preflight_reports_conditions_without_mutating_frame(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    frame = pd.DataFrame({
        "time": [0.0, 0.1, 0.1, 0.4, 0.5],
        "signal": [1.0, np.nan, 2.0, 3.0, 100.0],
        "constant": [5.0, 5.0, 5.0, 5.0, 5.0],
    })
    original = frame.copy(deep=True)

    findings = store.preflight_dataframe(frame, time_col="time", value_cols=["signal", "constant"], sampling_rate=10)
    checks = {finding["check"] for finding in findings}

    assert {"missing_values", "duplicate_time", "irregular_sampling", "constant_column", "outlier_range"} <= checks
    assert all({"severity", "message", "column", "count", "row_indices", "row_range", "time_range"} <= set(row) for row in findings)
    assert all(len(row["row_indices"]) <= 20 for row in findings)
    pd.testing.assert_frame_equal(frame, original)

    invalid_time = pd.DataFrame({"time": ["0", "bad"], "signal": [1.0, 2.0]})
    invalid = store.preflight_dataframe(invalid_time, time_col="time", time_kind="numeric", value_cols=["signal"])
    assert any(row["check"] == "invalid_time" and row["row_indices"] == [1] for row in invalid)


def test_run_history_is_immutable_and_compares_only_compatible_curves(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    left = store.record_run(_run_snapshot(value=1.0, fs=10))
    right_snapshot = _run_snapshot(value=1.5, fs=20)
    right_snapshot["curves"]["signal"]["x"] = [0.0, 0.1, 0.2]
    right_snapshot["curves"]["signal"]["y"] = [1.0, 2.0, 3.0]
    right = store.record_run(right_snapshot)

    comparison = store.compare_runs(left, right)
    assert comparison["metrics"][0]["delta"] == pytest.approx(0.5)
    assert comparison["parameters"][0]["changed"] is True
    assert comparison["curves"][0]["compatible"] is True
    assert len(store.get_run_snapshot(left)["curves"]["signal"]["x"]) == 2
    assert [item["run_id"] for item in store.list_run_history()] == [right, left]

    different_units = _run_snapshot(value=2.0, unit="mS")
    third = store.record_run(different_units)
    incompatible = store.compare_runs(left, third)
    assert incompatible["metrics"][0]["comparable"] is False
    assert incompatible["curves"][0]["compatible"] is False


def test_persistent_logs_rotate_and_support_cross_run_lookup(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    store.LOG_MAX_BYTES = 300
    run_id = "test-run"
    for index in range(8):
        store.write_run_log(run_id, "INFO", f"entry {index}", details={"index": index})
    try:
        raise RuntimeError("full trace detail")
    except RuntimeError as exc:
        store.write_run_log(run_id, "ERROR", "analysis failed", exception=exc)

    assert store.list_log_runs()[0]["run_id"] == run_id
    rows = store.read_run_logs(run_id)
    assert rows[0]["message"] == "analysis failed"
    assert "RuntimeError: full trace detail" in store.format_run_logs(run_id)
    assert store.read_run_logs(None, limit=1)[0]["run_id"] == run_id
    assert len(list((tmp_path / "workspace" / "logs").glob("test-run*.jsonl"))) <= store.LOG_RETAIN_FILES


def test_batch_isolates_failures_retries_by_batch_id_and_reuses_parameters(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    good = _write_csv(tmp_path / "good.csv", [1.0, 2.0, 3.0])
    transient = _write_csv(tmp_path / "transient.csv", [4.0, 5.0, 6.0])
    failed_once: set[str] = set()
    calls: list[tuple[str, dict]] = []
    progress: list[dict] = []

    def worker(item, raw_frame, event_frame, frozen_parameters, cancel_event, stage_callback):
        calls.append((item["display_name"], frozen_parameters))
        assert event_frame is None
        assert Path(item["output_dir"]).is_dir()
        stage_callback({"stage": "analysis", "message": "computed"})
        if item["display_name"] == "transient.csv" and item["display_name"] not in failed_once:
            failed_once.add(item["display_name"])
            raise RuntimeError("one-time error")
        mean = float(raw_frame["signal"].mean())
        return {"kind": "analysis", "metrics": {"mean": {"value": mean, "unit": "uS"}},
                "curves": {"signal": {"label": "signal", "x": [0.0], "y": [mean], "x_unit": "s", "y_unit": "uS",
                                        "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"}}},
                "results": {"mean": mean}}

    parameters = {"prepare": {"value_cols": ["signal"]}, "batch_tag": "frozen"}
    batch = store.run_batch([good, transient], worker, parameters=parameters, event_policy={"mode": "none"}, progress_callback=progress.append)
    assert [row["status"] for row in batch["results"]] == ["success", "failed"]
    assert Path(batch["summary_csv"]).is_file()
    assert len(store.list_run_history()) == 1
    assert any(event["stage"] == "analysis" for event in progress)

    retry = store.retry_batch(batch["batch_id"], worker)
    assert retry["parent_batch_id"] == batch["batch_id"]
    assert len(retry["results"]) == 1
    assert retry["results"][0]["path"] == str(transient.resolve())
    assert retry["results"][0]["status"] == "success"
    assert all(params == parameters for _, params in calls)
    assert len(store.list_run_history()) == 2


def test_batch_progress_counts_only_processed_terminal_items(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    paths = [
        _write_csv(tmp_path / "good.csv", [1.0, 2.0]),
        _write_csv(tmp_path / "bad.csv", [3.0, 4.0]),
        _write_csv(tmp_path / "cancel.csv", [5.0, 6.0]),
        _write_csv(tmp_path / "not-run.csv", [7.0, 8.0]),
    ]
    cancellation = Event()
    progress: list[dict] = []

    def worker(item, raw_frame, event_frame, frozen_parameters, cancel_event, stage_callback):
        if item["display_name"] == "bad.csv":
            raise RuntimeError("expected item failure")
        if item["display_name"] == "cancel.csv":
            cancel_event.set()
            return {"cancelled": True}
        return {"kind": "analysis", "results": {"rows": len(raw_frame)}}

    batch = store.run_batch(paths, worker, parameters={}, event_policy={"mode": "none"},
                            cancel_event=cancellation, progress_callback=progress.append)

    assert [item["status"] for item in batch["results"]] == ["success", "failed", "cancelled", "not_run"]
    terminal_events = [event for event in progress if event["stage"] == "complete"]
    assert [(event["status"], event["completed"], event["total"]) for event in terminal_events] == [
        ("success", 1, 4), ("failed", 2, 4), ("cancelled", 3, 4),
    ]
    assert [event["completed"] for event in progress] == sorted(event["completed"] for event in progress)


def test_batch_cancellation_before_start_marks_files_not_run(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    data_path = _write_csv(tmp_path / "data.csv", [1.0, 2.0])
    cancellation = Event()
    cancellation.set()
    calls = []

    def worker(*args):
        calls.append(args)
        return {"kind": "analysis", "results": {}}

    result = store.run_batch([data_path], worker, parameters={}, event_policy={"mode": "none"}, cancel_event=cancellation)
    assert result["cancelled"] is True
    assert result["results"][0]["status"] == "not_run"
    assert calls == []
    assert Path(result["summary_csv"]).is_file()


def test_per_file_event_mapping_must_cover_every_input(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace")
    data = _write_csv(tmp_path / "data.csv", [1.0, 2.0])
    event = _write_csv(tmp_path / "events.csv", [0.0, 0.0])

    with pytest.raises(ValueError, match="必须明确包含"):
        store.run_batch([data], lambda *args: {}, parameters={},
                        event_policy={"mode": "per_file", "mapping": {} })
    assert not (tmp_path / "workspace" / "history" / "batches").exists()

