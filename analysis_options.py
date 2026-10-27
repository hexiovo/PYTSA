"""Shared, validated numerical options for the offline analysis pipelines."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import signal


def numeric_seconds(values, unit="s"):
    scales = {"s": 1.0, "ms": .001, "us": .000001}
    if unit not in scales:
        raise ValueError("时间单位必须为 s、ms 或 us。")
    return np.asarray(values, dtype=float) * scales[unit]


def datetime_utc(values, timezone="UTC", errors="raise"):
    """Localize naive timestamps explicitly; reject ambiguous/nonexistent times."""
    def parse(value):
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError):
            if errors == "coerce":
                return pd.NaT
            raise
        if pd.isna(stamp):
            return pd.NaT
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
        return stamp.tz_convert("UTC")
    try:
        return pd.Series([parse(value) for value in values], index=getattr(values, "index", None),
                         dtype="datetime64[ns, UTC]")
    except Exception as exc:
        raise ValueError(f"日期时间或时区无效（{timezone}）：{exc}") from exc


def validate_gap_limits(values, fs, config):
    seconds = config.get("max_gap_seconds")
    samples = config.get("max_gap_samples")
    if seconds is not None and (not np.isfinite(float(seconds)) or float(seconds) <= 0):
        raise ValueError("最大填补时长必须为正数或留空。")
    if samples is not None and (int(samples) != samples or int(samples) < 1):
        raise ValueError("最大连续缺失点数必须为正整数或留空。")
    for name in values.columns:
        missing = values[name].isna().to_numpy()
        edges = np.diff(np.r_[False, missing, False].astype(int))
        lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
        longest = int(lengths.max()) if len(lengths) else 0
        if ((samples is not None and longest > int(samples)) or
                (seconds is not None and longest / fs > float(seconds) + 1e-12)):
            raise ValueError(f"{name} 连续缺失 {longest} 点（{longest / fs:g} 秒），超过填补上限。")


def filter_values(values, fs, config):
    method = config.get("filter_type", "none")
    if method == "none":
        return values
    if method not in {"lowpass", "highpass", "bandpass"}:
        raise ValueError("滤波类型必须为 none、lowpass、highpass 或 bandpass。")
    order = config.get("filter_order", 4)
    if int(order) != order or not 1 <= int(order) <= 12:
        raise ValueError("滤波阶数必须为 1–12 的整数。")
    low, high = config.get("filter_low"), config.get("filter_high")
    cutoff = low if method == "highpass" else high
    if method == "bandpass":
        if low is None or high is None or not 0 < float(low) < float(high) < fs / 2:
            raise ValueError("带通频率须满足 0 < 下限 < 上限 < fs/2。")
        cutoff = [float(low), float(high)]
    elif cutoff is None or not np.isfinite(float(cutoff)) or not 0 < float(cutoff) < fs / 2:
        raise ValueError("滤波截止频率须大于 0 且小于 fs/2。")
    phase = config.get("filter_phase", "zero")
    if phase not in {"zero", "causal"}:
        raise ValueError("滤波相位须为 zero 或 causal。")
    sos = signal.butter(int(order), cutoff, btype=method, fs=fs, output="sos")
    try:
        return (signal.sosfiltfilt(sos, values, axis=0) if phase == "zero"
                else signal.sosfilt(sos, values, axis=0))
    except ValueError as exc:
        raise ValueError(f"滤波失败；零相位滤波需要足够长的记录：{exc}") from exc


def welch_options(config, n):
    requested = config.get("nperseg", 256)
    if int(requested) != requested or int(requested) < 2:
        raise ValueError("Welch 分段长度必须为至少 2 的整数。")
    seg = max(2, min(int(requested), n // 2))
    overlap = float(config.get("welch_overlap", .5))
    if not np.isfinite(overlap) or not 0 <= overlap < 1:
        raise ValueError("Welch 重叠比例必须在 [0,1) 内。")
    nfft = config.get("nfft")
    if nfft is not None and (int(nfft) != nfft or int(nfft) < seg or int(nfft) > 1048576):
        raise ValueError(f"FFT 点数须为 {seg}–1048576 的整数，或留空。")
    window = config.get("welch_window", "hann")
    if window not in {"hann", "hamming", "blackman", "boxcar"}:
        raise ValueError("不支持的 Welch 窗函数。")
    detrend = config.get("welch_detrend", "constant")
    if detrend not in {"constant", "linear", "none"}:
        raise ValueError("频谱去趋势须为 constant、linear 或 none。")
    return dict(nperseg=seg, noverlap=int(seg * overlap), nfft=None if nfft is None else int(nfft),
                window=window, detrend=False if detrend == "none" else detrend)


def basic_metrics(values):
    q1, median, q3 = np.quantile(values, [.25, .5, .75])
    return {"variance": float(np.var(values, ddof=1)) if len(values) > 1 else np.nan,
            "peak_to_peak": float(np.ptp(values)), "q1": float(q1), "q3": float(q3),
            "mad": float(np.median(np.abs(values - median)))}


def integrate_band(freq, power, low, high):
    if not np.isfinite([low, high]).all() or not 0 <= low < high:
        raise ValueError("功率频带须为非负有限频率，且下限小于上限。")
    # Odd FFT sizes have no bin exactly at Nyquist. Integrate only the
    # measured PSD frequency support rather than extrapolating its tail.
    if low >= freq[-1]:
        return 0.
    high = min(high, float(freq[-1]))
    inside = (freq > low) & (freq < high)
    x = np.r_[low, freq[inside], high]
    y = np.interp(x, freq, power)
    return float(np.trapezoid(y, x))


def spectral_metrics(freq, power, low, high):
    total = float(np.trapezoid(power, freq))
    band = integrate_band(freq, power, low, high)
    return {"total_power": total, "band_power": band,
            "relative_band_power": band / total if total > 0 else np.nan,
            "spectral_centroid": float(np.trapezoid(freq * power, freq) / total) if total > 0 else np.nan}


def frequency_bands(config, nyquist):
    """Named bands are explicit and domain-neutral: name:low:high;name:low:high."""
    source = config.get("power_bands", "")
    if not source:
        return []
    rows = []
    for item in source.split(";"):
        try:
            name, lower, upper = item.strip().split(":")
            low, high = float(lower), float(upper)
        except (ValueError, AttributeError) as exc:
            raise ValueError("功率频段格式应为 名称:下限:上限；多段用英文分号分隔。") from exc
        tolerance = 64 * np.finfo(float).eps * max(1., abs(nyquist))
        if not name or name in [row[0] for row in rows] or not 0 <= low < high <= nyquist + tolerance:
            raise ValueError("功率频段名称须唯一，且满足 0 <= 下限 < 上限 <= fs/2。")
        rows.append((name, low, min(high, nyquist)))
    if len(rows) > 64:
        raise ValueError("功率频段最多 64 个。")
    return rows


def window_metrics(data, names, fs, config):
    duration = config.get("window_seconds")
    if duration is None:
        return pd.DataFrame()
    step_seconds = window_step(config, duration)
    for value in (duration, step_seconds):
        if not np.isfinite(float(value)) or float(value) * fs < 1:
            raise ValueError("窗口长度和步长须至少为一个采样间隔。")
    width, step = int(round(float(duration) * fs)), int(round(float(step_seconds) * fs))
    if ((len(data) + step - 1) // step) * len(names) > 100000:
        raise ValueError("窗口结果超过 100000 行，请增大步长。")
    rows = []
    for start in range(0, len(data), step):
        stop = min(start + width, len(data))
        for name in names:
            y = data[name].to_numpy(dtype=float)[start:stop]
            row = dict(variable=name, start_seconds=start / fs, end_seconds=stop / fs, n=len(y),
                       status="OK" if stop - start == width else "PARTIAL",
                       mean=float(np.mean(y)), std=float(np.std(y, ddof=1)) if len(y) > 1 else np.nan,
                       rms=float(np.sqrt(np.mean(y*y))), median=float(np.median(y)), **basic_metrics(y))
            if len(y) >= 4:
                f, p = signal.welch(y, fs=fs, **welch_options(config, len(y)))
                row.update(spectral_metrics(f, p, 0., float(f[-1])))
                low, high = float(config.get("band_low", 0)), config.get("band_high")
                high = fs/2 if high is None else min(float(high), fs/2)
                row.update(spectral_metrics(f, p, low, high))
            rows.append(row)
    return pd.DataFrame(rows)


def window_step(config, duration):
    overlap = float(config.get("window_overlap", 0))
    if not np.isfinite(overlap) or not 0 <= overlap < 1:
        raise ValueError("统计窗口重叠比例须在 [0,1) 内。")
    step = config.get("window_step_seconds")
    return float(duration) * (1 - overlap) if step is None else float(step)


def rolling_validation(raw, times, config, models, period, order, seasonal_order, confidence):
    from core import _forecast_one, _score_forecast
    columns = ["model", "fold", "train_start", "train_end", "test_start", "test_end",
               "status", "n_test", "mae", "rmse", "smape", "mase", "note"]
    pred_columns = ["model", "fold", "time", "actual", "predicted", "lower", "upper"]
    if config.get("validation", "holdout") == "holdout":
        return pd.DataFrame(columns=columns), pd.DataFrame(columns=pred_columns)
    if config.get("validation") != "rolling":
        raise ValueError("验证方式须为 holdout 或 rolling。")
    def integer(key, default, minimum):
        value = config.get(key, default)
        if int(value) != value or int(value) < minimum:
            raise ValueError(f"{key} 必须为至少 {minimum} 的整数。")
        return int(value)
    width = integer("rolling_train", max(16, len(raw)//2), 16)
    horizon = integer("rolling_horizon", config.get("horizon", 12), 1)
    step = integer("rolling_step", horizon, 1)
    if width + horizon > len(raw):
        raise ValueError("滚动训练窗口加验证长度超过记录长度。")
    origins = range(width, len(raw) - horizon + 1, step)
    if len(origins) * len(models) > 500:
        raise ValueError("滚动验证最多允许 500 次拟合，请增大步长。")
    if len(origins) * len(models) * horizon > 2000000:
        raise ValueError("滚动预测超过 2000000 行，请缩短每折验证长度或增大步长。")
    rows, predictions = [], []
    for fold, origin in enumerate(origins, 1):
        train = raw[origin-width:origin].copy()
        if config.get("missing", "reject") == "ffill":
            train = pd.Series(train).ffill().to_numpy()
        actual = raw[origin:origin+horizon]
        for model in models:
            row = dict(model=model, fold=fold, train_start=origin-width, train_end=origin,
                       test_start=origin, test_end=origin+horizon)
            try:
                if not np.isfinite(train).all():
                    raise ValueError("训练窗口包含未解决缺失值（窗口首部不可前向填补）。")
                pred, lo, hi, _, notes = _forecast_one(model, train, horizon, period, order, seasonal_order, confidence)
                score = _score_forecast(actual, pred, train, period)
                row.update(score, status="OK" if score["n_test"] else "NO_VALID_ACTUALS")
                row["note"] = " ".join([score["note"], *notes]).strip()
            except Exception as exc:
                row.update(status="FAILED", n_test=0, mae=np.nan, rmse=np.nan, smape=np.nan, mase=np.nan, note=str(exc))
                pred = lo = hi = np.full(horizon, np.nan)
            rows.append(row)
            for t, y, p, lower, upper in zip(times[origin:origin+horizon], actual, pred, lo, hi):
                predictions.append(dict(model=model, fold=fold, time=t, actual=y, predicted=p, lower=lower, upper=upper))
    return pd.DataFrame(rows, columns=columns), pd.DataFrame(predictions, columns=pred_columns)
