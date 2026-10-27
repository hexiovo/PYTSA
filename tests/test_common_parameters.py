from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import signal

import core
import experimental
import analysis_options as options
from workspace_service import WorkspaceStore


def prepared_signal(n=400, fs=10, **config):
    t = np.arange(n) / fs
    frame = pd.DataFrame({"time": t, "y": np.sin(2*np.pi*t) + .2*np.cos(2*np.pi*.1*t)})
    return core.prepare_data(frame, dict(time_col="time", value_cols=["y"], fs=fs, **config))


@pytest.mark.parametrize("unit,scale", [("s", 1), ("ms", 1000), ("us", 1000000)])
def test_time_units_markers_and_preflight_agree(tmp_path, unit, scale):
    frame = pd.DataFrame({"time": np.arange(100)*.1*scale, "y": np.arange(100.),
                          "marker": [""]*20 + ["A"]*80})
    p = core.prepare_data(frame, dict(time_col="time", value_cols=["y"], fs=10, time_scale=unit))
    np.testing.assert_allclose(p["original"]["time"], np.arange(100)*.1, atol=2e-15)
    assert p["fs"] == pytest.approx(10)
    events = experimental.markers_to_events(frame, "marker", "time", time_scale=unit)
    assert events.iloc[0]["time"] == pytest.approx(2)
    issues = WorkspaceStore(tmp_path).preflight_dataframe(frame, time_col="time", value_cols=["y"],
                                                         sampling_rate=10, time_scale=unit)
    assert not any(i["check"] in {"irregular_sampling", "sampling_rate_mismatch"} for i in issues)


def test_timezone_equivalence_and_ambiguous_time_rejection():
    local = pd.date_range("2026-01-01 08:00", periods=40, freq="s")
    frame = pd.DataFrame({"t": local.astype(str), "y": np.arange(40.)})
    p = core.prepare_data(frame, dict(time_col="t", value_cols=["y"], time_kind="datetime", timezone="Asia/Shanghai"))
    assert p["original"]["t"].iloc[0] == pd.Timestamp("2026-01-01T00:00Z")
    frame["t"] = local.tz_localize("Asia/Shanghai").tz_convert("UTC")
    q = core.prepare_data(frame, dict(time_col="t", value_cols=["y"], time_kind="datetime"))
    pd.testing.assert_series_equal(p["original"]["t"], q["original"]["t"])
    with pytest.raises(ValueError, match="时区"):
        options.datetime_utc(["2026-11-01 01:30"], "America/New_York")


@pytest.mark.parametrize("policy", ["interpolate", "ffill"])
def test_gap_limits_preserve_physical_values(policy):
    frame = pd.DataFrame({"y": np.arange(100.)})
    frame.loc[10:12, "y"] = np.nan
    config = dict(value_cols=["y"], fs=10, missing=policy, max_gap_samples=3, max_gap_seconds=.3)
    p = core.prepare_data(frame, config)
    assert p["original"]["y"].isna().sum() == 3
    assert not p["data"]["y"].isna().any()
    for changed in ({"max_gap_samples": 2}, {"max_gap_seconds": .29}):
        with pytest.raises(ValueError, match="上限"):
            core.prepare_data(frame, {**config, **changed})


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass"])
@pytest.mark.parametrize("phase", ["zero", "causal"])
def test_filter_matches_scipy_and_preserves_original(kind, phase):
    p = prepared_signal(500, 20)
    cfg = dict(filter_type=kind, filter_low=.5, filter_high=2., filter_order=4, filter_phase=phase)
    raw = p["original"]
    q = core.prepare_data(raw, dict(time_col="time", value_cols=["y"], fs=20, **cfg))
    cut = [.5, 2.] if kind == "bandpass" else .5 if kind == "highpass" else 2.
    sos = signal.butter(4, cut, btype=kind, fs=20, output="sos")
    fn = signal.sosfiltfilt if phase == "zero" else signal.sosfilt
    expected = fn(sos, raw["y"].to_numpy())
    np.testing.assert_allclose(q["data"]["y"], expected, atol=2e-13, rtol=2e-12)
    pd.testing.assert_frame_equal(q["original"], raw)


def test_filter_attenuates_rejected_frequency():
    fs = 100
    t = np.arange(2000)/fs
    y = np.sin(2*np.pi*t) + np.sin(2*np.pi*20*t)
    filtered = options.filter_values(y, fs, dict(filter_type="lowpass", filter_high=5))
    assert np.std(filtered[200:-200] - np.sin(2*np.pi*t[200:-200])) < .002


def test_summary_quartiles_mad_and_sample_variance():
    frame = pd.DataFrame({"y": np.arange(1., 21)})
    p = core.prepare_data(frame, dict(value_cols=["y"]))
    row = core.analyze(p, {"period": 5})["tables"]["summary"].iloc[0]
    assert row["variance"] == pytest.approx(35)
    assert row["peak_to_peak"] == 19
    assert row["q1"] == 5.75
    assert row["q3"] == 15.25
    assert row["mad"] == 5


@pytest.mark.parametrize("window,detrend,overlap", [("hamming", "linear", .25), ("boxcar", "none", 0)])
def test_welch_settings_power_and_windows(window, detrend, overlap):
    p = prepared_signal(400)
    config = dict(nperseg=100, nfft=200, welch_window=window, welch_detrend=detrend,
                  welch_overlap=overlap, band_low=.5, band_high=1.5,
                  power_bands="lower:0:2;upper:2:5", window_seconds=10, window_overlap=.5)
    a = core.analyze(p, config)
    f, power = signal.welch(p["data"]["y"].to_numpy(), fs=10, window=window, detrend=False if detrend == "none" else detrend,
                            nperseg=100, nfft=200, noverlap=int(100*overlap))
    np.testing.assert_allclose(a["tables"]["psd"]["psd"], power, atol=2e-13, rtol=2e-12)
    row = a["tables"]["summary"].iloc[0]
    assert row["total_power"] == pytest.approx(np.trapezoid(power, f))
    assert a["tables"]["band_power"]["relative_power"].sum() == pytest.approx(1)
    assert row["spectral_centroid"] == pytest.approx(np.trapezoid(f*power, f)/np.trapezoid(power, f))
    w = a["tables"]["windows"]
    np.testing.assert_allclose(w["start_seconds"], np.arange(0., 40., 5.), rtol=2e-14, atol=2e-14)
    assert w.iloc[-1]["status"] == "PARTIAL"
    assert w.iloc[0]["n"] == 100


@pytest.mark.parametrize("cfg", [{"welch_overlap": 1}, {"nfft": 2}, {"window_seconds": 0},
                                 {"power_bands": "a:2:1"}, {"window_seconds": 10, "window_overlap": 1}])
def test_invalid_analysis_settings_rejected(cfg):
    with pytest.raises(ValueError):
        core.analyze(prepared_signal(), cfg)


def test_eda_window_metrics_and_event_auc_hand_oracle():
    fs = 10
    time = np.arange(200)/fs
    signals = pd.DataFrame(dict(time=time, clean=2+time, tonic=2+time, valid=True))
    peaks = pd.DataFrame([dict(onset_seconds=6.2, peak_seconds=7., amplitude=.4,
                               rise_seconds=.8, recovery50_seconds=2., segment=1)], columns=experimental.PEAK_COLUMNS)
    rows = experimental._window_rows(signals, peaks, 0, 20, 10, fs, 5)
    assert [r["start_seconds"] for r in rows] == [0, 5, 10, 15]
    assert rows[0]["scl_slope_per_second"] == pytest.approx(1)
    assert rows[0]["scl_std"] == pytest.approx(np.std(2+time[:100], ddof=1))
    assert rows[0]["scr_amplitude_median"] == .4
    assert rows[0]["scr_rise_seconds_mean"] == .8
    assert rows[0]["scr_recovery50_n"] == 1
    events = pd.DataFrame({"time": [5., 15.], "label": ["A", "A"]})
    cfg = dict(baseline_start=-2., baseline_end=0., response_start=1., response_end=3., baseline_correction="subtract_mean")
    detail, grouped = experimental._events(signals, peaks, events, cfg, fs, 0, 20)
    # Baseline [3,5) => mean clean=5.95; response [6,8) => mean clean=8.95.
    assert detail.iloc[0]["delta_mean"] == pytest.approx(3.)
    assert detail.iloc[0]["response_auc"] == pytest.approx(3.*1.9)
    assert grouped.iloc[0]["nonresponse_fraction"] == .5
    assert grouped.iloc[0]["nonresponse_count"] == 1
    signals.loc[65, "valid"] = False
    detail, grouped = experimental._events(signals, peaks, events, cfg, fs, 0, 20)
    assert np.isnan(detail.iloc[0]["response_auc"])
    assert grouped.iloc[0]["n_valid"] == 1
    assert grouped.iloc[0]["nonresponse_fraction"] == 1


@pytest.mark.parametrize("mode,expected", [("none", 8.95), ("subtract_mean", 3.),
                                          ("subtract_median", 3.), ("ratio", 8.95/5.95),
                                          ("percent", 300/5.95)])
def test_all_event_baseline_modes(mode, expected):
    signals = pd.DataFrame(dict(time=np.arange(200)/10, clean=2+np.arange(200)/10, valid=True))
    peaks = pd.DataFrame(columns=experimental.PEAK_COLUMNS)
    cfg = dict(baseline_start=-2., baseline_end=0., response_start=1., response_end=3., baseline_correction=mode)
    detail, _ = experimental._events(signals, peaks, pd.DataFrame({"time": [5.], "label": ["A"]}), cfg, 10, 0, 20)
    assert detail.iloc[0]["corrected_response_mean"] == pytest.approx(expected)


def test_rolling_validation_is_causal_and_test_missing_not_filled():
    p = prepared_signal(100)
    cfg = dict(models=["Naive"], horizon=4, period=5, validation="rolling",
               rolling_train=40, rolling_horizon=10, rolling_step=20, missing="ffill")
    p["original"].loc[45, "y"] = np.nan
    a = core.forecast(p, "y", cfg)
    metrics = a["rolling_metrics"]
    assert list(metrics["test_start"]) == [40, 60, 80]
    assert metrics.iloc[0]["n_test"] == 9
    first = a["rolling_predictions"].query("fold == 1")
    assert first["predicted"].eq(p["original"].iloc[39]["y"]).all()
    assert first["actual"].isna().sum() == 1
    p["original"].loc[60:, "y"] += 1000
    b = core.forecast(p, "y", cfg)
    pd.testing.assert_frame_equal(a["rolling_predictions"].query("fold == 1"), b["rolling_predictions"].query("fold == 1"))


def test_prediction_confidence_changes_bounds_only():
    p = prepared_signal(100)
    base = dict(models=["ARIMA"], order=(1,0,0), horizon=3)
    a = core.forecast(p, "y", {**base, "confidence": .8})["future"]
    b = core.forecast(p, "y", {**base, "confidence": .99})["future"]
    np.testing.assert_allclose(a["predicted"], b["predicted"], atol=1e-12)
    assert ((b["upper"] - b["lower"]) > (a["upper"] - a["lower"])).all()


def test_template_project_and_report_roundtrip_new_results(tmp_path):
    import report_export
    import tkinter as tk
    from app import PytsaApp
    root = tk.Tcl()
    ui = PytsaApp.__new__(PytsaApp)
    # StringVars need the default Tcl root; provide it for this headless control test.
    previous = tk._default_root
    tk._default_root = root
    try:
        ui._init_additional_variables()
        parameters = {"prepare": {"time_scale": "ms", "filter_type": "lowpass", "filter_high": 2},
                      "analysis": {"power_bands": "slow:0:1", "window_seconds": 10},
                      "forecast": {"validation": "rolling", "rolling_train": 40},
                      "eda": {"config": {"baseline_correction": "percent", "window_overlap": .5}}}
        ui._apply_additional_parameters(parameters)
        store = WorkspaceStore(tmp_path / "workspace")
        saved = store.save_parameter_template("extended", parameters)
        assert store.get_parameter_template(saved["id"])["parameters"] == parameters
        state = ui._additional_state()
        path = store.save_project({"raw_frame": pd.DataFrame({"y": [1.]}), "params": parameters,
                                   "ui_state": {"additional_options": state}})
        restored = store.load_project(path)
        ui._restore_additional_state(restored["ui_state"]["additional_options"])
        assert ui._additional_config("prepare")["time_scale"] == "ms"
        assert ui._additional_config("eda")["baseline_correction"] == "percent"
        ui._apply_additional_parameters({"prepare": {}, "analysis": {}, "forecast": {}, "eda": {}})
        assert ui._additional_config("prepare")["filter_type"] == "none"
        assert ui._additional_config("analysis")["window_seconds"] is None
    finally:
        tk._default_root = previous
    p = prepared_signal(100)
    a = core.analyze(p, dict(window_seconds=2, power_bands="slow:0:1"))
    forecast = core.forecast(p, "y", dict(models=["Naive"], validation="rolling", rolling_train=40,
                                         rolling_horizon=10, rolling_step=20))
    result = report_export.export_report(tmp_path / "export", p, a, forecast,
                                         template={"formats": ["csv", "xlsx", "json", "html"]})
    folder = Path(result["folder"])
    assert (folder / "forecast_rolling_metrics.csv").exists()
    assert (folder / "forecast_rolling_predictions.csv").exists()
    assert (folder / "analysis_band_power.csv").exists()
    assert (folder / "analysis_windows.csv").exists()
    payload = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    assert payload["result_tables"]["forecast"]["rolling_metrics"]
    assert "rolling_metrics" in (folder / "report.html").read_text(encoding="utf-8")


def test_odd_fft_band_edges_and_zero_power():
    p = prepared_signal(400)
    a = core.analyze(p, dict(nfft=401, power_bands="all:0:5;tail:4.999:5"))
    bands = a["tables"]["band_power"]
    assert bands.iloc[0]["relative_power"] == pytest.approx(1)
    assert bands.iloc[1]["power"] == 0
    p["data"]["y"] = 0.
    row = core.analyze(p, {})["tables"]["summary"].iloc[0]
    assert row["total_power"] == 0
    assert np.isnan(row["relative_band_power"])
    assert np.isnan(row["spectral_centroid"])


def test_preflight_mixed_timezones_and_bad_dates(tmp_path):
    store = WorkspaceStore(tmp_path)
    frame = pd.DataFrame({"t": ["2026-01-01T08:00:00+08:00", "2026-01-01T00:00:01Z", "bad"], "y": [1., 2., 3.]})
    issues = store.preflight_dataframe(frame, time_col="t", time_kind="datetime", timezone="Asia/Shanghai")
    assert next(row for row in issues if row["check"] == "invalid_time")["count"] == 1


def test_zero_baseline_is_flagged_and_nonresponse_denominator_excludes_it():
    signals = pd.DataFrame(dict(time=np.arange(200)/10, clean=np.zeros(200), valid=True))
    peaks = pd.DataFrame(columns=experimental.PEAK_COLUMNS)
    cfg = dict(baseline_start=-2., baseline_end=0., response_start=1., response_end=3., baseline_correction="percent")
    detail, grouped = experimental._events(signals, peaks, pd.DataFrame({"time": [5.], "label": ["A"]}), cfg, 10, 0, 20)
    assert detail.iloc[0]["status"] == "ZERO_BASELINE"
    assert np.isnan(detail.iloc[0]["response_auc"])
    assert grouped.iloc[0]["n_valid"] == 0
    assert np.isnan(grouped.iloc[0]["nonresponse_fraction"])


@pytest.mark.parametrize("cfg", [{"confidence": 1}, {"test_fraction": .5},
                                 {"validation": "rolling", "rolling_train": 95, "rolling_horizon": 10},
                                 {"validation": "rolling", "rolling_step": 0}])
def test_invalid_forecast_settings_rejected(cfg):
    with pytest.raises(ValueError):
        core.forecast(prepared_signal(100), "y", dict(models=["Naive"], **cfg))


def test_rolling_failed_fold_keeps_other_folds_and_warns():
    p = prepared_signal(100)
    p["original"].loc[20, "y"] = np.nan
    result = core.forecast(p, "y", dict(models=["Naive"], missing="ffill", validation="rolling",
                                        rolling_train=20, rolling_horizon=10, rolling_step=20))
    assert list(result["rolling_metrics"]["status"]) == ["OK", "FAILED", "OK", "OK"]
    assert any("rolling" in warning for warning in result["warnings"])
