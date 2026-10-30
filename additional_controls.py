"""Declarative advanced controls shared by runs, templates and projects."""
from __future__ import annotations

import tkinter as tk

# key, label, default, type or choices; empty numeric fields mean automatic/disabled.
OPTIONS = {
    "prepare": [
        ("time_scale", "数值时间单位", "s", ("s", "ms", "us")),
        ("timezone", "无时区日期的时区", "UTC", str),
        ("max_gap_seconds", "最大填补时长（秒；空不限）", "", float),
        ("max_gap_samples", "最大连续缺失点数（空不限）", "", int),
        ("preprocess_detrend", "预处理去趋势", "none", ("none", "constant", "linear")),
        ("hampel_enabled", "Hampel 去尖峰", "off", ("off", "on")),
        ("hampel_window", "Hampel 窗口（奇数点）", "7", int),
        ("hampel_sigma", "Hampel 阈值倍数", "3", float),
        ("smoothing", "平滑方法", "none", ("none", "median", "savgol")),
        ("smoothing_window", "平滑窗口（奇数点）", "5", int),
        ("savgol_polyorder", "Savgol 多项式阶数", "2", int),
        ("notch_frequency", "陷波频率（Hz；空关闭）", "", float),
        ("notch_q", "陷波品质因数 Q", "30", float),
        ("wavelet_enabled", "小波软阈值去噪", "off", ("off", "on")),
        ("wavelet_name", "离散小波", "db4", ("db4", "db6", "sym4", "coif1")),
        ("filter_type", "Butterworth 滤波", "none", ("none", "lowpass", "highpass", "bandpass")),
        ("filter_low", "高通 / 带通下限（Hz）", "", float),
        ("filter_high", "低通 / 带通上限（Hz）", "", float),
        ("filter_order", "滤波阶数（1–12）", "4", int),
        ("filter_phase", "滤波相位", "zero", ("zero", "causal")),
    ],
    "analysis": [
        ("welch_window", "Welch 窗函数", "hann", ("hann", "hamming", "blackman", "boxcar")),
        ("welch_overlap", "Welch 重叠比例（0–<1）", "0.5", float),
        ("nfft", "FFT 点数（空=分段长度）", "", int),
        ("welch_detrend", "频谱去趋势", "constant", ("constant", "linear", "none")),
        ("power_bands", "功率频段：名称:下限:上限;…", "", str),
        ("window_seconds", "统计窗口长度（秒；空=关闭）", "", float),
        ("window_step_seconds", "统计步长（秒；空=按重叠率）", "", float),
        ("window_overlap", "统计窗口重叠比例（0–<1）", "0", float),
    ],
    "forecast": [
        ("test_fraction", "留后比例（0–<0.5）", "0.2", float),
        ("confidence", "预测区间置信水平（0–1）", "0.95", float),
        ("validation", "附加验证方式", "holdout", ("holdout", "rolling")),
        ("rolling_train", "滚动训练窗口（样本点）", "", int),
        ("rolling_horizon", "每折验证长度（样本点）", "", int),
        ("rolling_step", "滚动步长（样本点）", "", int),
    ],
    "eda": [
        ("window_step_seconds", "皮电窗口步长（秒；空=按重叠率）", "", float),
        ("window_overlap", "皮电窗口重叠比例（0–<1）", "0", float),
        ("baseline_correction", "事件基线校正", "subtract_mean",
         ("subtract_mean", "subtract_median", "none", "ratio", "percent")),
    ],
}


class AdditionalControlsMixin:
    def _init_additional_variables(self):
        self.additional_vars = {
            section: {key: tk.StringVar(value=default) for key, _, default, _ in fields}
            for section, fields in OPTIONS.items()
        }

    def _additional_config(self, section):
        result = {}
        for key, label, default, kind in OPTIONS[section]:
            variable = self.additional_vars[section][key]
            if kind is int:
                value = None if not variable.get().strip() and not default else self._parse_int(variable, label, minimum=1)
            elif kind is float:
                value = self._parse_float(variable, label, allow_blank=not bool(default))
            else:
                value = variable.get().strip()
                if isinstance(kind, tuple) and value not in kind:
                    raise ValueError(f"{label}选项无效。")
            # Omit automatic rolling values so backend defaults remain data-dependent.
            if section == "forecast" and key.startswith("rolling_") and value is None:
                continue
            result[key] = value
        return result

    def _additional_card(self, parent, section, title, note, color, *, keys=None):
        card = self._card(parent, title, note, color)
        for column in range(2):
            card.grid_columnconfigure(column, weight=1)
        fields = [row for row in OPTIONS[section] if keys is None or row[0] in keys]
        for i, (key, label, _, kind) in enumerate(fields):
            variable = self.additional_vars[section][key]
            widget = (self._combo(card, variable, list(kind)) if isinstance(kind, tuple)
                      else self._entry(card, variable))
            self._field(card, label, widget, i // 2, i % 2)
        return card

    def _apply_additional_parameters(self, parameters):
        for section, fields in OPTIONS.items():
            settings = parameters.get(section)
            if not isinstance(settings, dict):
                continue
            if section == "eda":
                settings = settings.get("config", settings)
            for key, _, default, _ in fields:
                value = settings.get(key, default)
                self.additional_vars[section][key].set("" if value is None else str(value))

    def _additional_state(self):
        return {section: {key: var.get() for key, var in fields.items()}
                for section, fields in self.additional_vars.items()}

    def _restore_additional_state(self, state):
        for section, fields in OPTIONS.items():
            for key, _, default, _ in fields:
                self.additional_vars[section][key].set(state.get(section, {}).get(key, default))
