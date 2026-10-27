"""Offline EDA and experiment summaries. No device interfaces or diagnostic labels."""
from __future__ import annotations

import warnings
from importlib.metadata import version
import numpy as np
import pandas as pd
from analysis_options import numeric_seconds, datetime_utc, window_step

PEAK_COLUMNS = ['onset_seconds', 'peak_seconds', 'amplitude', 'rise_seconds',
                'recovery50_seconds', 'segment']
EVENT_COLUMNS = ['event_time', 'label', 'baseline_mean', 'response_mean', 'delta_mean',
                 'scr_count', 'max_scr_amplitude', 'latency_seconds', 'status',
                 'baseline_reference', 'corrected_response_mean', 'response_auc', 'nonresponse']


def demo_eda():
    """SYNTHETIC_ONLY; seed 20261026, 120 s at 10 Hz, four Bateman responses."""
    rng = np.random.default_rng(20261026)
    fs = 10
    t = np.arange(120 * fs) / fs
    y = 2 + .001 * t + .025 * np.sin(2 * np.pi * .01 * t)
    event = np.full(len(t), '', dtype=object)
    for j, at in enumerate([15., 45., 75., 105.]):
        elapsed = np.maximum(t - at - 1, 0)
        response = np.exp(-elapsed / 3) - np.exp(-elapsed / .6)
        y += (.28 + .04 * (j % 2)) * response
        event[int(at * fs)] = ['A', 'B'][j % 2]
    y += rng.normal(0, .0015, len(t))
    frame = pd.DataFrame({'time': t, 'EDA_uS': y, 'auxiliary': np.sin(t / 4), 'event': event})
    frame.attrs.update(status='SYNTHETIC_ONLY', generator='experimental.demo_eda',
                       seed=20261026, duration_seconds=120, sampling_rate_hz=10,
                       waveform='tonic trend plus four delayed Bateman responses and Gaussian noise',
                       unit='uS')
    return frame


def _relative_seconds(index):
    if isinstance(index, pd.DatetimeIndex):
        return np.asarray((index - index[0]).total_seconds(), dtype=float)
    a = np.asarray(index, dtype=float)
    return a - a[0]


def markers_to_events(df, marker_col, time_col=None, fs=1., time_kind='auto', time_scale='s', timezone='UTC'):
    """One event per nonempty marker run/change, seconds from recording start."""
    if marker_col not in df.columns:
        raise ValueError('请选择有效事件标记列。')
    if time_col is None:
        if not np.isfinite(fs) or fs <= 0:
            raise ValueError('采样率必须大于0。')
        times = np.arange(len(df)) / fs
    else:
        raw = df[time_col]
        numeric = pd.to_numeric(raw, errors='coerce')
        if time_kind == 'numeric' or (time_kind == 'auto' and numeric.notna().all()):
            times = numeric_seconds(numeric, time_scale)
            if not np.isfinite(times).all():
                raise ValueError('事件时间列包含无效数值。')
            times -= np.min(times)
        else:
            parsed = datetime_utc(raw, timezone)
            if parsed.isna().any():
                raise ValueError('事件日期时间列包含无法解析的值。')
            times = (parsed - parsed.min()).dt.total_seconds().to_numpy()
    out = []
    previous = ''
    for at, value in zip(times, df[marker_col]):
        text = '' if pd.isna(value) else str(value).strip()
        if text.lower() in ('', '0', '0.0', 'nan', 'none', 'false'):
            previous = ''
            continue
        if text != previous:
            out.append({'time': float(at), 'label': text})
        previous = text
    return pd.DataFrame(out, columns=['time', 'label']).sort_values('time', ignore_index=True)


def _peaks(phasic, fs, offset, relative, absolute, segment, nk):
    # NeuroKit can raise on a truly peak-free signal (empty reduction).
    try:
        info = nk.eda_findpeaks(phasic, sampling_rate=fs, method='neurokit', amplitude_min=relative)
    except ValueError as exc:
        from scipy.signal import find_peaks
        if len(find_peaks(phasic)[0]) == 0:
            return []
        raise ValueError(f'SCR检测失败：{exc}') from exc
    peaks = np.asarray(info['SCR_Peaks'], dtype=int)
    onsets = np.asarray(info['SCR_Onsets'], dtype=float)
    rows = []
    # Use the public NeuroKit detector; derive onset-to-peak amplitudes directly.
    for j, (peak, onset) in enumerate(zip(peaks, onsets)):
        if not np.isfinite(onset) or not 0 <= onset < peak:
            continue
        onset = int(onset)
        amplitude = float(phasic[peak] - phasic[onset])
        if amplitude <= 0 or amplitude < absolute:
            continue
        stop = int(peaks[j + 1]) if j + 1 < len(peaks) else len(phasic)
        half = phasic[onset] + amplitude / 2
        crossing = np.flatnonzero(phasic[peak:stop] <= half)
        recovery = float(crossing[0] / fs) if len(crossing) else np.nan
        rows.append({'onset_seconds': offset + onset / fs,
                     'peak_seconds': offset + peak / fs, 'amplitude': amplitude,
                     'rise_seconds': (peak - onset) / fs, 'recovery50_seconds': recovery,
                     'segment': segment})
    return rows


def _window_rows(signals, peaks, start, end, window, fs, step=None):
    rows = []
    time = signals['time'].to_numpy()
    valid = signals['valid'].to_numpy(dtype=bool)
    tonic = signals['tonic'].to_numpy()
    peak_times = peaks['peak_seconds'].to_numpy()
    starts = [start] if window is None else np.arange(start, end, window if step is None else step)
    for at in starts:
        stop = end if window is None else min(at + window, end)
        a, b = np.searchsorted(time, [at - 1e-9, stop - 1e-9])
        good = valid[a:b]
        duration = good.sum() / fs
        pa, pb = np.searchsorted(peak_times, [at, stop])
        selected = peaks.iloc[pa:pb]
        expected = b - a
        status = 'OK' if expected and good.sum() == expected else 'GAP_OR_SHORT_SEGMENT'
        if not expected:
            status = 'EMPTY'
        elif window is not None and stop - at < window - 1e-8 and status == 'OK':
            status = 'PARTIAL'
        scl = tonic[a:b][good]
        x = time[a:b][good]
        slope = float(np.polyfit(x - x[0], scl, 1)[0]) if len(scl) > 1 else np.nan
        rows.append({'start_seconds': at, 'end_seconds': stop,
                     'valid_seconds': duration,
                     'scl_mean': float(np.mean(tonic[a:b][good])) if good.any() else np.nan,
                     'scr_count': len(selected) if good.any() else np.nan,
                     'scr_per_minute': len(selected) / duration * 60 if duration else np.nan,
                     'scr_amplitude_mean': selected['amplitude'].mean(),
                     'scl_std': float(np.std(scl, ddof=1)) if len(scl) > 1 else np.nan,
                     'scl_slope_per_second': slope,
                     'scr_amplitude_median': selected['amplitude'].median(),
                     'scr_amplitude_max': selected['amplitude'].max(),
                     'scr_rise_seconds_mean': selected['rise_seconds'].mean(),
                     'scr_rise_seconds_median': selected['rise_seconds'].median(),
                     'scr_recovery50_seconds_mean': selected['recovery50_seconds'].mean(),
                     'scr_recovery50_seconds_median': selected['recovery50_seconds'].median(),
                     'scr_recovery50_n': int(selected['recovery50_seconds'].notna().sum()),
                     'status': status, 'scope': 'whole' if window is None else 'window'})
    return rows


def _events(signals, peaks, events, cfg, fs, start, end):
    if events is None or events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS), pd.DataFrame(columns=['label', 'n_events', 'n_valid'])
    if not {'time', 'label'}.issubset(events.columns):
        raise ValueError('事件表需要 time（相对记录起点的秒）与 label 两列。')
    ev = events[['time', 'label']].copy()
    ev['time'] = pd.to_numeric(ev['time'], errors='coerce')
    if not np.isfinite(ev['time']).all() or ev['label'].isna().any():
        raise ValueError('事件时间必须为有限秒数，标签不能为空。')
    ev = ev.sort_values('time', kind='stable', ignore_index=True)
    b0, b1, r0, r1 = (cfg[k] for k in ['baseline_start', 'baseline_end', 'response_start', 'response_end'])
    left = ev['time'].to_numpy() + min(b0, r0)
    right = ev['time'].to_numpy() + max(b1, r1)
    overlap = np.zeros(len(ev), dtype=bool)
    if len(ev):
        max_i = 0
        for i in range(1, len(ev)):
            if left[i] < right[max_i] - 1e-9:
                overlap[i] = overlap[max_i] = True
            if right[i] > right[max_i]:
                max_i = i
    t = signals['time'].to_numpy()
    valid = signals['valid'].to_numpy(dtype=bool)
    clean = signals['clean'].to_numpy()
    onset_times = peaks['onset_seconds'].to_numpy()
    rows = []
    for i, event in ev.iterrows():
        at = float(event['time'])
        ba, bb = np.searchsorted(t, [at + b0 - 1e-9, at + b1 - 1e-9])
        ra, rb = np.searchsorted(t, [at + r0 - 1e-9, at + r1 - 1e-9])
        base_valid, response_valid = valid[ba:bb], valid[ra:rb]
        status = []
        if left[i] < start - 1e-8 or right[i] > end + 1e-8:
            status.append('OUT_OF_RANGE')
        if overlap[i]:
            status.append('OVERLAP')
        if not len(base_valid) or not len(response_valid) or not base_valid.all() or not response_valid.all():
            status.append('GAP_OR_INCOMPLETE')
        mean_base = float(np.mean(clean[ba:bb][base_valid])) if base_valid.any() else np.nan
        mean_response = float(np.mean(clean[ra:rb][response_valid])) if response_valid.any() else np.nan
        # Stimulus-related inclusion uses onset latency, not a peak that began before the stimulus.
        pa, pb = np.searchsorted(onset_times, [at + r0, at + r1])
        chosen = peaks.iloc[pa:pb]
        reference = float(np.median(clean[ba:bb][base_valid])) if cfg['baseline_correction'] == 'subtract_median' and base_valid.any() else mean_base
        corrected = clean[ra:rb].copy()
        mode = cfg['baseline_correction']
        if mode in ('subtract_mean', 'subtract_median'):
            corrected -= reference
        elif mode in ('ratio', 'percent'):
            if not np.isfinite(reference) or abs(reference) <= np.finfo(float).eps:
                status.append('ZERO_BASELINE')
                corrected[:] = np.nan
            else:
                corrected = corrected / reference if mode == 'ratio' else (corrected - reference) / reference * 100
        corrected_mean = float(np.mean(corrected)) if not status else np.nan
        auc = float(np.trapezoid(corrected, t[ra:rb])) if not status and len(corrected) >= 2 else np.nan
        rows.append({'event_time': at, 'label': str(event['label']), 'baseline_mean': mean_base,
                     'response_mean': mean_response, 'delta_mean': mean_response - mean_base,
                     'scr_count': len(chosen) if response_valid.any() else np.nan,
                     'max_scr_amplitude': chosen['amplitude'].max(),
                     'latency_seconds': chosen['onset_seconds'].min() - at,
                     'status': '|'.join(status) if status else 'OK',
                     'baseline_reference': reference, 'corrected_response_mean': corrected_mean,
                     'response_auc': auc, 'nonresponse': float(len(chosen) == 0) if not status else np.nan})
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    aggregates = []
    for label, group in frame.groupby('label', sort=False):
        good = group[group['status'] == 'OK']
        aggregates.append({'label': label, 'n_events': len(group), 'n_valid': len(good),
                           'delta_mean': good['delta_mean'].mean(),
                           'scr_count_mean': good['scr_count'].mean(),
                           'max_scr_amplitude_mean': good['max_scr_amplitude'].mean(),
                           'latency_seconds_mean': good['latency_seconds'].mean()})
        aggregates[-1].update(corrected_response_mean=good['corrected_response_mean'].mean(),
                              response_auc_mean=good['response_auc'].mean(),
                              nonresponse_count=int(good['nonresponse'].sum()),
                              nonresponse_fraction=good['nonresponse'].mean())
    return frame, pd.DataFrame(aggregates)


def analyze_eda(prepared, column, config, event_df=None):
    """Compute EDA in physical units, isolating contiguous finite runs.

    Unit is mandatory. Time output always denotes seconds from recording start.
    Event tables describe observations, not automatic stress/emotion diagnoses.
    """
    cfg = {'method': 'highpass', 'clean': 'neurokit', 'cutoff': .05,
           'relative_threshold': .1, 'absolute_threshold': .01, 'window_seconds': 60.,
           'window_step_seconds': None, 'baseline_correction': 'subtract_mean',
           'start_seconds': 0., 'end_seconds': None,
           'baseline_start': -2., 'baseline_end': 0., 'response_start': 1., 'response_end': 5.}
    cfg.update(config)
    source = prepared['original']
    if column not in source:
        raise ValueError('请选择已导入的皮电通道。')
    fs = float(prepared['fs'])
    if not np.isfinite(fs) or fs < 2:
        raise ValueError('皮电模块要求采样率至少2 Hz；低采样率请使用通用分析。')
    unit = cfg.get('unit')
    scales = {'uS': 1., 'μS': 1., 'µS': 1., 'mS': 1000., 'nS': .001, 'a.u.': 1.}
    if unit not in scales:
        raise ValueError('请明确选择皮电单位：uS、mS、nS 或 a.u.；电阻/电压不能直接当皮电导。')
    if cfg['method'] not in ('highpass', 'median') or cfg['clean'] not in ('neurokit', 'none'):
        raise ValueError('不支持的皮电分解或清理方法。')
    numeric_keys = ['cutoff', 'relative_threshold', 'absolute_threshold', 'window_seconds',
                    'start_seconds', 'baseline_start', 'baseline_end', 'response_start', 'response_end']
    for key in numeric_keys:
        cfg[key] = float(cfg[key])
        if not np.isfinite(cfg[key]):
            raise ValueError(f'参数 {key} 必须为有限数值。')
    if not 0 < cfg['cutoff'] < fs / 2:
        raise ValueError('分解截止频率必须大于0且小于Nyquist频率。')
    if not 0 <= cfg['relative_threshold'] <= 1 or cfg['absolute_threshold'] < 0:
        raise ValueError('相对阈值须在0至1之间，绝对阈值不能为负。')
    if cfg['window_seconds'] < 1 / fs:
        raise ValueError('窗口时长不能小于一个采样间隔。')
    step = window_step(cfg, cfg['window_seconds'])
    if not np.isfinite(step) or step < 1 / fs:
        raise ValueError('皮电窗口步长不能小于一个采样间隔。')
    cfg['window_step_seconds'] = step
    if cfg['baseline_correction'] not in ('none', 'subtract_mean', 'subtract_median', 'ratio', 'percent'):
        raise ValueError('不支持的事件基线校正方式。')
    if not cfg['baseline_start'] < cfg['baseline_end'] <= 0 <= cfg['response_start'] < cfg['response_end']:
        raise ValueError('请设置刺激前基线窗（起点<终点<=0）和刺激后响应窗（0<=起点<终点）。')
    time_col = prepared.get('meta', {}).get('time_col')
    axis = source[time_col] if time_col and time_col in source else source.index
    if pd.api.types.is_datetime64_any_dtype(axis):
        time = _relative_seconds(pd.DatetimeIndex(axis))
    else:
        time = _relative_seconds(axis)
    if len(time) < 2 or not np.allclose(np.diff(time), 1 / fs, rtol=1e-3, atol=1e-7):
        raise ValueError('皮电数据必须按时间对齐且等间隔；请先明确重采样。')
    end = time[-1] + 1 / fs if cfg['end_seconds'] is None else float(cfg['end_seconds'])
    start = cfg['start_seconds']
    if not np.isfinite(end) or not 0 <= start < end <= time[-1] + 1 / fs + 1e-7:
        raise ValueError('分析区间必须位于记录范围内，且终点大于起点。')
    if (end - start) / step > 100000:
        raise ValueError('窗口数量超过100000，请增大窗口时长。')
    raw = source[column].to_numpy(dtype=float, copy=True) * scales[unit]
    finite = np.isfinite(raw)
    transitions = np.diff(np.r_[False, finite, False].astype(np.int8))
    starts, stops = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
    if not any((stops - starts) / fs >= 20):
        raise ValueError('需要至少一段20秒的连续有效皮电数据；不会跨缺失段滤波。')
    notes = ['EDA分解和SCR是方法估计，不能直接解释为压力、情绪或疾病。',
             '每个文件按单个连续实验记录处理；不同被试/会话请分别导入。',
             '滤波边界可能失真，建议使用足够长的记录并检查首尾曲线。',
             '相对阈值按NeuroKit峰显著度筛选；绝对阈值按起始到峰值的幅值筛选。']
    if unit == 'a.u.':
        absolute = 0.
        notes.append('输入为任意单位：绝对uS阈值已禁用，所有幅值保持a.u.，不能用于uS阈值比较。')
    else:
        absolute = cfg['absolute_threshold']
        if np.any(raw[finite] < 0):
            notes.append('发现负皮电导值；未自动删除，请核对单位、偏移或伪迹。')
    import neurokit2 as nk
    signals = pd.DataFrame({'time': time, 'raw': raw, 'clean': np.nan,
                            'tonic': np.nan, 'phasic': np.nan, 'valid': False})
    rows = []
    for number, (a, b) in enumerate(zip(starts, stops), 1):
        if (b - a) / fs < 20:
            notes.append(f'连续段{number}不足20秒，保留原始值，分解与峰统计不纳入。')
            continue
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            cleaned = nk.eda_clean(raw[a:b], sampling_rate=fs, method=cfg['clean'])
            kwargs = {'cutoff': cfg['cutoff']} if cfg['method'] == 'highpass' else {}
            parts = nk.eda_phasic(cleaned, sampling_rate=fs, method=cfg['method'], **kwargs)
            phasic = parts['EDA_Phasic'].to_numpy()
            segment_peaks = _peaks(phasic, fs, time[a], cfg['relative_threshold'], absolute, number, nk)
        notes.extend(str(w.message) for w in caught)
        signals.loc[a:b-1, ['clean', 'tonic', 'phasic']] = np.column_stack(
            [cleaned, parts['EDA_Tonic'].to_numpy(), phasic])
        signals.loc[a:b-1, 'valid'] = True
        rows.extend(segment_peaks)
    peaks = pd.DataFrame(rows, columns=PEAK_COLUMNS)
    windows = _window_rows(signals, peaks, start, end, None, fs)
    windows += _window_rows(signals, peaks, start, end, cfg['window_seconds'], fs, step)
    events, grouped = _events(signals, peaks, event_df, cfg, fs, start, end)
    if len(peaks) == 0:
        notes.append('在当前方法与阈值下未检测到有效SCR；零峰是允许的结果。')
    if not events.empty and (events['status'] != 'OK').any():
        notes.append('重叠、越界或缺段事件保留逐事件结果，但未计入标签有效汇总。')
    meta = {'module': 'EDA', 'column': column, 'sampling_rate_hz': fs,
            'input_unit': unit, 'output_unit': 'a.u.' if unit == 'a.u.' else 'uS',
            'config': cfg, 'range_seconds': [start, end], 'neurokit2_version': version('neurokit2'),
            'missing_policy': 'process contiguous finite runs >=20s separately; no interpolation',
            'event_time_reference': 'seconds from first recording sample',
            'event_scr_rule': 'onset in [event+response_start,event+response_end)',
            'event_auc_rule': 'Trapezoidal signed integral of corrected clean samples in the half-open response window; no extrapolation to the right boundary; invalid events have NaN.',
            'event_corrected_unit': '%' if cfg['baseline_correction'] == 'percent' else 'ratio' if cfg['baseline_correction'] == 'ratio' else 'a.u.' if unit == 'a.u.' else 'uS',
            'nonresponse_rule': 'Zero qualifying SCR onsets in a valid response window; fraction denominator is valid events only.',
            'recovery_rule': 'first half-amplitude crossing before next detected peak, NaN if absent',
            'relative_threshold_rule': 'relative maximum peak prominence, per contiguous segment',
            'median_smoothing_seconds': 4 if cfg['method'] == 'median' else None,
            'source_type': prepared.get('meta', {}).get('status', 'USER_PROVIDED'),
            'references': ['EDA2026', 'NK_EDA', 'NK2021']}
    return {'tables': {'eda_signals': signals, 'eda_peaks': peaks,
                       'eda_windows': pd.DataFrame(windows), 'eda_events': events,
                       'eda_event_summary': grouped},
            'warnings': list(dict.fromkeys(notes)), 'meta': meta}
