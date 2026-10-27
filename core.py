"""Core data preparation, time-series analysis, forecasting, and export APIs.

The module deliberately separates exploratory preparation from forecasting:
``prepared['data']`` may contain the requested interpolation/transformation,
while ``prepared['original']`` retains physical measurements for holdout work.
"""

from __future__ import annotations

import html
import json
import math
import os
import platform
import re
import time
import warnings as pywarnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import scipy
from scipy import signal, stats
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.stattools import adfuller, acf, kpss, pacf
from statsmodels.stats.diagnostic import acorr_ljungbox
from analysis_options import (numeric_seconds, datetime_utc, validate_gap_limits, filter_values,
                              welch_options, basic_metrics, spectral_metrics, frequency_bands,
                              integrate_band, window_metrics, rolling_validation)


def load_table(path, sheet_name=0, encoding="utf-8-sig", sep=None) -> pd.DataFrame:
    """Load a CSV, TSV, TXT, or XLSX table without modifying the source file."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Input file does not exist: {source}")
    suffix = source.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        frame = pd.read_excel(source, sheet_name=sheet_name)
    elif suffix in {".csv", ".tsv", ".txt"}:
        delimiter = sep
        if delimiter is None and suffix == ".tsv":
            delimiter = "\t"
        if delimiter is None:
            frame = pd.read_csv(source, encoding=encoding, sep=None, engine="python")
        else:
            frame = pd.read_csv(source, encoding=encoding, sep=delimiter)
    else:
        raise ValueError("Supported input formats are CSV, TSV, TXT, and XLSX.")
    if frame.empty:
        raise ValueError("The selected table is empty.")
    return frame.copy()


def demo_data() -> pd.DataFrame:
    """Return fixed-seed synthetic signals. This is SYNTHETIC_ONLY data."""
    rng = np.random.default_rng(20261026)
    n = 1200
    t = np.arange(n, dtype=float) / 10.0
    slow = 0.65 * np.sin(2 * np.pi * 0.18 * t)
    fast = 0.18 * np.sin(2 * np.pi * 1.25 * t + 0.35)
    drift = 0.0008 * (np.arange(n) - n / 2)
    shared = slow + fast + drift
    frame = pd.DataFrame(
        {
            "time_seconds": t,
            "signal_a": shared + rng.normal(0, 0.16, n),
            "signal_b": 0.72 * shared + rng.normal(0, 0.22, n),
            "signal_c": 0.45 * np.sin(2 * np.pi * 0.31 * t) + rng.normal(0, 0.2, n),
        }
    )
    frame.attrs["status"] = "SYNTHETIC_ONLY"
    frame.attrs["generator"] = "fixed seed 20261026; three synthetic sinusoid/noise channels"
    return frame


def _get_time_axis(frame: pd.DataFrame, time_col: str | None, time_kind: str, fs: float,
                   time_scale="s", timezone="UTC"):
    """Parse time once and return frame, numeric seconds key, and metadata."""
    result = frame.copy()
    if time_col is None:
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError("fs must be a finite positive number when no time column is selected.")
        name = "time_seconds"
        counter = 1
        while name in result.columns:
            name = f"__time_seconds_{counter}__"
            counter += 1
        result[name] = np.arange(len(result), dtype=float) / float(fs)
        return result, result[name].to_numpy(dtype=float), {
            "time_col": name,
            "time_kind": "numeric",
            "time_unit": "seconds (generated from sample index and fs)",
            "time_zone_note": None,
        }

    if time_col not in result.columns:
        raise ValueError(f"Selected time column {time_col!r} is not in the table.")
    if time_kind not in {"auto", "numeric", "datetime"}:
        raise ValueError("time_kind must be 'auto', 'numeric', or 'datetime'.")
    source = result[time_col]
    chosen = time_kind
    if chosen == "auto":
        as_numeric = pd.to_numeric(source, errors="coerce")
        chosen = "numeric" if as_numeric.notna().all() else "datetime"

    if chosen == "numeric":
        parsed = pd.to_numeric(source, errors="coerce")
        if parsed.isna().any():
            bad = int(parsed.isna().sum())
            raise ValueError(f"Time column {time_col!r} has {bad} values that are not numeric.")
        values = numeric_seconds(parsed, time_scale)
        if not np.isfinite(values).all():
            raise ValueError("Time values must be finite; infinite timestamps are not supported.")
        result[time_col] = values
        return result, values, {
            "time_col": time_col,
            "time_kind": "numeric",
            "time_unit": "seconds",
            "input_time_unit": time_scale,
            "time_zone_note": None,
        }

    parsed = datetime_utc(source, timezone)
    if parsed.isna().any():
        bad = int(parsed.isna().sum())
        raise ValueError(f"Time column {time_col!r} has {bad} values that cannot be parsed as datetimes.")
    result[time_col] = parsed
    seconds = parsed.astype("int64", copy=False).to_numpy(dtype=np.float64) / 1e9
    return result, seconds, {
        "time_col": time_col,
        "time_kind": "datetime",
        "time_unit": "UTC datetime; sampling interval and fs are expressed in seconds/Hz",
        "time_zone_note": f"Naive datetimes are interpreted as {timezone}; aware datetimes are converted to UTC.",
        "input_timezone": timezone,
    }


def _aggregate_duplicates(frame: pd.DataFrame, time_key: str, value_cols: list[str], policy: str):
    if policy not in {"reject", "mean", "first"}:
        raise ValueError("duplicate must be 'reject', 'mean', or 'first'.")
    duplicated = frame[time_key].duplicated(keep=False)
    if not duplicated.any():
        return frame
    count = int(duplicated.sum())
    if policy == "reject":
        raise ValueError(f"Found {count} rows with duplicate timestamps; choose duplicate='mean' or 'first'.")
    aggregations = {col: ("mean" if policy == "mean" and col in value_cols else "first") for col in frame.columns}
    return frame.groupby(time_key, as_index=False, sort=True).agg(aggregations)


def _resample_frame(frame: pd.DataFrame, time_key: str, interval: float, value_cols: list[str]):
    if not np.isfinite(interval) or interval <= 0:
        raise ValueError("resample must be a finite positive interval in seconds.")
    keys = frame[time_key].to_numpy(dtype=float)
    start = float(keys[0])
    bins = np.floor((keys - start) / interval + 1e-10).astype(np.int64)
    work = frame.copy()
    work[time_key] = keys
    work["__resample_bin__"] = bins
    aggregations = {col: "mean" if col in value_cols else "first"
                    for col in frame.columns if col != time_key}
    grouped = work.groupby("__resample_bin__", sort=True).agg(aggregations)
    grouped.index.name = None
    grid = np.arange(int(bins.min()), int(bins.max()) + 1, dtype=np.int64)
    grouped = grouped.reindex(grid)
    grouped[time_key] = start + grid.astype(float) * interval
    grouped = grouped.reset_index(drop=True)
    return grouped


def _time_values_from_seconds(seconds: np.ndarray, time_meta: dict[str, Any]) -> np.ndarray:
    if time_meta["time_kind"] == "datetime":
        return pd.to_datetime(np.rint(seconds * 1e9).astype("int64"), utc=True).to_numpy()
    return np.asarray(seconds, dtype=float)


def prepare_data(df, config) -> dict[str, Any]:
    """Sort and validate a table, apply explicit missing-value and transform rules.

    ``original`` is the deduplicated/resampled physical-value branch. It keeps
    missing bins as missing even when ``data`` is filled for exploration.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame.")
    if df.empty:
        raise ValueError("The input table is empty.")
    cfg = dict(config or {})
    value_cols = cfg.get("value_cols")
    if not isinstance(value_cols, (list, tuple)) or not value_cols:
        raise ValueError("Select at least one numeric value column.")
    value_cols = list(value_cols)
    if len(set(value_cols)) != len(value_cols):
        raise ValueError("value_cols contains duplicate column names.")
    if len(value_cols) > 32:
        raise ValueError("Select no more than 32 signal channels.")
    absent = [name for name in value_cols if name not in df.columns]
    if absent:
        raise ValueError(f"Selected value columns are missing: {absent!r}.")
    if any(not isinstance(name, str) for name in value_cols):
        raise ValueError("Selected value columns must have string column names.")

    fs_cfg = float(cfg.get("fs", 1.0))
    if not np.isfinite(fs_cfg) or fs_cfg <= 0:
        raise ValueError("fs must be a finite positive number.")
    time_col = cfg.get("time_col")
    if time_col in value_cols:
        raise ValueError("The selected time column cannot also be used as a signal column.")
    time_kind = cfg.get("time_kind", "auto")
    frame, times, time_meta = _get_time_axis(df, time_col, time_kind, fs_cfg,
                                           cfg.get("time_scale", "s"), cfg.get("timezone", "UTC"))
    time_key = "__pytsa_time_key__"
    while time_key in frame.columns:
        time_key += "_"
    frame[time_key] = times

    # Reject categorical signal columns instead of converting their labels to NaN.
    for name in value_cols:
        numeric = pd.to_numeric(frame[name], errors="coerce")
        invalid = frame[name].notna() & numeric.isna()
        if invalid.any():
            examples = frame.loc[invalid, name].astype(str).head(3).tolist()
            raise ValueError(f"Signal column {name!r} contains non-numeric values, e.g. {examples!r}.")
        infinite = np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan))
        if infinite.any():
            raise ValueError(f"Signal column {name!r} contains infinite values; resolve them explicitly first.")
        frame[name] = numeric.astype(float)

    frame = frame.sort_values(time_key, kind="mergesort").reset_index(drop=True)
    duplicate_policy = cfg.get("duplicate", "reject")
    before_dups = len(frame)
    frame = _aggregate_duplicates(frame, time_key, value_cols, duplicate_policy)
    duplicate_rows = before_dups - len(frame)

    interval = cfg.get("resample")
    warnings_out: list[str] = []
    if interval is not None:
        interval = float(interval)
        frame = _resample_frame(frame, time_key, interval, value_cols)
        warnings_out.append(f"Resampled into {interval:g}-second bins using mean aggregation for selected signal columns; other metadata takes the first bin value and empty bins remain missing.")
        step_seconds = interval
    else:
        keys = frame[time_key].to_numpy(dtype=float)
        diffs = np.diff(keys)
        if len(diffs) == 0 or np.any(diffs <= 0):
            raise ValueError("At least 16 unique, increasing time points are required.")
        step_seconds = float(np.median(diffs))
        tolerance = max(abs(step_seconds) * 1e-3, 1e-9)
        if not np.allclose(diffs, step_seconds, rtol=1e-3, atol=tolerance):
            raise ValueError("Time points are irregular. Choose an explicit resample interval in seconds.")

    if not np.isfinite(step_seconds) or step_seconds <= 0:
        raise ValueError("The time step must be finite and positive.")
    fs = float(1.0 / step_seconds)
    if time_col is not None and not math.isclose(fs_cfg, fs, rel_tol=0.01, abs_tol=1e-9):
        warnings_out.append(f"fs={fs_cfg:g} was not used because the selected time column determines the sampling rate ({fs:g} Hz).")
    if duplicate_rows:
        warnings_out.append(f"Combined {duplicate_rows} duplicate timestamp rows using duplicate='{duplicate_policy}'.")

    # Restore normalized time labels and remove only the internal sort key.
    seconds = frame[time_key].to_numpy(dtype=float)
    frame[time_meta["time_col"]] = _time_values_from_seconds(seconds, time_meta)
    original = frame.drop(columns=[time_key]).copy()
    time_name = time_meta["time_col"]
    ordered_cols = [time_name] + [name for name in original.columns if name != time_name]
    original = original.loc[:, ordered_cols]

    missing_policy = cfg.get("missing", "reject")
    if missing_policy not in {"reject", "ffill", "interpolate", "drop"}:
        raise ValueError("missing must be 'reject', 'ffill', 'interpolate', or 'drop'.")
    values = original[value_cols]
    missing_cells = int(values.isna().sum().sum())
    if missing_cells and missing_policy == "reject":
        raise ValueError(f"Selected signals contain {missing_cells} missing values; choose ffill or interpolate explicitly.")
    if missing_cells and missing_policy == "drop":
        raise ValueError("missing='drop' can change time spacing. Choose ffill or interpolate for equally spaced analysis.")

    data = original.copy()
    validate_gap_limits(values, fs, cfg)
    if missing_policy == "ffill":
        data.loc[:, value_cols] = data[value_cols].ffill()
        if data[value_cols].isna().any().any():
            raise ValueError("Forward fill cannot resolve leading missing values; remove them explicitly or choose interpolate.")
        if missing_cells:
            warnings_out.append(f"Forward-filled {missing_cells} missing signal cells in the exploratory data branch only.")
    elif missing_policy == "interpolate":
        data.loc[:, value_cols] = data[value_cols].interpolate(method="linear", limit_area="inside")
        if data[value_cols].isna().any().any():
            raise ValueError("Linear interpolation cannot resolve edge gaps; trim them explicitly or choose forward fill.")
        if missing_cells:
            warnings_out.append(f"Linearly interpolated {missing_cells} internal missing signal cells in the exploratory data branch only.")

    data.loc[:, value_cols] = filter_values(data[value_cols].to_numpy(dtype=float), fs, cfg)
    if cfg.get("filter_type", "none") != "none":
        warnings_out.append(f"Butterworth {cfg['filter_type']} filtering applied only to exploratory values; phase={cfg.get('filter_phase', 'zero')}.")
    transform = cfg.get("transform", "none")
    if transform not in {"none", "demean", "detrend", "zscore", "difference"}:
        raise ValueError("transform must be 'none', 'demean', 'detrend', 'zscore', or 'difference'.")
    if transform == "demean":
        data.loc[:, value_cols] = data[value_cols] - data[value_cols].mean()
    elif transform == "detrend":
        data.loc[:, value_cols] = signal.detrend(data[value_cols].to_numpy(dtype=float), axis=0, type="linear")
    elif transform == "zscore":
        means = data[value_cols].mean()
        stds = data[value_cols].std(ddof=1)
        constant = stds.index[stds.eq(0)].tolist()
        if constant:
            raise ValueError(f"Cannot z-score constant signal columns: {constant!r}.")
        data.loc[:, value_cols] = (data[value_cols] - means) / stds
    elif transform == "difference":
        data.loc[:, value_cols] = data[value_cols].diff()
        data = data.iloc[1:].reset_index(drop=True)
        warnings_out.append("Applied first differences; timestamps mark the later observation in each adjacent pair.")

    if len(data) < 16:
        raise ValueError("At least 16 usable samples are required after preparation and transformation.")
    if data[value_cols].isna().any().any():
        raise ValueError("Prepared signal data still contain missing values.")

    source_status = "SYNTHETIC_ONLY" if bool(cfg.get("synthetic_only", df.attrs.get("status") == "SYNTHETIC_ONLY")) else "REAL_OR_USER_PROVIDED"
    source_meta: dict[str, Any] = {"status": source_status}
    if source_status == "SYNTHETIC_ONLY":
        source_meta["attrs"] = dict(getattr(df, "attrs", {}))
    meta = {
        "status": source_status,
        "source": source_meta,
        "value_cols": value_cols,
        **time_meta,
        "fs": fs,
        "time_step_seconds": step_seconds,
        "n_rows_input": int(len(df)),
        "n_rows_original": int(len(original)),
        "n_rows_prepared": int(len(data)),
        "duplicate_policy": duplicate_policy,
        "missing_policy": missing_policy,
        "missing_cells_original": missing_cells,
        "resample_seconds": interval,
        "transform": transform,
        "parameters": cfg,
        "warnings": list(warnings_out),
    }
    return {"data": data, "original": original, "fs": fs, "time_unit": time_meta["time_unit"],
            "meta": meta, "warnings": warnings_out}


def _spectral_summary(values: np.ndarray, fs: float, nperseg: int, config=None):
    n = len(values)
    options = welch_options({"nperseg": nperseg, **(config or {})}, n)
    seg = options["nperseg"]
    freq, power = signal.welch(values, fs=fs, scaling="density", **options)
    total = float(np.sum(power))
    if not np.isfinite(total) or total <= 0:
        entropy = float("nan")
        peak = float("nan")
    else:
        probabilities = power / total
        nonzero = probabilities > 0
        denom = math.log(len(probabilities)) if len(probabilities) > 1 else 0.0
        entropy = float(-np.sum(probabilities[nonzero] * np.log(probabilities[nonzero])) / denom) if denom > 0 else 0.0
        peak = float(freq[int(np.argmax(power))])
    return freq, power, entropy, peak, seg


def _constant(values: np.ndarray) -> bool:
    return len(values) == 0 or not np.isfinite(values).all() or float(np.ptp(values)) == 0.0


def _stl_components(values: np.ndarray, period: int):
    if period < 2 or len(values) < 2 * period:
        raise ValueError(f"STL requires at least two complete cycles (n >= 2*period={2 * period}).")
    with pywarnings.catch_warnings(record=True) as caught:
        pywarnings.simplefilter("always")
        result = STL(values, period=period, robust=True).fit()
    note = "; ".join(str(item.message) for item in caught)
    return result, note


def _matrix_frame(matrix: np.ndarray, names: list[str]) -> pd.DataFrame:
    return pd.DataFrame(matrix, index=names, columns=names)


def analyze(prepared, config) -> dict[str, Any]:
    """Compute descriptive, stationarity, decomposition, and matrix summaries."""
    if not isinstance(prepared, Mapping) or "data" not in prepared or "meta" not in prepared:
        raise TypeError("prepared must be the dictionary returned by prepare_data().")
    cfg = dict(config or {})
    data = prepared["data"]
    names = list(prepared["meta"]["value_cols"])
    if not isinstance(data, pd.DataFrame) or any(name not in data.columns for name in names):
        raise ValueError("Prepared data does not match its selected signal columns.")
    if len(data) < 16:
        raise ValueError("At least 16 prepared samples are required.")
    max_lag = int(cfg.get("max_lag", 30))
    period = int(cfg.get("period", 12))
    lag = int(cfg.get("lag", 1))
    nperseg = int(cfg.get("nperseg", 256))
    band_low = float(cfg.get("band_low", 0.0))
    band_high_cfg = cfg.get("band_high")
    if max_lag < 1 or period < 2 or lag < 0 or nperseg < 2:
        raise ValueError("max_lag and nperseg must be positive; period must be at least 2; lag cannot be negative.")
    if not np.isfinite(band_low) or band_low < 0:
        raise ValueError("band_low must be finite and nonnegative.")

    matrix_data = data[names].to_numpy(dtype=float)
    n = len(data)
    # Cache whole-column scans once; pairwise matrix loops reuse these flags.
    constant_flags = np.array([_constant(matrix_data[:, i]) for i in range(len(names))], dtype=bool)
    fs = float(prepared["fs"])
    nyquist = fs / 2.0
    band_high = nyquist if band_high_cfg is None else float(band_high_cfg)
    if not np.isfinite(band_high) or band_high <= band_low:
        raise ValueError("band_high must be greater than band_low and finite.")
    if band_high > nyquist:
        band_high = nyquist
        warnings_out = [f"band_high was limited to Nyquist frequency ({nyquist:g} Hz)."]
    else:
        warnings_out = []
    seg = max(2, min(nperseg, n // 2))
    spectral_options = welch_options(cfg, n)
    bands = frequency_bands(cfg, nyquist)
    band_rows = []
    if seg != nperseg:
        warnings_out.append(f"nperseg was reduced to {seg} so Welch coherence uses multiple segments.")

    summary_rows = []
    test_rows = []
    acf_rows = []
    psd_rows = []
    decomposition_rows = []
    time_values = data[prepared["meta"]["time_col"]].to_numpy()
    for i, name in enumerate(names):
        values = matrix_data[:, i]
        freq, power, entropy, peak, psd_seg = _spectral_summary(values, fs, nperseg, cfg)
        if band_low >= freq[-1]:
            raise ValueError("频带下限须小于最高可用频率。")
        power_summary = spectral_metrics(freq, power, band_low, band_high)
        for band_name, lower, upper in bands:
            band_power = integrate_band(freq, power, lower, upper)
            band_rows.append(dict(variable=name, band=band_name, low_hz=lower, high_hz=upper,
                                  power=band_power, relative_power=band_power / power_summary["total_power"]
                                  if power_summary["total_power"] > 0 else np.nan))
        for f, p in zip(freq, power):
            psd_rows.append({"variable": name, "frequency": float(f), "psd": float(p)})
        if constant_flags[i]:
            skew = kurt = float("nan")
        else:
            skew = float(stats.skew(values, bias=False))
            kurt = float(stats.kurtosis(values, fisher=True, bias=False))

        trend_strength = seasonal_strength = float("nan")
        stl_note = ""
        if n >= 2 * period:
            try:
                stl, stl_note = _stl_components(values, period)
                trend_component = np.asarray(stl.trend, dtype=float)
                seasonal_component = np.asarray(stl.seasonal, dtype=float)
                residual_component = np.asarray(stl.resid, dtype=float)
                trend_denom = float(np.var(trend_component + residual_component, ddof=1))
                seasonal_denom = float(np.var(seasonal_component + residual_component, ddof=1))
                residual_var = float(np.var(residual_component, ddof=1))
                if trend_denom > 0:
                    trend_strength = max(0.0, float(1.0 - residual_var / trend_denom))
                if seasonal_denom > 0:
                    seasonal_strength = max(0.0, float(1.0 - residual_var / seasonal_denom))
                for t, obs, tr, seas, resid in zip(time_values, values, stl.trend, stl.seasonal, stl.resid):
                    decomposition_rows.append({"variable": name, "time": t, "observed": float(obs),
                                               "trend": float(tr), "seasonal": float(seas), "residual": float(resid)})
                if stl_note:
                    warnings_out.append(f"STL {name}: {stl_note}")
            except Exception as exc:
                stl_note = f"STL skipped: {exc}"
                warnings_out.append(f"{name}: {stl_note}")
        else:
            stl_note = f"STL requires n >= 2*period={2 * period}; observed n={n}."
        summary_rows.append({
            "variable": name, "n": n, "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)), "rms": float(np.sqrt(np.mean(np.square(values)))),
            "median": float(np.median(values)), "iqr": float(np.quantile(values, .75) - np.quantile(values, .25)),
            "min": float(np.min(values)), "max": float(np.max(values)), "skew": skew,
            "kurtosis": kurt, "spectral_entropy": entropy, "dominant_frequency": peak,
            "trend_strength": trend_strength, "seasonal_strength": seasonal_strength,
            "stl_note": stl_note,
            **basic_metrics(values), **power_summary,
        })

        # Unit-root and stationarity tests have different null hypotheses; neither is proof.
        if constant_flags[i]:
            for test_name in ("ADF", "KPSS", "LjungBox"):
                test_rows.append({"variable": name, "test": test_name, "statistic": np.nan,
                                  "pvalue": np.nan, "n": n,
                                  "note": "Skipped: constant or non-finite series; test is undefined."})
        else:
            try:
                with pywarnings.catch_warnings(record=True) as caught:
                    pywarnings.simplefilter("always")
                    result = adfuller(values, autolag="AIC")
                note = "H0: unit root; rejection is evidence against a unit root, not proof of stationarity."
                if caught:
                    note += " " + "; ".join(str(item.message) for item in caught)
                test_rows.append({"variable": name, "test": "ADF", "statistic": float(result[0]),
                                  "pvalue": float(result[1]), "n": n, "note": note})
            except Exception as exc:
                test_rows.append({"variable": name, "test": "ADF", "statistic": np.nan,
                                  "pvalue": np.nan, "n": n, "note": f"Skipped: {exc}"})
            try:
                with pywarnings.catch_warnings(record=True) as caught:
                    pywarnings.simplefilter("always")
                    result = kpss(values, regression="c", nlags="auto")
                note = "H0: level-stationary; non-rejection is not proof of stationarity."
                if caught:
                    note += " " + "; ".join(str(item.message) for item in caught)
                test_rows.append({"variable": name, "test": "KPSS", "statistic": float(result[0]),
                                  "pvalue": float(result[1]), "n": n, "note": note})
            except Exception as exc:
                test_rows.append({"variable": name, "test": "KPSS", "statistic": np.nan,
                                  "pvalue": np.nan, "n": n, "note": f"Skipped: {exc}"})
            lb_lag = min(max_lag, max(1, n // 5), n - 1)
            try:
                lb = acorr_ljungbox(values, lags=[lb_lag], return_df=True)
                test_rows.append({"variable": name, "test": "LjungBox", "statistic": float(lb["lb_stat"].iloc[0]),
                                  "pvalue": float(lb["lb_pvalue"].iloc[0]), "n": n,
                                  "note": f"Raw series at lag {lb_lag}; H0: no autocorrelation through tested lag."})
            except Exception as exc:
                test_rows.append({"variable": name, "test": "LjungBox", "statistic": np.nan,
                                  "pvalue": np.nan, "n": n, "note": f"Skipped: {exc}"})

        acf_limit = min(max_lag, max(1, n // 2 - 1), n - 1)
        try:
            acf_values = acf(values, nlags=min(max_lag, n - 1), fft=True, missing="raise")
            pacf_values = pacf(values, nlags=acf_limit, method="ywm")
            for k, value in enumerate(acf_values):
                acf_rows.append({"variable": name, "lag": k, "acf": float(value),
                                 "pacf": float(pacf_values[k]) if k < len(pacf_values) else np.nan})
        except Exception as exc:
            warnings_out.append(f"ACF/PACF skipped for {name}: {exc}")

    # Pairwise descriptive matrices.
    signal_frame = pd.DataFrame(matrix_data, columns=names)
    pearson = signal_frame.corr(method="pearson")
    spearman = signal_frame.corr(method="spearman")
    covariance = signal_frame.cov()
    matrices: dict[str, pd.DataFrame] = {
        "pearson": pearson, "spearman": spearman, "covariance": covariance,
    }
    warnings_out.append("Correlation, lagged correlation, partial correlation, and coherence are descriptive associations, not causal evidence.")

    partial = np.full((len(names), len(names)), np.nan, dtype=float)
    cov_values = covariance.to_numpy(dtype=float)
    if len(names) == 1 and not constant_flags[0]:
        partial[0, 0] = 1.0
    elif len(names) > 1:
        rank = int(np.linalg.matrix_rank(cov_values))
        condition = float(np.linalg.cond(cov_values)) if rank == len(names) else float("inf")
        if rank == len(names) and np.isfinite(condition) and condition < 1e12:
            precision = np.linalg.inv(cov_values)
            denom = np.sqrt(np.outer(np.diag(precision), np.diag(precision)))
            with np.errstate(divide="ignore", invalid="ignore"):
                partial = -precision / denom
            np.fill_diagonal(partial, 1.0)
        else:
            warnings_out.append(f"Partial correlation is undefined: covariance matrix is rank-deficient or ill-conditioned (rank={rank}/{len(names)}).")
    matrices["partial"] = _matrix_frame(partial, names)

    if lag >= n:
        raise ValueError(f"lag must be smaller than the number of samples ({n}).")
    lagged = np.full((len(names), len(names)), np.nan, dtype=float)
    lagged_left = matrix_data[: n - lag, :] if lag else matrix_data
    lagged_right = matrix_data[lag:, :] if lag else matrix_data
    lagged_left_constant = np.array([_constant(lagged_left[:, i]) for i in range(len(names))], dtype=bool)
    lagged_right_constant = np.array([_constant(lagged_right[:, i]) for i in range(len(names))], dtype=bool)
    for i in range(len(names)):
        for j in range(len(names)):
            left = lagged_left[:, i]
            right = lagged_right[:, j]
            if not lagged_left_constant[i] and not lagged_right_constant[j]:
                lagged[i, j] = float(np.corrcoef(left, right)[0, 1])
    matrices["lagged"] = _matrix_frame(lagged, names)

    coherence = np.full((len(names), len(names)), np.nan, dtype=float)
    for i in range(len(names)):
        for j in range(i, len(names)):
            if constant_flags[i] or constant_flags[j]:
                continue
            freq, coh = signal.coherence(matrix_data[:, i], matrix_data[:, j], fs=fs,
                                         **spectral_options)
            selected = (freq >= band_low) & (freq <= band_high)
            if selected.any():
                value = float(np.mean(coh[selected]))
                coherence[i, j] = coherence[j, i] = value
    if not np.isfinite(coherence).any():
        warnings_out.append("Coherence is undefined for the selected band or constant signals.")
    matrices["coherence"] = _matrix_frame(coherence, names)

    standardized = np.full_like(matrix_data, np.nan, dtype=float)
    std = np.std(matrix_data, axis=0, ddof=1)
    means = np.mean(matrix_data, axis=0)
    nonconstant = np.isfinite(std) & (std > 0)
    standardized[:, nonconstant] = (matrix_data[:, nonconstant] - means[nonconstant]) / std[nonconstant]
    euclidean = np.full((len(names), len(names)), np.nan, dtype=float)
    for i in range(len(names)):
        for j in range(i, len(names)):
            if nonconstant[i] and nonconstant[j]:
                value = float(np.sqrt(np.mean(np.square(standardized[:, i] - standardized[:, j]))))
                euclidean[i, j] = euclidean[j, i] = value
    matrices["standardized_euclidean"] = _matrix_frame(euclidean, names)

    tables = {
        "summary": pd.DataFrame(summary_rows),
        "band_power": pd.DataFrame(band_rows, columns=["variable", "band", "low_hz", "high_hz", "power", "relative_power"]),
        "windows": window_metrics(data, names, fs, cfg),
        "tests": pd.DataFrame(test_rows, columns=["variable", "test", "statistic", "pvalue", "n", "note"]),
        "acf": pd.DataFrame(acf_rows, columns=["variable", "lag", "acf", "pacf"]),
        "psd": pd.DataFrame(psd_rows, columns=["variable", "frequency", "psd"]),
        "decomposition": pd.DataFrame(decomposition_rows,
                                      columns=["variable", "time", "observed", "trend", "seasonal", "residual"]),
    }
    meta = {
        "parameters": cfg,
        "n": n,
        "variables": names,
        "fs": fs,
        "time_unit": prepared["time_unit"],
        "psd_method": "Welch power spectral density; nperseg constrained to <= n/2.",
        "welch_options": spectral_options,
        "power_rule": "Trapezoidal PSD integral with interpolated band edges within measured frequency support (odd FFT stops below Nyquist); relative power uses integrated total PSD. MAD is unscaled; variance uses ddof=1.",
        "window_rule": "Half-open sample-aligned windows; rounded duration and step; final partial window retained. Time is elapsed from first prepared sample.",
        "coherence_band_hz": [band_low, band_high],
        "lagged_direction": "matrix row is source at t; column is target at t+lag.",
        "standardized_euclidean": "sqrt(mean squared difference after per-channel sample z-score; ddof=1).",
        "partial_method": "inverse sample covariance; undefined when rank-deficient or condition number >= 1e12.",
        "stl_period": period,
        "interpretation": "ADF/KPSS are evidence under their stated nulls, not proofs; association matrices are not causal evidence.",
    }
    return {"tables": tables, "matrices": matrices, "warnings": warnings_out, "meta": meta}


def _validate_order(order, name: str, max_pq: int, max_d: int):
    if not isinstance(order, (tuple, list)) or len(order) != 3:
        raise ValueError(f"{name} must be a 3-item tuple of nonnegative integers.")
    vals = tuple(int(v) for v in order)
    if any(v < 0 for v in vals) or vals[0] > max_pq or vals[1] > max_d or vals[2] > max_pq:
        raise ValueError(f"{name} exceeds supported bounds: p/q <= {max_pq}, d <= {max_d}.")
    return vals


def _validate_seasonal_order(order):
    if not isinstance(order, (tuple, list)) or len(order) != 4:
        raise ValueError("seasonal_order must be a 4-item tuple (P, D, Q, period).")
    vals = tuple(int(v) for v in order)
    if any(v < 0 for v in vals[:3]) or vals[0] > 2 or vals[1] > 1 or vals[2] > 2 or not 2 <= vals[3] <= 366:
        raise ValueError("seasonal_order bounds are P/Q <= 2, D <= 1, and period 2..366.")
    return vals


def _forecast_one(model_name: str, train: np.ndarray, steps: int, period: int,
                  order: tuple[int, int, int], seasonal_order: tuple[int, int, int, int], confidence=.95):
    """Fit one bounded model and return point forecasts, intervals, and diagnostics."""
    if not np.isfinite(train).all():
        raise ValueError("训练序列含未解决的缺失或非有限数值。")
    lower = np.full(steps, np.nan, dtype=float)
    upper = np.full(steps, np.nan, dtype=float)
    fit = None
    notes: list[str] = []
    with pywarnings.catch_warnings(record=True) as caught:
        pywarnings.simplefilter("always")
        if model_name == "Naive":
            predicted = np.full(steps, float(train[-1]), dtype=float)
            notes.append("Naive forecasts have no model-based prediction interval.")
        elif model_name == "SeasonalNaive":
            if len(train) < period:
                raise ValueError(f"SeasonalNaive requires at least one full period ({period} training samples).")
            predicted = np.resize(train[-period:], steps).astype(float)
            notes.append("SeasonalNaive forecasts have no model-based prediction interval.")
        elif model_name == "ETS":
            seasonal = "add" if period >= 2 and len(train) >= 2 * period else None
            if seasonal is None:
                notes.append("ETS used an additive trend without a seasonal component because training data had fewer than two periods.")
            model = ExponentialSmoothing(train, trend="add", seasonal=seasonal,
                                        seasonal_periods=period if seasonal else None,
                                        initialization_method="estimated")
            fit = model.fit(optimized=True, use_brute=False)
            predicted = np.asarray(fit.forecast(steps), dtype=float)
            notes.append("ETS intervals are unavailable in this implementation; bounds are omitted.")
        elif model_name in {"ARIMA", "SARIMA"}:
            seasonal = seasonal_order if model_name == "SARIMA" else (0, 0, 0, 0)
            model = ARIMA(train, order=order, seasonal_order=seasonal,
                          enforce_stationarity=True, enforce_invertibility=True)
            fit = model.fit(method_kwargs={"maxiter": 100})
            prediction = fit.get_forecast(steps=steps)
            frame = prediction.summary_frame(alpha=1 - confidence)
            predicted = np.asarray(frame["mean"], dtype=float)
            lower = np.asarray(frame["mean_ci_lower"], dtype=float)
            upper = np.asarray(frame["mean_ci_upper"], dtype=float)
            converged = getattr(fit, "mle_retvals", {}).get("converged", None)
            if converged is False:
                notes.append("Model optimizer did not converge; forecast and interval require caution.")
        else:
            raise ValueError(f"Unsupported model name: {model_name!r}.")
    if caught:
        notes.extend(str(item.message) for item in caught)
    return predicted, lower, upper, fit, notes


def _score_forecast(actual: np.ndarray, predicted: np.ndarray, train: np.ndarray, period: int):
    valid = np.isfinite(actual) & np.isfinite(predicted)
    if not valid.any():
        return {"n_test": 0, "mae": np.nan, "rmse": np.nan, "smape": np.nan, "mase": np.nan,
                "note": "No non-missing test actuals were available for scoring."}
    y = actual[valid]
    p = predicted[valid]
    err = y - p
    denom = np.abs(y) + np.abs(p)
    usable = denom > 0
    smape = float(np.mean(2.0 * np.abs(err[usable]) / denom[usable]) * 100.0) if usable.any() else np.nan
    note = "sMAPE excludes rows where both actual and predicted are zero." if (~usable).any() else ""
    if len(train) > period:
        scale = float(np.mean(np.abs(train[period:] - train[:-period])))
    else:
        scale = float("nan")
        note = (note + " " if note else "") + f"MASE undefined: fewer than period={period} training comparisons."
    mase = float(np.mean(np.abs(err)) / scale) if np.isfinite(scale) and scale > 0 else np.nan
    if np.isfinite(scale) and scale == 0:
        note = (note + " " if note else "") + "MASE undefined: training seasonal-naive scale is zero."
    return {"n_test": int(valid.sum()), "mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(np.square(err)))), "smape": smape,
            "mase": mase, "note": note.strip()}


def _future_times(last_time, horizon: int, step_seconds: float, time_kind: str):
    if time_kind == "datetime":
        last = pd.Timestamp(last_time)
        return [last + pd.to_timedelta(step_seconds * k, unit="s") for k in range(1, horizon + 1)]
    return [float(last_time) + step_seconds * k for k in range(1, horizon + 1)]


def forecast(prepared, column, config) -> dict[str, Any]:
    """Compare bounded classical forecasts on one time-ordered holdout.

    Fitting uses only the physical-value ``prepared['original']`` branch. The
    training split is filled causally only when requested; test actuals are never
    interpolated or backward-filled.
    """
    if not isinstance(prepared, Mapping) or "original" not in prepared or "meta" not in prepared:
        raise TypeError("prepared must be the dictionary returned by prepare_data().")
    cfg = dict(config or {})
    original = prepared["original"]
    confidence = float(cfg.get("confidence", .95))
    if not np.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("预测区间置信水平须在 0 与 1 之间。")
    names = list(prepared["meta"]["value_cols"])
    if column not in names or column not in original.columns:
        raise ValueError(f"Forecast column {column!r} must be one of the selected signal columns: {names!r}.")
    n = len(original)
    horizon = int(cfg.get("horizon", 12))
    test_fraction = float(cfg.get("test_fraction", 0.2))
    period = int(cfg.get("period", 12))
    order = _validate_order(cfg.get("order", (1, 1, 1)), "order", max_pq=8, max_d=2)
    seasonal_order = _validate_seasonal_order(cfg.get("seasonal_order", (1, 0, 1, 12)))
    model_names = list(cfg.get("models", ["Naive", "SeasonalNaive", "ETS", "ARIMA", "SARIMA"]))
    allowed_models = {"Naive", "SeasonalNaive", "ETS", "ARIMA", "SARIMA"}
    unknown = [name for name in model_names if name not in allowed_models]
    if unknown or not model_names or len(set(model_names)) != len(model_names):
        raise ValueError(f"models must be a nonempty unique list drawn from {sorted(allowed_models)}.")
    missing_policy = cfg.get("missing", "reject")
    if missing_policy not in {"reject", "ffill"}:
        raise ValueError("forecast missing must be 'reject' or 'ffill'.")
    if horizon < 1 or not 0 < test_fraction < 0.5 or not 2 <= period <= 366:
        raise ValueError("horizon must be positive, test_fraction in (0, 0.5), and period in 2..366.")
    if n < 24:
        raise ValueError("Forecasting requires at least 24 prepared time points.")
    raw = pd.to_numeric(original[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    if np.isinf(raw).any():
        raise ValueError("Forecast source contains infinite values.")
    test_n = max(1, int(math.ceil(n * test_fraction)))
    train_n = n - test_n
    if train_n < 16:
        raise ValueError("The training split must contain at least 16 samples.")
    train_raw = raw[:train_n]
    test_actual = raw[train_n:]
    if missing_policy == "reject" and np.isnan(train_raw).any():
        raise ValueError("The original training series contains missing values; choose forecast missing='ffill' explicitly.")
    if missing_policy == "ffill":
        train_series = pd.Series(train_raw).ffill()
        if train_series.isna().any():
            raise ValueError("Causal forward fill cannot resolve leading training gaps; trim or explicitly resolve them.")
        train_values = train_series.to_numpy(dtype=float)
        full_series = pd.Series(raw).ffill()
        if full_series.isna().any():
            raise ValueError("Causal forward fill cannot resolve leading gaps in the full history.")
        full_values = full_series.to_numpy(dtype=float)
    else:
        train_values = train_raw.copy()
        full_values = raw.copy()
    if not np.isfinite(train_values).all():
        raise ValueError("Training values must be finite after the selected missing-value policy.")

    time_col = prepared["meta"]["time_col"]
    time_kind = prepared["meta"]["time_kind"]
    all_times = original[time_col].to_numpy()
    backtest_times = all_times[train_n:]
    future_times = _future_times(all_times[-1], horizon, float(prepared["meta"]["time_step_seconds"]), time_kind)
    rolling_metrics, rolling_predictions = rolling_validation(
        raw, all_times, cfg, model_names, period, order, seasonal_order, confidence)
    metric_rows: list[dict[str, Any]] = []
    backtest_rows: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    warnings_out: list[str] = []
    if not rolling_metrics.empty:
        failed_folds = int((rolling_metrics["status"] == "FAILED").sum())
        if failed_folds:
            warnings_out.append(f"{failed_folds} rolling model/fold fits failed; inspect rolling_metrics notes.")
    missing_test_count = int(np.isnan(test_actual).sum())
    if missing_test_count:
        warnings_out.append(f"Excluded {missing_test_count} missing holdout actuals from forecast scoring.")
        if missing_policy == "reject":
            warnings_out.append("Future models cannot refit through missing holdout values under missing='reject'; choose ffill for a causal full-history refit.")
    mase_scale_note = "MASE denominator is the mean absolute training seasonal difference at the selected period."

    for model_name in model_names:
        try:
            pred, lower, upper, fit, notes = _forecast_one(model_name, train_values, test_n, period, order, seasonal_order, confidence)
            score = _score_forecast(test_actual, pred, train_values, period)
            status = "OK" if score["n_test"] else "NO_VALID_ACTUALS"
            missing_note = f"Excluded {missing_test_count} missing holdout actuals from scoring." if missing_test_count else ""
            message = " ".join(part for part in [score["note"], missing_note, *notes] if part)
            metric_rows.append({"model": model_name, "status": status, "message": message,
                                "n_test_total": int(test_n), **score})
            for j, t in enumerate(backtest_times):
                backtest_rows.append({"model": model_name, "time": t,
                                      "actual": float(test_actual[j]) if np.isfinite(test_actual[j]) else np.nan,
                                      "predicted": float(pred[j]), "lower": float(lower[j]), "upper": float(upper[j])})
            if fit is not None and model_name in {"ARIMA", "SARIMA"}:
                converged = getattr(fit, "mle_retvals", {}).get("converged", None)
                diagnostic_rows.append({"model": model_name, "diagnostic": "convergence",
                                        "statistic": np.nan, "pvalue": np.nan,
                                        "note": f"Optimizer converged={converged!r}."})
                residuals = np.asarray(fit.resid, dtype=float)
                residuals = residuals[np.isfinite(residuals)]
                model_df = sum(order[:1]) + order[2] + seasonal_order[0] + seasonal_order[2] if model_name == "SARIMA" else order[0] + order[2]
                lb_lag = min(max(10, period), max(1, len(residuals) // 5), len(residuals) - 1) if len(residuals) > 1 else 0
                if lb_lag > model_df:
                    lb = acorr_ljungbox(residuals, lags=[lb_lag], model_df=model_df, return_df=True)
                    diagnostic_rows.append({"model": model_name, "diagnostic": "residual LjungBox",
                                            "statistic": float(lb["lb_stat"].iloc[0]),
                                            "pvalue": float(lb["lb_pvalue"].iloc[0]),
                                            "note": f"Training residuals, lag={lb_lag}, model_df={model_df}; non-rejection is not proof of white noise."})
                else:
                    diagnostic_rows.append({"model": model_name, "diagnostic": "residual LjungBox",
                                            "statistic": np.nan, "pvalue": np.nan,
                                            "note": "Skipped: tested lag does not exceed fitted ARMA degrees of freedom."})
            elif model_name in {"Naive", "SeasonalNaive", "ETS"}:
                diagnostic_rows.append({"model": model_name, "diagnostic": "prediction interval",
                                        "statistic": np.nan, "pvalue": np.nan,
                                        "note": "No model-based interval is supplied; lower and upper are NaN."})
            if notes:
                warnings_out.extend([f"{model_name}: {note}" for note in notes])
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            metric_rows.append({"model": model_name, "status": "FAILED", "message": message,
                                "n_test_total": int(test_n), "n_test": int(np.isfinite(test_actual).sum()),
                                "mae": np.nan, "rmse": np.nan, "smape": np.nan, "mase": np.nan, "note": message})
            warnings_out.append(f"{model_name} holdout fit failed: {message}")
            for t, actual in zip(backtest_times, test_actual):
                backtest_rows.append({"model": model_name, "time": t,
                                      "actual": float(actual) if np.isfinite(actual) else np.nan,
                                      "predicted": np.nan, "lower": np.nan, "upper": np.nan})

        # Refit each selected method to all observed history for future forecasts.
        try:
            pred_full, lower_full, upper_full, fit_full, notes_full = _forecast_one(
                model_name, full_values, horizon, period, order, seasonal_order, confidence
            )
            for t, p, lo, hi in zip(future_times, pred_full, lower_full, upper_full):
                future_rows.append({"model": model_name, "time": t, "predicted": float(p),
                                    "lower": float(lo), "upper": float(hi)})
            if notes_full:
                warnings_out.extend([f"{model_name} full-history fit: {note}" for note in notes_full])
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            warnings_out.append(f"{model_name} full-history fit failed: {message}")
            for t in future_times:
                future_rows.append({"model": model_name, "time": t, "predicted": np.nan,
                                    "lower": np.nan, "upper": np.nan})

    metrics = pd.DataFrame(metric_rows)
    metrics["rank_rmse"] = metrics["rmse"].rank(method="min", ascending=True, na_option="bottom").where(metrics["rmse"].notna()).astype("Int64")
    backtest = pd.DataFrame(backtest_rows, columns=["model", "time", "actual", "predicted", "lower", "upper"])
    future = pd.DataFrame(future_rows, columns=["model", "time", "predicted", "lower", "upper"])
    diagnostics = pd.DataFrame(diagnostic_rows, columns=["model", "diagnostic", "statistic", "pvalue", "note"])
    meta = {
        "parameters": cfg, "column": column, "source_branch": "prepared.original (physical values before exploratory transform/interpolation)",
        "n_total": n, "n_train": train_n, "n_test": test_n, "horizon": horizon,
        "test_fraction_actual": test_n / n, "period": period, "order": order,
        "seasonal_order": seasonal_order, "models": model_names, "missing_policy": missing_policy,
        "time_unit": prepared["time_unit"], "time_step_seconds": prepared["meta"]["time_step_seconds"],
        "mase": mase_scale_note,
        "confidence": confidence,
        "validation": cfg.get("validation", "holdout"),
        "rolling_rule": "Fixed-length preceding training window; half-open row bounds; complete test folds only; missing test actuals excluded, never filled. Overlapping tests remain separate fold observations.",
        "interpretation": "One time-ordered holdout is an exploratory comparison for this series. Choosing a model by this score creates selection optimism; evaluate again on new data. Prediction intervals depend on model assumptions and are not guaranteed to cover future observations.",
    }
    return {"metrics": metrics, "backtest": backtest, "future": future, "diagnostics": diagnostics,
            "rolling_metrics": rolling_metrics, "rolling_predictions": rolling_predictions,
            "warnings": warnings_out, "meta": meta}


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (Path,)):
        return str(value)
    if pd.isna(value):
        return None
    return str(value)


def _json_safe(value):
    """Normalize metadata recursively so results.json is strict JSON."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.ndarray,)):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _version_info():
    import statsmodels
    return {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
            "pandas": pd.__version__, "statsmodels": statsmodels.__version__}


def _unique_output_dir(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = parent / f"PYTSA_results_{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = parent / f"PYTSA_results_{stamp}_{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _excel_safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy a frame and encode datetime values as ISO-8601 for Excel compatibility."""
    safe = frame.copy()

    def as_iso(value):
        if value is None or value is pd.NaT:
            return None
        if isinstance(value, (pd.Timestamp, datetime)):
            return None if pd.isna(value) else value.isoformat()
        return value

    for column in safe.columns:
        series = safe[column]
        if pd.api.types.is_datetime64_any_dtype(series.dtype):
            safe[column] = series.map(as_iso)
        elif series.dtype == object and any(isinstance(value, (pd.Timestamp, datetime)) for value in series.array):
            safe[column] = series.map(as_iso)
    return safe


def export_results(folder, prepared, analysis, forecast_result=None, figures=None, research_path=None) -> dict[str, Any]:
    """Write non-overwriting CSV/XLSX/JSON/offline-HTML outputs in a new folder."""
    destination = _unique_output_dir(Path(folder).expanduser())
    files: list[str] = []
    export_warnings = list(prepared.get("warnings", [])) + list(analysis.get("warnings", []))
    if forecast_result is not None:
        export_warnings.extend(forecast_result.get("warnings", []))
    sheets: list[tuple[str, pd.DataFrame]] = []
    csv_sets: list[tuple[str, pd.DataFrame]] = [
        ("prepared_data.csv", prepared["data"]), ("prepared_original.csv", prepared["original"]),
    ]
    for key, frame in analysis.get("tables", {}).items():
        csv_sets.append((f"analysis_{key}.csv", frame))
        sheets.append((f"A_{key}", frame))
    for key, frame in analysis.get("matrices", {}).items():
        csv_sets.append((f"matrix_{key}.csv", frame.reset_index(names="variable")))
        sheets.append((f"M_{key}", frame.reset_index(names="variable")))
    sheets.extend([("Prepared", prepared["data"]), ("Original", prepared["original"])])
    if forecast_result is not None:
        for key in ("metrics", "backtest", "future", "diagnostics", "rolling_metrics", "rolling_predictions"):
            frame = forecast_result.get(key, pd.DataFrame())
            csv_sets.append((f"forecast_{key}.csv", frame))
            sheets.append((f"F_{key}", frame))

    for filename, frame in csv_sets:
        path = destination / filename
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        files.append(str(path.resolve()))

    workbook_path = destination / "PYTSA_results.xlsx"
    used_sheet_names: set[str] = set()
    skipped_sheets: list[dict[str, str]] = []
    excel_data_row_limit = 1_048_575  # Excel allows 1,048,576 rows including the header.

    def csv_for_sheet(sheet_name: str) -> str:
        if sheet_name.startswith("A_"):
            return f"analysis_{sheet_name[2:]}.csv"
        if sheet_name.startswith("M_"):
            return f"matrix_{sheet_name[2:]}.csv"
        if sheet_name.startswith("F_"):
            return f"forecast_{sheet_name[2:]}.csv"
        return "prepared_data.csv" if sheet_name == "Prepared" else "prepared_original.csv"

    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        for proposed_name, frame in sheets:
            safe_name = re.sub(r"[^A-Za-z0-9_]", "_", proposed_name)[:31] or "Sheet"
            if len(frame) > excel_data_row_limit:
                csv_name = csv_for_sheet(proposed_name)
                message = (f"Skipped XLSX sheet {safe_name!r}: {len(frame)} data rows exceed Excel's "
                           f"1,048,575-row data limit. The complete table is in {csv_name}.")
                export_warnings.append(message)
                skipped_sheets.append({"sheet": safe_name, "csv": csv_name, "message": message})
                continue
            base = safe_name
            ix = 1
            while safe_name in used_sheet_names:
                suffix = f"_{ix}"
                safe_name = base[:31 - len(suffix)] + suffix
                ix += 1
            used_sheet_names.add(safe_name)
            # Keep datetime zones/offsets as ISO text; Excel cannot store tz-aware datetimes.
            _excel_safe_frame(frame).to_excel(writer, sheet_name=safe_name, index=False)
        if skipped_sheets:
            note_frame = pd.DataFrame(skipped_sheets)
            note_frame.to_excel(writer, sheet_name="Export notes", index=False)
    files.append(str(workbook_path.resolve()))

    research = None
    research_file = Path(research_path).expanduser() if research_path else Path(__file__).with_name("research_notes.json")
    if research_file.is_file():
        try:
            research = json.loads(research_file.read_text(encoding="utf-8"))
        except Exception as exc:
            export_warnings.append(f"Could not read research notes {research_file}: {exc}")
    else:
        export_warnings.append(f"Research notes file was not found: {research_file}")

    figure_files = []
    if figures:
        image_dir = destination / "figures"
        image_dir.mkdir()
        for idx, (label, figure) in enumerate(figures.items(), start=1):
            filename = f"figure_{idx:02d}.png"
            image_path = image_dir / filename
            figure.savefig(image_path, dpi=160, bbox_inches="tight")
            figure_files.append({"label": str(label), "path": str(image_path.relative_to(destination)).replace(os.sep, "/")})
            files.append(str(image_path.resolve()))

    payload = {
        "application": "PYTSA",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": prepared.get("meta", {}).get("status", "REAL_OR_USER_PROVIDED"),
        "versions": _version_info(),
        "prepared": prepared.get("meta", {}),
        "analysis": analysis.get("meta", {}),
        "forecast": forecast_result.get("meta", {}) if forecast_result is not None else None,
        "warnings": export_warnings,
        "research_notes_original": research,
        "figures": figure_files,
        "outputs": [Path(p).name for p in files] + ["results.json", "report.html"],
        "result_tables": {
            "analysis": {key: frame.to_dict(orient="records") for key, frame in analysis.get("tables", {}).items()},
            "forecast": {key: forecast_result[key].to_dict(orient="records")
                         for key in ("metrics", "backtest", "future", "diagnostics", "rolling_metrics", "rolling_predictions")
                         if forecast_result is not None and key in forecast_result},
        },
    }
    json_path = destination / "results.json"
    json_path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False,
                                    default=_json_default), encoding="utf-8")
    files.append(str(json_path.resolve()))

    sections = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<title>PYTSA offline report</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#19232d}h1,h2{color:#153d5b}table{border-collapse:collapse;margin:1rem 0;display:block;overflow-x:auto}th,td{border:1px solid #cbd5df;padding:.4rem .6rem;text-align:left;white-space:nowrap}th{background:#edf3f7}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{background:#fff7e6;padding:.5rem 1rem;margin:.4rem 0}.figure{max-width:100%;height:auto}</style></head><body>",
        "<h1>PYTSA time-series report</h1>",
        f"<p>Data status: <strong>{html.escape(str(payload['status']))}</strong>. Sampling rate: {html.escape(str(prepared.get('fs')))} Hz; time unit: {html.escape(str(prepared.get('time_unit')))}.</p>",
        "<p>Association matrices describe dependence and do not establish causality. ADF/KPSS results are evidence under their stated null hypotheses, not proof. A single holdout ranking is exploratory; choosing a model from it creates selection optimism. Prediction intervals depend on model assumptions and do not guarantee future coverage.</p>",
    ]
    def append_table(title: str, frame: pd.DataFrame, csv_name: str, index: bool = False):
        shown = frame.head(200) if len(frame) > 200 else frame
        sections.append(f"<h2>{html.escape(title)}</h2>")
        if len(frame) > 200:
            xlsx_note = ("The full table is also in the XLSX workbook."
                        if len(frame) <= excel_data_row_limit else "This table exceeds Excel's row limit and is omitted from XLSX.")
            sections.append(
                f"<p>Showing the first 200 of {len(frame)} rows. "
                f"<a href=\"{html.escape(csv_name, quote=True)}\">Complete table CSV</a>. {xlsx_note}</p>"
            )
        sections.append(shown.to_html(index=index, escape=True, border=0))

    for label, frame in analysis.get("tables", {}).items():
        append_table(str(label), frame, f"analysis_{label}.csv")
    for label, frame in analysis.get("matrices", {}).items():
        append_table(f"{label} matrix", frame, f"matrix_{label}.csv", index=True)
    if forecast_result is not None:
        for label in ("metrics", "diagnostics", "rolling_metrics", "rolling_predictions"):
            frame = forecast_result.get(label, pd.DataFrame())
            append_table(f"Forecast {label}", frame, f"forecast_{label}.csv")
    for figure in figure_files:
        sections.append(f"<h2>{html.escape(figure['label'])}</h2><img class=\"figure\" src=\"{html.escape(figure['path'], quote=True)}\" alt=\"{html.escape(figure['label'], quote=True)}\">")
    if research is not None:
        sections.append("<h2>Research notes</h2>")
        sections.append(f"<p>{html.escape(str(research.get('title', '')))} — {html.escape(str(research.get('review_type', '')))}</p>")
        sections.append("<ul>")
        for ref in research.get("references", []):
            title = html.escape(str(ref.get("title", ref.get("id", "Reference"))))
            url = str(ref.get("url", ""))
            if url.startswith("https://"):
                sections.append(f"<li><a href=\"{html.escape(url, quote=True)}\">{title}</a> ({html.escape(str(ref.get('year', '')))}): {html.escape(str(ref.get('supports', '')))}</li>")
            else:
                sections.append(f"<li>{title}: {html.escape(str(ref.get('supports', '')))}</li>")
        sections.append("</ul><p>Full original research metadata is embedded in results.json.</p>")
    if export_warnings:
        sections.append("<h2>Warnings</h2>")
        sections.extend(f"<p class=\"warning\">{html.escape(str(item))}</p>" for item in export_warnings)
    sections.append("</body></html>")
    html_path = destination / "report.html"
    html_path.write_text("\n".join(sections), encoding="utf-8")
    files.append(str(html_path.resolve()))
    return {"files": files, "folder": str(destination.resolve())}


__all__ = ["load_table", "demo_data", "prepare_data", "analyze", "forecast", "export_results"]
