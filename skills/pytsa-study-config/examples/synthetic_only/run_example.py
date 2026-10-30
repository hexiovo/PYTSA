#!/usr/bin/env python3
"""SYNTHETIC_ONLY: narrow conversion, native PYTSA EDA and paired-statistics example."""
from __future__ import annotations
import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t, ttest_rel

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("synthetic-output"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "core.py").is_file() or not (root / "experimental.py").is_file():
        parser.error("Set --project-root to the PYTSA source directory containing core.py and experimental.py")
    sys.path.insert(0, str(root))
    import core
    import experimental

    rng = np.random.default_rng(20261031)
    fs = 10.0
    time_s = np.arange(0, 40, 1 / fs)
    signal_us = 5.0 + 0.04 * np.sin(2 * np.pi * 0.08 * time_s) + rng.normal(0, 0.015, len(time_s))
    for onset, amplitude in ((20.8, 1.0), (21.7, 0.45)):
        elapsed = time_s - onset
        valid = elapsed >= 0
        signal_us[valid] += amplitude * (np.exp(-elapsed[valid] / 2.2) - np.exp(-elapsed[valid] / 0.65))
    eda = pd.DataFrame({"time": time_s, "eda_uS": signal_us})
    eda.attrs["status"] = "SYNTHETIC_ONLY"
    prepared = core.prepare_data(
        eda,
        {
            "value_cols": ["eda_uS"],
            "time_col": "time",
            "time_kind": "numeric",
            "time_scale": "s",
            "fs": fs,
            "missing": "reject",
            "duplicate": "reject",
            "transform": "none",
            "filter_type": "none",
            "synthetic_only": True,
        },
    )
    if prepared["meta"]["status"] != "SYNTHETIC_ONLY" or not math.isclose(prepared["fs"], fs):
        raise AssertionError("PYTSA preparation did not preserve expected synthetic status/fs")

    marker_frame = pd.DataFrame({"time_ms": time_s * 1000, "marker": ["" for _ in time_s]})
    marker_frame.loc[np.argmin(np.abs(time_s - 20.0)), "marker"] = "stimulus_A"
    events = experimental.markers_to_events(
        marker_frame, marker_col="marker", time_col="time_ms", fs=fs,
        time_kind="numeric", time_scale="ms",
    )
    if len(events) != 1 or not math.isclose(float(events.iloc[0]["time"]), 20.0, abs_tol=1e-9):
        raise AssertionError(f"ms-to-s event conversion mismatch: {events.to_dict(orient='records')}")

    eda_result = experimental.analyze_eda(
        prepared,
        "eda_uS",
        {
            "unit": "uS",
            "method": "highpass",
            "clean": "none",
            "cutoff": 0.05,
            "relative_threshold": 0.1,
            "absolute_threshold": 0.01,
            "window_seconds": 10.0,
            "window_step_seconds": 5.0,
            "baseline_correction": "subtract_mean",
            "start_seconds": 0.0,
            "end_seconds": None,
            "baseline_start": -2.0,
            "baseline_end": 0.0,
            "response_start": 1.0,
            "response_end": 5.0,
        },
        event_df=events,
    )
    event_rows = eda_result["tables"]["eda_events"]
    if len(event_rows) != 1 or event_rows.iloc[0]["status"] != "OK":
        raise AssertionError(f"PYTSA EDA did not return one valid event: {event_rows.to_dict(orient='records')}")

    ids = ["001", "002", "003", "004", "005"]
    pre = [10.0, 12.0, 9.0, 14.0, 11.0]
    post = [11.0, 14.0, 12.0, 18.0, 16.0]
    rows = []
    for subject, before, after in zip(ids, pre, post):
        rows.extend([
            {"subject_id": subject, "condition": "pre", "score": before},
            {"subject_id": subject, "condition": "post", "score": after},
        ])
    paired = pd.DataFrame(rows)
    paired["subject_id"] = paired["subject_id"].astype("string")
    if paired.duplicated(["subject_id", "condition"]).any():
        raise AssertionError("More than one row per subject-condition; paired input is not unique")
    wide = paired.pivot(index="subject_id", columns="condition", values="score").sort_index()
    if wide[["pre", "post"]].isna().any().any() or wide.index.nunique() != 5:
        raise AssertionError("Incomplete or duplicated paired subject set")
    test = ttest_rel(wide["post"], wide["pre"], alternative="two-sided")
    differences = [after - before for before, after in zip(pre, post)]
    mean_difference = statistics.mean(differences)
    sd_difference = statistics.stdev(differences)
    se_difference = sd_difference / math.sqrt(len(differences))
    hand_t = mean_difference / se_difference
    hand_df = len(differences) - 1
    hand_dz = mean_difference / sd_difference
    hand_p = float(2 * t.sf(abs(hand_t), hand_df))
    expected = {
        "n_pairs": 5,
        "mean_difference": 3.0,
        "sample_sd_difference": math.sqrt(2.5),
        "standard_error": math.sqrt(0.5),
        "t": math.sqrt(18.0),
        "df": 4,
        "cohens_dz": 3.0 / math.sqrt(2.5),
    }
    observed = {
        "n_pairs": int(len(wide)),
        "mean_difference": mean_difference,
        "sample_sd_difference": sd_difference,
        "standard_error": se_difference,
        "t": float(test.statistic),
        "df": int(test.df),
        "p_two_sided": float(test.pvalue),
        "cohens_dz": hand_dz,
        "manual_t": hand_t,
        "manual_p_two_sided": hand_p,
    }
    for key in ("mean_difference", "sample_sd_difference", "standard_error", "t", "cohens_dz"):
        if not math.isclose(observed[key], expected[key], rel_tol=1e-10, abs_tol=1e-10):
            raise AssertionError(f"Manual paired-statistics baseline mismatch for {key}")
    if observed["n_pairs"] != expected["n_pairs"] or observed["df"] != expected["df"]:
        raise AssertionError("Sample count/df mismatch: rows were treated as independent or paired incorrectly")
    if not math.isclose(observed["t"], observed["manual_t"], rel_tol=1e-12):
        raise AssertionError("ttest_rel does not match the separately calculated paired t statistic")
    if not math.isclose(observed["p_two_sided"], observed["manual_p_two_sided"], rel_tol=1e-12):
        raise AssertionError("paired p does not match the t-distribution reference calculation")

    result = {
        "status": "SYNTHETIC_ONLY",
        "pytsa": {
            "prepared_fs_hz": float(prepared["fs"]),
            "prepared_source_status": prepared["meta"]["status"],
            "eda_event_time_seconds": float(events.iloc[0]["time"]),
            "eda_event_status": str(event_rows.iloc[0]["status"]),
            "eda_unit": "uS (explicit synthetic fixture unit)",
            "entrypoints_called": [
                "core.prepare_data(df, config)",
                "experimental.markers_to_events(... time_scale='ms')",
                "experimental.analyze_eda(...)",
            ],
        },
        "paired_statistics": {
            "independent_unit": "subject_id",
            "id_type": "string; leading zeros retained",
            "condition_rows": int(len(paired)),
            "n_independent_pairs": observed["n_pairs"],
            "pre_values": pre,
            "post_values": post,
            "differences": differences,
            "scipy_ttest_rel": observed,
            "independent_manual_baseline": expected,
            "interpretation": "Engineering fixture only; do not infer a real study effect.",
        },
        "limits": [
            "One fs/time/unit/event path only; not a complete PYTSA schema or method suite.",
            "No real experimental design, device calibration or user data were supplied.",
            "This example does not establish model assumptions for any real study.",
        ],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "example-results.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS_SYNTHETIC_ONLY", "output": str(target), "result": result}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

