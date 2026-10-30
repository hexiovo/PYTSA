from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest
from scipy import signal

import analysis_options as options
import core
import experimental
from compute_backend import association_matrices, resolve_compute_backend


def _prepared(n=240, fs=20):
    time = np.arange(n) / fs
    frame = pd.DataFrame({
        "time": time,
        "x": np.sin(2 * np.pi * 0.7 * time) + 0.1 * np.cos(2 * np.pi * 0.13 * time),
        "y": 0.4 * np.sin(2 * np.pi * 0.7 * time + 0.2),
        "constant": np.full(n, 3.0),
    })
    return core.prepare_data(frame, {"time_col": "time", "value_cols": ["x", "y", "constant"], "fs": fs})


def test_preprocessing_defaults_off_and_linear_detrend_keeps_physical_branch():
    n, fs = 100, 10
    time = np.arange(n) / fs
    source = pd.DataFrame({"time": time, "y": 3 + 0.4 * time})
    unchanged = core.prepare_data(source, {"time_col": "time", "value_cols": ["y"], "fs": fs})
    np.testing.assert_array_equal(unchanged["data"]["y"], source["y"])
    assert unchanged["meta"]["preprocessing"]["processing_order"] == []

    detrended = core.prepare_data(source, {"time_col": "time", "value_cols": ["y"], "fs": fs,
                                           "preprocess_detrend": "linear"})
    pd.testing.assert_frame_equal(detrended["original"], unchanged["original"])
    np.testing.assert_allclose(detrended["data"]["y"], 0, atol=1e-12)
    assert detrended["meta"]["preprocessing"]["processing_order"] == ["detrend:linear"]


def test_hampel_replaces_one_spike_on_a_local_ramp_and_records_count():
    ramp = np.arange(31, dtype=float)
    ramp[15] = 100.0
    filtered, meta = options.apply_signal_preprocessing(
        ramp, 10, {"hampel_enabled": "on", "hampel_window": 5, "hampel_sigma": 3.0})
    assert filtered[15] == 16.0
    np.testing.assert_array_equal(np.delete(filtered, 15), np.delete(ramp, 15))
    assert meta["details"]["hampel"]["replaced_total"] == 1


def test_notch_matches_scipy_and_rejects_nyquist():
    fs = 100
    time = np.arange(1000) / fs
    values = np.sin(2 * np.pi * 5 * time) + np.sin(2 * np.pi * 20 * time)
    observed, meta = options.apply_signal_preprocessing(
        values, fs, {"notch_frequency": 20, "notch_q": 30})
    b, a = signal.iirnotch(20, 30, fs=fs)
    expected = signal.filtfilt(b, a, values)
    np.testing.assert_allclose(observed, expected, rtol=1e-12, atol=1e-12)
    assert meta["details"]["notch"] == {"frequency_hz": 20.0, "q": 30.0}
    with pytest.raises(ValueError, match="Nyquist"):
        options.apply_signal_preprocessing(values, 20, {"notch_frequency": 10})


def test_wavelet_denoising_preserves_length_and_finite_values():
    pytest.importorskip("pywt")
    time = np.arange(256) / 50
    values = np.sin(2 * np.pi * time) + 0.02 * np.cos(2 * np.pi * 13 * time)
    observed, meta = options.apply_signal_preprocessing(values, 50, {"wavelet_enabled": "on", "wavelet_name": "db4"})
    assert observed.shape == values.shape
    assert np.isfinite(observed).all()
    assert meta["details"]["wavelet"]["name"] == "db4"


def test_time_domain_feature_oracles_and_degenerate_rules():
    values = np.array([0.0, 1.0, 1.0, 0.0])
    metrics = options.time_domain_metrics(values, fs=2)
    assert metrics["hjorth_activity"] == pytest.approx(1 / 3)
    assert metrics["line_length"] == pytest.approx(2)
    assert metrics["zero_crossing_rate"] == pytest.approx(4 / 3)
    assert metrics["hjorth_complexity"] == pytest.approx(0)

    constant = options.time_domain_metrics(np.ones(5), fs=10)
    assert constant["hjorth_activity"] == 0
    assert np.isnan(constant["hjorth_mobility"])
    assert np.isnan(constant["hjorth_complexity"])
    assert constant["zero_crossing_rate"] == 0
    assert options.permutation_entropy(np.arange(3.0), order=3, delay=1) == 0
    assert options.permutation_entropy(np.ones(5), order=3, delay=1) == 0
    assert np.isnan(options.permutation_entropy([1.0, 2.0], order=3, delay=1))
    # Three ordinal patterns occur with probabilities .4, .3, .3.
    periodic = np.tile([0.0, 1.0, 2.0], 4)
    expected = -(0.4 * np.log(0.4) + 2 * 0.3 * np.log(0.3)) / np.log(6)
    assert options.permutation_entropy(periodic) == pytest.approx(expected)


def test_summary_includes_time_domain_features_and_kendall_matrix():
    result = core.analyze(_prepared(), {"backend": "cpu", "workers": 1})
    summary = result["tables"]["summary"].set_index("variable")
    assert {"hjorth_activity", "hjorth_mobility", "hjorth_complexity",
            "permutation_entropy", "line_length", "zero_crossing_rate"}.issubset(summary.columns)
    assert np.isnan(summary.loc["constant", "hjorth_mobility"])
    assert "kendall" in result["matrices"]
    assert np.isnan(result["matrices"]["kendall"].loc["constant", "x"])
    assert "pvalue" not in result["matrices"]["kendall"].columns


def test_cpu_and_threaded_analysis_matrices_match_in_stable_order():
    prepared = _prepared()
    serial = core.analyze(prepared, {"backend": "cpu", "workers": 1})
    parallel = core.analyze(prepared, {"backend": "cpu", "workers": 4})
    assert serial["meta"]["compute_backend"]["workers"] == 1
    assert parallel["meta"]["compute_backend"]["workers"] == 4
    for name in serial["matrices"]:
        np.testing.assert_allclose(serial["matrices"][name], parallel["matrices"][name],
                                   equal_nan=True, rtol=1e-12, atol=1e-12)


def test_cupy_matrix_oracle_when_cuda_is_available():
    values = np.random.default_rng(1403).normal(size=(200, 4))
    values[:, 3] = 1.0
    cpu = resolve_compute_backend({"backend": "cpu", "workers": 1})
    try:
        gpu = resolve_compute_backend({"backend": "gpu", "workers": 1})
    except RuntimeError as exc:
        pytest.skip(f"CuPy/CUDA unavailable in this environment: {exc}")
    cpu_matrices = association_matrices(values, cpu)
    gpu_matrices = association_matrices(values, gpu)
    assert gpu["actual"] == "gpu"
    for expected, observed in zip(cpu_matrices, gpu_matrices):
        np.testing.assert_allclose(expected, observed, equal_nan=True, rtol=1e-10, atol=1e-12)
    assert gpu_matrices[2][0, 0] == 0.0
    assert np.isnan(gpu_matrices[2][3, 3])


def test_eda_quality_masks_split_decomposition_without_interpolation():
    fs = 10
    raw = np.full(600, 2.0)
    raw[300] = 12.0
    prepared = core.prepare_data(pd.DataFrame({"eda": raw}), {"value_cols": ["eda"], "fs": fs})
    result = experimental.analyze_eda(
        prepared, "eda", {"unit": "uS", "clean": "none", "method": "median",
                          "min_us": 1.0, "max_us": 10.0,
                          "max_slope_us_per_second": 50.0})
    signals = result["tables"]["eda_signals"]
    quality = result["tables"]["eda_quality_summary"].iloc[0]
    assert quality["n_above_max_us"] == 1
    assert quality["n_slope_edges_exceeded"] == 2
    assert quality["n_quality_invalid"] == 3
    assert signals.loc[299:301, "quality_invalid"].all()
    assert not signals.loc[299:301, "valid"].any()
    assert signals.loc[299:301, "clean"].isna().all()


def test_cvxeda_missing_optional_dependency_has_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cvxopt", None)
    fs = 10
    values = np.full(250, 2.0)
    prepared = core.prepare_data(pd.DataFrame({"eda": values}), {"value_cols": ["eda"], "fs": fs})
    with pytest.raises(ImportError, match="cvxopt"):
        experimental.analyze_eda(prepared, "eda", {"unit": "uS", "method": "cvxeda", "clean": "none"})


def test_sparse_decomposition_failure_is_not_reported_as_success(monkeypatch):
    import neurokit2 as nk

    def fail_sparse(*args, **kwargs):
        raise ValueError("simulated upstream failure")

    monkeypatch.setattr(nk, "eda_phasic", fail_sparse)
    prepared = core.prepare_data(pd.DataFrame({"eda": np.full(250, 2.0)}),
                                 {"value_cols": ["eda"], "fs": 10})
    with pytest.raises(RuntimeError, match="SparsEDA.*实验性.*simulated upstream failure"):
        experimental.analyze_eda(prepared, "eda", {"unit": "uS", "method": "sparse", "clean": "none"})
