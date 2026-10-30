"""Shared, validated numerical options for the offline analysis pipelines."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import ndimage, signal


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


def apply_signal_preprocessing(values, fs, config):
    """Apply opt-in exploratory preprocessing without changing physical inputs.

    The operation order is detrending, Hampel despiking, smoothing, notch,
    then optional wavelet denoising. ``values`` must be finite because gap
    handling belongs to the caller and must be explicit.
    """
    array = np.asarray(values, dtype=float)
    if array.ndim == 1:
        array = array[:, None]
        was_1d = True
    elif array.ndim == 2:
        was_1d = False
    else:
        raise ValueError("预处理输入必须是一维或二维数值数组。")
    if not len(array) or not np.isfinite(array).all():
        raise ValueError("预处理要求非空且没有缺失值的信号；请先显式处理缺失值。")
    fs = float(fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("采样率 fs 必须为有限正数。")
    cfg = dict(config or {})
    out = array.copy()
    steps = []
    details = {}

    detrend = cfg.get("preprocess_detrend", "none")
    if detrend not in {"none", "constant", "linear"}:
        raise ValueError("预处理去趋势须为 none、constant 或 linear。")
    if detrend != "none":
        out = signal.detrend(out, axis=0, type=detrend)
        steps.append(f"detrend:{detrend}")

    hampel_enabled = cfg.get("hampel_enabled", False)
    if isinstance(hampel_enabled, str):
        if hampel_enabled not in {"on", "off"}:
            raise ValueError("Hampel 去尖峰选项须为 on 或 off。")
        hampel_enabled = hampel_enabled == "on"
    replacement_counts = [0] * out.shape[1]
    if hampel_enabled:
        window = cfg.get("hampel_window", 7)
        sigma = float(cfg.get("hampel_sigma", 3.0))
        if int(window) != window or int(window) < 3 or int(window) % 2 == 0 or int(window) > len(out):
            raise ValueError("Hampel 窗口必须为不超过记录长度的奇数，且至少为 3。")
        if not np.isfinite(sigma) or sigma <= 0:
            raise ValueError("Hampel 阈值倍数必须为有限正数。")
        window = int(window)
        for column in range(out.shape[1]):
            values_1d = out[:, column]
            local_median = ndimage.vectorized_filter(
                values_1d, np.median, size=window, axes=(0,), mode="nearest",
                batch_memory=64 * 1024 * 1024)

            def window_mad(samples, *, axis):
                median = np.median(samples, axis=axis, keepdims=True)
                return np.median(np.abs(samples - median), axis=axis)

            local_mad = ndimage.vectorized_filter(
                values_1d, window_mad, size=window, axes=(0,), mode="nearest",
                batch_memory=64 * 1024 * 1024)
            threshold = sigma * 1.4826 * local_mad
            outliers = (np.abs(values_1d - local_median) > threshold) & (local_mad > 0)
            # A zero local MAD is common in quantized/flat regions; deviations
            # from that local constant are still clear spikes.
            outliers |= (local_mad == 0) & (values_1d != local_median)
            replacement_counts[column] = int(np.count_nonzero(outliers))
            out[outliers, column] = local_median[outliers]
        steps.append("hampel")
        details["hampel"] = {"window_samples": window, "threshold_mad": sigma,
                             "replaced_by_channel": replacement_counts,
                             "replaced_total": int(sum(replacement_counts))}
        if sum(replacement_counts):
            details.setdefault("warnings", []).append(
                f"Hampel replaced {sum(replacement_counts)} local outliers with the local window median.")

    smoothing = cfg.get("smoothing", "none")
    if smoothing not in {"none", "median", "savgol"}:
        raise ValueError("平滑方法须为 none、median 或 savgol。")
    if smoothing != "none":
        window = cfg.get("smoothing_window", 5)
        if int(window) != window or int(window) < 3 or int(window) % 2 == 0 or int(window) > len(out):
            raise ValueError("平滑窗口必须为不超过记录长度的奇数，且至少为 3。")
        window = int(window)
        if smoothing == "median":
            out = ndimage.median_filter(out, size=(window, 1), mode="nearest")
        else:
            order = cfg.get("savgol_polyorder", 2)
            if int(order) != order or int(order) < 0 or int(order) >= window:
                raise ValueError("Savgol 多项式阶数须为非负整数且小于窗口长度。")
            out = signal.savgol_filter(out, window_length=window, polyorder=int(order), axis=0,
                                       mode="interp")
            details["savgol_polyorder"] = int(order)
        steps.append(f"smoothing:{smoothing}")
        details["smoothing"] = {"window_samples": window}

    notch_frequency = cfg.get("notch_frequency")
    if notch_frequency not in (None, ""):
        frequency = float(notch_frequency)
        quality = float(cfg.get("notch_q", 30.0))
        if not np.isfinite(frequency) or not 0 < frequency < fs / 2:
            raise ValueError("陷波频率须大于 0 且小于 Nyquist 频率 fs/2。")
        if not np.isfinite(quality) or quality <= 0:
            raise ValueError("陷波品质因数 Q 必须为有限正数。")
        b, a = signal.iirnotch(frequency, quality, fs=fs)
        try:
            out = signal.filtfilt(b, a, out, axis=0)
        except ValueError as exc:
            raise ValueError(f"陷波滤波失败；记录长度不足：{exc}") from exc
        steps.append("notch")
        details["notch"] = {"frequency_hz": frequency, "q": quality}

    wavelet_enabled = cfg.get("wavelet_enabled", False)
    if isinstance(wavelet_enabled, str):
        if wavelet_enabled not in {"on", "off"}:
            raise ValueError("小波阈值去噪选项须为 on 或 off。")
        wavelet_enabled = wavelet_enabled == "on"
    if wavelet_enabled:
        try:
            import pywt
        except ImportError as exc:
            raise ImportError("启用小波去噪需要 PyWavelets；请安装 PyWavelets。") from exc
        wavelet_name = str(cfg.get("wavelet_name", "db4"))
        if wavelet_name not in pywt.wavelist(kind="discrete"):
            raise ValueError(f"未知离散小波：{wavelet_name!r}。")
        thresholds = []
        denoised = np.empty_like(out)
        for column in range(out.shape[1]):
            coeffs = pywt.wavedec(out[:, column], wavelet_name, mode="symmetric")
            finest = np.asarray(coeffs[-1], dtype=float)
            sigma = float(np.median(np.abs(finest - np.median(finest))) / 0.6744897501960817)
            threshold = sigma * math.sqrt(2.0 * math.log(max(len(out), 2)))
            shrunk = ([coeffs[0]] + [pywt.threshold(c, threshold, mode="soft") for c in coeffs[1:]]
                      if threshold > 0 else coeffs)
            denoised[:, column] = pywt.waverec(shrunk, wavelet_name, mode="symmetric")[:len(out)]
            thresholds.append(float(threshold))
        out = denoised
        steps.append("wavelet:soft")
        details["wavelet"] = {"name": wavelet_name, "threshold_by_channel": thresholds,
                              "threshold_rule": "universal soft threshold from finest-detail MAD"}

    info = {"steps": steps, "details": details,
            "rule": "Applied only to the exploratory prepared data branch; original physical measurements remain unchanged."}
    result = out[:, 0] if was_1d else out
    return result, info


def time_domain_metrics(values, fs):
    """Return compact time-domain descriptors with explicit degenerate rules."""
    x = np.asarray(values, dtype=float).reshape(-1)
    fs = float(fs)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError("采样率 fs 必须为有限正数。")
    if not np.isfinite(x).all():
        raise ValueError("时域特征要求有限信号值。")
    n = len(x)
    activity = float(np.var(x, ddof=1)) if n >= 2 else np.nan
    mobility = complexity = np.nan
    if n >= 3:
        var_x = float(np.var(x, ddof=1))
        first = np.diff(x) * fs
        var_first = float(np.var(first, ddof=1))
        if var_x > 0:
            mobility = math.sqrt(var_first / var_x)
            if n >= 4 and var_first > 0:
                second = np.diff(first) * fs
                var_second = float(np.var(second, ddof=1))
                complexity = math.sqrt(var_second / var_first) / mobility if mobility > 0 else np.nan

    line_length = float(np.sum(np.abs(np.diff(x)))) if n >= 2 else np.nan
    zero_crossing_rate = np.nan
    if n >= 2:
        centered = x - np.mean(x)
        signs = np.sign(centered)
        nonzero_signs = signs[signs != 0]
        crossings = int(np.count_nonzero(nonzero_signs[1:] != nonzero_signs[:-1]))
        zero_crossing_rate = float(crossings / ((n - 1) / fs))
    return {"hjorth_activity": activity, "hjorth_mobility": mobility,
            "hjorth_complexity": complexity, "line_length": line_length,
            "zero_crossing_rate": zero_crossing_rate}


def permutation_entropy(values, order=3, delay=1):
    """Normalized Bandt-Pompe entropy in [0, 1], with stable tie ordering."""
    x = np.asarray(values, dtype=float).reshape(-1)
    if int(order) != order or order < 2 or int(delay) != delay or delay < 1:
        raise ValueError("排列熵 order 须至少为 2，delay 须为正整数。")
    order, delay = int(order), int(delay)
    if not np.isfinite(x).all():
        raise ValueError("排列熵要求有限信号值。")
    span = (order - 1) * delay + 1
    if len(x) < span:
        return float("nan")
    count = len(x) - span + 1
    patterns = np.column_stack([x[index * delay:index * delay + count] for index in range(order)])
    # Stable sorting makes equal-valued samples retain their temporal order.
    ordinal = np.argsort(patterns, axis=1, kind="stable")
    _, counts = np.unique(ordinal, axis=0, return_counts=True)
    probabilities = counts / counts.sum()
    entropy = -float(np.sum(probabilities * np.log(probabilities)))
    return float(entropy / math.log(math.factorial(order)))


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
