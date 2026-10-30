"""Application settings page and runtime configuration actions."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import runtime_settings
from additional_controls import OPTIONS


COLORS = {
    "canvas": "#F3F6FA",
    "ink": "#243447",
    "white": "#FFFFFF",
    "mint": "#E8F5F2",
    "sky": "#EEF3F8",
    "coral": "#F8E6E2",
    "muted": "#607086",
    "line": "#D6DEE8",
}

EDA_CLEAN_OPTIONS = {
    "neurokit": "neurokit · NeuroKit",
    "biosppy": "biosppy · BioSPPy",
    "none": "none · 不清理",
}
EDA_METHOD_OPTIONS = {
    "highpass": "highpass · 高通",
    "median": "median · 中值",
    "cvxeda": "cvxeda · cvxEDA",
    "sparse": "sparse · SparseEDA（实验性，上游可能报错）",
}


class SettingsMixin:
    def _init_settings_state(self) -> None:
        loaded = runtime_settings.load_runtime_settings()
        self.settings_workers_var = tk.StringVar(value=str(loaded["workers"]))
        self.settings_backend_var = tk.StringVar(value=str(loaded["backend"]))
        self.settings_cupy_wheel_var = tk.StringVar(value="自动（依驱动）")
        self.settings_environment_state: dict | None = None
        self.settings_async_busy = False
        self.settings_async_buttons: list[tk.Button] = []
        self.eda_min_us_var = tk.StringVar(value="")
        self.eda_max_us_var = tk.StringVar(value="")
        self.eda_max_slope_us_per_second_var = tk.StringVar(value="")
        self.eda_clean_display_var = tk.StringVar(
            value=EDA_CLEAN_OPTIONS[self.eda_clean_var.get()]
        )
        self.eda_method_display_var = tk.StringVar(
            value=EDA_METHOD_OPTIONS[self.eda_method_var.get()]
        )

    def _build_settings_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "配置与环境",
            "保存完整分析配置、设置计算后端，并检测本机可用的运行组件。",
            COLORS["mint"],
        )
        config = self._card(
            parent,
            "完整配置导入与导出",
            "配置包括当前预处理、分析、预测、皮电、事件映射和运行资源设置；不包含数据文件。",
            COLORS["sky"],
        )
        config.grid_columnconfigure(0, weight=1)
        row = tk.Frame(config, bg=COLORS["white"])
        row.grid(row=0, column=0, sticky="w", padx=5, pady=5)
        for label, command, color in (
            ("导出完整配置…", self.settings_export_configuration, "coral"),
            ("导入完整配置…", self.settings_import_configuration, "lavender"),
        ):
            button = self._action_button(row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=(0, 8))

        runtime = self._card(
            parent,
            "运行资源",
            "设置会持久保存到当前用户配置目录；cpu 强制使用 CPU，auto 按核心能力选择，gpu 要求 CuPy 功能探测通过。",
            COLORS["mint"],
        )
        runtime.grid_columnconfigure(0, weight=1)
        runtime.grid_columnconfigure(1, weight=1)
        backend = self._combo(runtime, self.settings_backend_var, ["cpu", "auto", "gpu"], width=14)
        worker_count = tk.Spinbox(
            runtime,
            from_=1,
            to=32,
            textvariable=self.settings_workers_var,
            width=8,
            relief="flat",
            bd=0,
            bg=COLORS["white"],
            fg=COLORS["ink"],
            highlightbackground=COLORS["line"],
            highlightthickness=1,
            font=(self.font_family, 10),
        )
        self._workflow_register_editable(worker_count)
        self._field(runtime, "运行后端", backend, 0, 0)
        self._field(runtime, "并行 workers（1–32）", worker_count, 0, 1)
        save = self._action_button(runtime, "保存运行设置", self.settings_save_runtime, "green")
        save.shadow_wrapper.grid(row=4, column=0, sticky="w", padx=6, pady=(8, 4))
        self.settings_runtime_status_var = tk.StringVar(value=str(runtime_settings.runtime_settings_path()))
        self._label(
            runtime,
            "",
            textvariable=self.settings_runtime_status_var,
            color=COLORS["muted"],
            wraplength=900,
        ).grid(row=4, column=1, sticky="w", padx=6, pady=(8, 4))

        environment = self._card(
            parent,
            "环境检测与缺少组件",
            "检测会实际导入包并同步运行 CuPy 求和与矩阵乘法。没有 NVIDIA 驱动时不会安装驱动，也不会标记 GPU 可用。",
            COLORS["coral"],
        )
        environment.grid_columnconfigure(0, weight=1)
        environment.grid_columnconfigure(1, weight=1)
        controls = tk.Frame(environment, bg=COLORS["white"])
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=(3, 8))
        detect = self._action_button(
            controls, "检测环境", self.settings_detect_environment, "sky", track_busy=False
        )
        detect.shadow_wrapper.pack(side="left", padx=(0, 8))
        install = self._action_button(
            controls, "一键安装缺少包", self.settings_install_missing, "green", track_busy=False
        )
        install.shadow_wrapper.pack(side="left", padx=(0, 10))
        install.configure(state=tk.DISABLED)
        self.settings_install_button = install
        self.settings_async_buttons = [detect, install]
        wheel = self._combo(
            controls,
            self.settings_cupy_wheel_var,
            ["自动（依驱动）", "CUDA 12.x", "CUDA 13.x"],
            width=20,
        )
        self._label(controls, "CuPy CUDA wheel", color=COLORS["muted"]).pack(side="left", padx=(4, 6))
        wheel.pack(side="left", padx=3)
        self.settings_environment_status_var = tk.StringVar(value="尚未检测。请先运行环境检测。")
        self._label(
            environment,
            "",
            textvariable=self.settings_environment_status_var,
            color=COLORS["ink"],
            wraplength=900,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        log = tk.Text(
            environment,
            height=13,
            wrap="word",
            bg=COLORS["white"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            font=(self.font_family, 9),
        )
        log.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=6, pady=4)
        environment.grid_rowconfigure(2, weight=1)
        log.insert("1.0", "环境检测与安装输出会按任务进度记录在这里。\n")
        log.configure(state="disabled")
        self.settings_log = log
        self._label(
            environment,
            "EXE 中安装的组件会进入独立、持久、与 EXE Python ABI 匹配的环境，重启 PYTSA 后通过 site-packages 启用；必须重新运行导入与 GPU 功能检测确认可用。",
            color=COLORS["muted"],
            wraplength=900,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 2))

    def settings_save_runtime(self) -> None:
        try:
            workers = int(self.settings_workers_var.get().strip())
            runtime_settings.save_runtime_settings(
                {"workers": workers, "backend": self.settings_backend_var.get()}
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("运行设置无效", str(exc), parent=self.root)
            return
        self.settings_runtime_status_var.set(f"已保存：{runtime_settings.runtime_settings_path()}")
        self._set_status("运行后端与 workers 设置已保存。")

    def settings_export_configuration(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出完整 PYTSA 配置",
            defaultextension=".json",
            initialfile="PYTSA_configuration.json",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            parameters = self._workflow_collect_parameters()
            payload = {
                "format": "pytsa-configuration",
                "version": 1,
                "app_version": self._app_version,
                "parameters": parameters,
            }
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("导出配置失败", str(exc), parent=self.root)
            return
        self._set_status(f"完整配置已导出：{path}")

    @staticmethod
    def _settings_validate_configuration(payload: object) -> tuple[dict, dict[str, int | str]]:
        if not isinstance(payload, dict) or payload.get("format") != "pytsa-configuration" or payload.get("version") != 1:
            raise ValueError("文件不是受支持的 PYTSA 完整配置。")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("配置的 parameters 必须是对象。")
        required_sections = {"prepare", "analysis", "forecast", "eda", "event_mapping", "runtime"}
        if not required_sections.issubset(parameters):
            raise ValueError("配置不完整，缺少预处理、分析、预测、皮电、事件映射或运行资源设置。")
        for section in ("prepare", "analysis", "forecast", "eda", "event_mapping"):
            if not isinstance(parameters[section], dict):
                raise ValueError(f"配置中的 {section} 必须是对象。")
        eda = parameters["eda"]
        if not isinstance(eda.get("config"), dict):
            raise ValueError("配置中的 eda.config 必须是对象。")
        runtime = parameters["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != {"workers", "backend"}:
            raise ValueError("运行设置必须只包含 workers 与 backend。")
        runtime = runtime_settings.validate_runtime_settings(runtime)
        if parameters["analysis"].get("backend") != runtime["backend"] or parameters["analysis"].get("workers") != runtime["workers"]:
            raise ValueError("analysis 运行参数必须与 runtime.backend / runtime.workers 一致。")

        def number(value, name, *, optional=False, minimum=None, maximum=None, min_exclusive=False, max_exclusive=False):
            if optional and value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值。")
            if minimum is not None and (value <= minimum if min_exclusive else value < minimum):
                bound = "大于" if min_exclusive else "不小于"
                raise ValueError(f"{name}必须{bound} {minimum}。")
            if maximum is not None and (value >= maximum if max_exclusive else value > maximum):
                bound = "小于" if max_exclusive else "不大于"
                raise ValueError(f"{name}必须{bound} {maximum}。")
            return float(value)

        def integer(value, name, *, minimum=0, maximum=None):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name}必须是整数。")
            if value < minimum or maximum is not None and value > maximum:
                raise ValueError(f"{name}必须在 {minimum} 至 {maximum if maximum is not None else '无上限'} 之间。")
            return value

        prepare = parameters["prepare"]
        analysis = parameters["analysis"]
        forecast = parameters["forecast"]
        eda_config = eda["config"]
        events = parameters["event_mapping"]
        fs = number(prepare.get("fs"), "prepare.fs", minimum=0, min_exclusive=True)

        def validate_additional(section, settings):
            for key, label, default, kind in OPTIONS[section]:
                if key not in settings:
                    if section == "forecast" and key.startswith("rolling_"):
                        continue
                    raise ValueError(f"配置缺少 {section}.{key}。")
                value = settings[key]
                name = f"{section}.{key}（{label}）"
                if isinstance(kind, tuple):
                    if not isinstance(value, str) or value not in kind:
                        raise ValueError(f"{name} 选项无效。")
                elif kind is int:
                    if value is None and default == "":
                        continue
                    integer(value, name, minimum=0 if key == "savgol_polyorder" else 1)
                elif kind is float:
                    number(value, name, optional=default == "")
                elif not isinstance(value, str):
                    raise ValueError(f"{name} 必须是文本。")

        validate_additional("prepare", prepare)
        validate_additional("analysis", analysis)
        validate_additional("forecast", forecast)
        validate_additional("eda", eda_config)

        if prepare["max_gap_seconds"] is not None:
            number(prepare["max_gap_seconds"], "prepare.max_gap_seconds", minimum=0, min_exclusive=True)
        if prepare["max_gap_samples"] is not None:
            integer(prepare["max_gap_samples"], "prepare.max_gap_samples", minimum=1)
        if int(prepare["hampel_window"]) < 3 or int(prepare["hampel_window"]) % 2 == 0:
            raise ValueError("prepare.hampel_window 必须是至少3的奇数。")
        number(prepare["hampel_sigma"], "prepare.hampel_sigma", minimum=0, min_exclusive=True)
        if int(prepare["smoothing_window"]) < 3 or int(prepare["smoothing_window"]) % 2 == 0:
            raise ValueError("prepare.smoothing_window 必须是至少3的奇数。")
        if prepare["savgol_polyorder"] >= prepare["smoothing_window"]:
            raise ValueError("prepare.savgol_polyorder 必须小于 smoothing_window。")
        if prepare["filter_order"] > 12:
            raise ValueError("prepare.filter_order 必须在1至12之间。")
        filter_type = prepare["filter_type"]
        filter_low = prepare["filter_low"]
        filter_high = prepare["filter_high"]
        if filter_type == "highpass" and (filter_low is None or not 0 < filter_low < fs / 2):
            raise ValueError("高通频率必须大于0且低于 Nyquist 频率。")
        if filter_type == "lowpass" and (filter_high is None or not 0 < filter_high < fs / 2):
            raise ValueError("低通频率必须大于0且低于 Nyquist 频率。")
        if filter_type == "bandpass" and (
            filter_low is None or filter_high is None or not 0 < filter_low < filter_high < fs / 2
        ):
            raise ValueError("带通频率必须满足 0 < 下限 < 上限 < Nyquist 频率。")
        if prepare["notch_frequency"] is not None and not 0 < prepare["notch_frequency"] < fs / 2:
            raise ValueError("prepare.notch_frequency 必须低于 Nyquist 频率。")
        number(prepare["notch_q"], "prepare.notch_q", minimum=0, min_exclusive=True)

        value_cols = prepare.get("value_cols")
        if not isinstance(value_cols, list) or any(not isinstance(value, str) for value in value_cols) or len(set(value_cols)) != len(value_cols):
            raise ValueError("prepare.value_cols 必须是唯一列名组成的列表。")
        time_col = prepare.get("time_col")
        if time_col is not None and not isinstance(time_col, str):
            raise ValueError("prepare.time_col 必须是列名或 null。")
        if time_col and time_col in value_cols:
            raise ValueError("时间列不能同时出现在信号变量中。")
        for key, choices in (
            ("time_kind", {"auto", "numeric", "datetime"}),
            ("missing", {"reject", "ffill", "interpolate"}),
            ("duplicate", {"reject", "mean", "first"}),
            ("transform", {"none", "demean", "detrend", "zscore", "difference"}),
        ):
            if prepare.get(key) not in choices:
                raise ValueError(f"prepare.{key} 选项无效。")
        if prepare.get("resample") is not None:
            number(prepare["resample"], "prepare.resample", minimum=0, min_exclusive=True)

        integer(analysis.get("max_lag"), "analysis.max_lag", minimum=1)
        integer(analysis.get("period"), "analysis.period", minimum=2, maximum=366)
        integer(analysis.get("lag"), "analysis.lag", minimum=1)
        integer(analysis.get("nperseg"), "analysis.nperseg", minimum=2)
        band_low = number(analysis.get("band_low"), "analysis.band_low", minimum=0)
        band_high = number(analysis.get("band_high"), "analysis.band_high", optional=True)
        if band_high is not None and (band_high <= band_low or band_high >= fs / 2):
            raise ValueError("analysis.band_high 必须高于下限且低于 Nyquist 频率。")
        for key in ("welch_overlap", "window_overlap"):
            number(analysis[key], f"analysis.{key}", minimum=0, maximum=1, max_exclusive=True)
        for key in ("window_seconds", "window_step_seconds"):
            if analysis[key] is not None:
                number(analysis[key], f"analysis.{key}", minimum=0, min_exclusive=True)
        if analysis["nfft"] is not None:
            integer(analysis["nfft"], "analysis.nfft", minimum=2)
        power_bands = analysis["power_bands"].strip()
        if power_bands:
            for band in power_bands.split(";"):
                parts = band.split(":")
                if len(parts) != 3 or not parts[0].strip():
                    raise ValueError("analysis.power_bands 格式须为 名称:下限:上限;…")
                low = number(float(parts[1]), "功率频段下限", minimum=0)
                high = number(float(parts[2]), "功率频段上限", minimum=0)
                if low >= high or high > fs / 2:
                    raise ValueError("功率频段上限须高于下限且不超过 Nyquist 频率。")

        if "column" not in forecast or not isinstance(forecast["column"], str):
            raise ValueError("forecast.column 必须是列名文本。")
        integer(forecast.get("horizon"), "forecast.horizon", minimum=1)
        integer(forecast.get("period"), "forecast.period", minimum=2, maximum=366)
        order = forecast.get("order")
        seasonal_order = forecast.get("seasonal_order")
        if not isinstance(order, list) or len(order) != 3:
            raise ValueError("forecast.order 必须包含三个整数。")
        if not isinstance(seasonal_order, list) or len(seasonal_order) != 4:
            raise ValueError("forecast.seasonal_order 必须包含四个整数。")
        for value, upper, name in zip(order, (8, 2, 8), ("p", "d", "q")):
            integer(value, f"forecast.order.{name}", maximum=upper)
        for value, upper, name in zip(seasonal_order, (2, 1, 2, 366), ("P", "D", "Q", "period")):
            integer(value, f"forecast.seasonal_order.{name}", minimum=2 if name == "period" else 0, maximum=upper)
        models = forecast.get("models")
        if not isinstance(models, list) or not models or len(set(models)) != len(models) or any(model not in {"Naive", "SeasonalNaive", "ETS", "ARIMA", "SARIMA"} for model in models):
            raise ValueError("forecast.models 必须是唯一且非空的支持模型列表。")
        if forecast.get("missing") not in {"reject", "ffill"}:
            raise ValueError("forecast.missing 选项无效。")
        number(forecast["test_fraction"], "forecast.test_fraction", minimum=0, maximum=0.5, min_exclusive=True, max_exclusive=True)
        number(forecast["confidence"], "forecast.confidence", minimum=0, maximum=1, min_exclusive=True, max_exclusive=True)
        if forecast["validation"] not in {"holdout", "rolling"}:
            raise ValueError("forecast.validation 选项无效。")
        for key in ("rolling_train", "rolling_horizon", "rolling_step"):
            if key in forecast:
                integer(forecast[key], f"forecast.{key}", minimum=1)

        if eda.get("column") is not None and not isinstance(eda.get("column"), str):
            raise ValueError("eda.column 必须是列名文本。")
        for key in ("min_us", "max_us", "max_slope_us_per_second"):
            if key not in eda_config:
                raise ValueError(f"配置缺少 eda.config.{key}。")
        unit = eda_config.get("unit")
        if unit not in {"", "uS", "mS", "nS", "a.u."}:
            raise ValueError("eda.config.unit 选项无效。")
        if eda_config.get("method") not in EDA_METHOD_OPTIONS or eda_config.get("clean") not in EDA_CLEAN_OPTIONS:
            raise ValueError("eda.config 分解或清理方法选项无效。")
        cutoff = number(eda_config.get("cutoff"), "eda.config.cutoff", minimum=0, min_exclusive=True)
        if eda_config["method"] == "highpass" and cutoff >= fs / 2:
            raise ValueError("eda.config.cutoff 必须低于 Nyquist 频率。")
        number(eda_config["relative_threshold"], "eda.config.relative_threshold", minimum=0, maximum=1)
        number(eda_config["absolute_threshold"], "eda.config.absolute_threshold", minimum=0)
        number(eda_config["window_seconds"], "eda.config.window_seconds", minimum=0, min_exclusive=True)
        start = number(eda_config["start_seconds"], "eda.config.start_seconds", minimum=0)
        end = number(eda_config["end_seconds"], "eda.config.end_seconds", optional=True)
        if end is not None and end <= start:
            raise ValueError("eda.config.end_seconds 必须晚于 start_seconds。")
        if not number(eda_config["baseline_start"], "eda.config.baseline_start") < number(eda_config["baseline_end"], "eda.config.baseline_end") <= 0 <= number(eda_config["response_start"], "eda.config.response_start") < number(eda_config["response_end"], "eda.config.response_end"):
            raise ValueError("EDA 事件基线与响应时间范围无效。")
        number(eda_config["window_overlap"], "eda.config.window_overlap", minimum=0, maximum=1, max_exclusive=True)
        if eda_config["window_step_seconds"] is not None:
            step = number(eda_config["window_step_seconds"], "eda.config.window_step_seconds", minimum=0, min_exclusive=True)
            if step < 1 / fs:
                raise ValueError("eda.config.window_step_seconds 不能短于一个采样间隔。")
        quality = []
        for key in ("min_us", "max_us", "max_slope_us_per_second"):
            quality.append(number(eda_config.get(key), f"eda.config.{key}", optional=True, minimum=0))
        if quality[0] is not None and quality[1] is not None and quality[0] >= quality[1]:
            raise ValueError("eda.config.min_us 必须小于 max_us。")
        if unit == "a.u." and any(value is not None for value in quality):
            raise ValueError("a.u. 配置不能包含以 uS 为单位的质量阈值。")
        if eda_config.get("baseline_correction") not in {"none", "subtract_mean", "subtract_median", "ratio", "percent"}:
            raise ValueError("eda.config.baseline_correction 选项无效。")
        if eda_config.get("clean") == "biosppy" and fs <= 10:
            raise ValueError("BioSPPy 清理要求采样率高于 10 Hz。")

        if events.get("source") not in {"无事件", "事件文件", "原始标签列"}:
            raise ValueError("event_mapping.source 选项无效。")
        for key in ("path", "time_col", "label_col", "marker_col"):
            if not isinstance(events.get(key), str):
                raise ValueError(f"event_mapping.{key} 必须是文本。")
        return parameters, runtime

    def _settings_capture_controls(self) -> dict:
        return {
            "variables": {
                name: value.get()
                for name, value in vars(self).items()
                if isinstance(value, tk.Variable)
            },
            "additional": self._additional_state(),
            "value_cols": list(self._value_columns()) if self.raw_frame is not None else [],
            "models": {name: bool(value.get()) for name, value in self.model_vars.items()},
            "event_path": str(self.event_path) if getattr(self, "event_path", None) else "",
            "runtime": runtime_settings.load_runtime_settings(),
        }

    def _settings_restore_controls(self, state: dict) -> None:
        for name, value in state["variables"].items():
            variable = getattr(self, name, None)
            if isinstance(variable, tk.Variable):
                variable.set(value)
        self._restore_additional_state(state["additional"])
        self.event_path = Path(state["event_path"]) if state["event_path"] else None
        if self.raw_frame is not None:
            columns = [str(name) for name in self.raw_frame.columns]
            selected = set(state["value_cols"])
            self.value_list.selection_clear(0, tk.END)
            for index, column in enumerate(columns):
                if column in selected:
                    self.value_list.selection_set(index)
        for name, value in state["models"].items():
            self.model_vars[name].set(value)
        self.settings_workers_var.set(str(state["runtime"]["workers"]))
        self.settings_backend_var.set(str(state["runtime"]["backend"]))
        self._settings_sync_eda_choices()
        if hasattr(self, "event_path_var"):
            self.event_path_var.set(str(self.event_path or ""))
            self._on_event_source_changed()
            self._on_eda_unit_changed()

    def settings_import_configuration(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入完整 PYTSA 配置",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        previous = self._settings_capture_controls()
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            parameters, runtime = self._settings_validate_configuration(payload)
            self._workflow_apply_parameters_to_controls(parameters)
            self.event_path = Path(parameters["event_mapping"].get("path", "")) if parameters["event_mapping"].get("path") else None
            self.settings_workers_var.set(str(runtime["workers"]))
            self.settings_backend_var.set(str(runtime["backend"]))
            if hasattr(self, "event_path_var"):
                self.event_path_var.set(str(self.event_path or ""))
            current_frame = self.raw_frame
            self.raw_frame = None
            try:
                self._workflow_collect_parameters()
            finally:
                self.raw_frame = current_frame
            runtime_settings.save_runtime_settings(runtime)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            self._settings_restore_controls(previous)
            messagebox.showerror("导入配置失败", str(exc), parent=self.root)
            return
        self._set_status(f"完整配置已导入：{path}")

    def settings_detect_environment(self) -> None:
        self._settings_start_async(
            "环境检测",
            lambda emit: runtime_settings.detect_environment(emit),
            self._settings_environment_detected,
        )

    def _settings_environment_detected(self, result: dict) -> None:
        self.settings_environment_state = result
        packages = result["packages"]
        missing = [name for name, status in packages.items() if not status["ok"]]
        self.settings_environment_status_var.set(
            f"Python {result['python']} · CPU {result['cpu']}（{result['cpu_count']} 逻辑核） · "
            f"NVIDIA {result['nvidia']['driver'] or '未检测到驱动'} / CUDA {result['nvidia']['cuda'] or '未知'} · "
            f"CuPy {'功能验证通过' if result['cupy']['functional'] else '未通过功能验证'}"
        )
        for name, status in packages.items():
            self._settings_append_log(
                f"{name}: {status['version'] if status['ok'] else status['error']}"
            )
        self._settings_append_log(
            f"NVIDIA: {result['nvidia']['device'] or result['nvidia']['error']}; "
            f"driver={result['nvidia']['driver'] or 'unknown'}; CUDA={result['nvidia']['cuda'] or 'unknown'}"
        )
        self._settings_append_log(
            f"CuPy: {result['cupy']['version'] or 'not imported'}; "
            f"functional={result['cupy']['functional']}; {result['cupy']['error'] or ''}"
        )
        cupy_missing = (
            not result["cupy"]["imported"]
            and bool(runtime_settings.compatible_cuda_wheels(result))
        )
        self.settings_install_button.configure(
            state=tk.NORMAL if missing or cupy_missing else tk.DISABLED
        )
        self._set_status("环境检测完成；GPU 可用性仅在 CuPy 同步计算通过时成立。")

    def settings_install_missing(self) -> None:
        environment = self.settings_environment_state
        if environment is None:
            self._settings_append_log("请先运行环境检测。")
            return
        missing = [
            runtime_settings.PACKAGE_SPECS[name]
            for name, status in environment["packages"].items()
            if not status["ok"]
        ]
        if not environment["cupy"]["imported"]:
            if environment["nvidia"]["device"] is None:
                self._settings_append_log("CuPy 未安装；未检测到 NVIDIA 驱动，不安装 GPU 运行包。")
            else:
                wheel_label = self.settings_cupy_wheel_var.get()
                wheel = {"CUDA 12.x": "12x", "CUDA 13.x": "13x"}.get(wheel_label, wheel_label)
                try:
                    missing.append(runtime_settings.selected_cupy_package(environment, wheel))
                except ValueError as exc:
                    self._settings_append_log(str(exc))
        elif not environment["cupy"]["functional"]:
            self._settings_append_log(
                "CuPy is installed but failed the real device calculation. The installer will not replace it or claim GPU support."
            )
        if not missing:
            self._settings_append_log("没有可安装的缺少包。")
            return
        self.settings_install_button.configure(state=tk.DISABLED)
        self._settings_start_async(
            "安装缺少包",
            lambda emit: runtime_settings.install_optional_packages(missing, emit),
            lambda _result: self._settings_install_finished(),
        )

    def _settings_install_finished(self) -> None:
        self._settings_append_log("安装流程完成；运行环境检测以确认真实导入与 GPU 功能。")
        self.settings_environment_state = None
        self.settings_install_button.configure(state=tk.DISABLED)
        self._set_status("组件安装完成；EXE 中安装的组件需重启 PYTSA 后验证。")

    def _settings_start_async(self, title: str, operation, on_success) -> None:
        if self.settings_async_busy:
            return
        self.settings_async_busy = True
        for button in self.settings_async_buttons:
            button.configure(state=tk.DISABLED)
        self._settings_append_log(f"\n[{title}] 开始")

        def emit(line: str) -> None:
            self.task_queue.put(("settings_log", line))

        def worker() -> None:
            try:
                result = operation(emit)
            except Exception as exc:
                self.task_queue.put(("settings_done", title, None, f"{type(exc).__name__}: {exc}", on_success))
            else:
                self.task_queue.put(("settings_done", title, result, None, on_success))

        threading.Thread(target=worker, name=f"pytsa-{title}", daemon=True).start()

    def _settings_process_async_event(self, event: tuple) -> bool:
        if event[0] == "settings_log":
            self._settings_append_log(str(event[1]))
            return True
        if event[0] != "settings_done":
            return False
        _, title, result, error, on_success = event
        self.settings_async_busy = False
        for button in self.settings_async_buttons:
            button.configure(state=tk.NORMAL)
        if error:
            self._settings_append_log(f"[{title}] 失败：{error}")
            self._set_status(f"{title}失败：{error}", warning=True)
            messagebox.showerror(f"{title}失败", error, parent=self.root)
        else:
            self._settings_append_log(f"[{title}] 完成")
            on_success(result)
        if title == "环境检测" and error:
            self.settings_install_button.configure(state=tk.DISABLED)
        return True

    def _settings_append_log(self, line: str) -> None:
        if not hasattr(self, "settings_log"):
            return
        self.settings_log.configure(state="normal")
        self.settings_log.insert(tk.END, line + "\n")
        line_count = int(self.settings_log.index("end-1c").split(".")[0])
        if line_count > 500:
            self.settings_log.delete("1.0", f"{line_count - 500}.0")
        self.settings_log.see(tk.END)
        self.settings_log.configure(state="disabled")

    def _settings_sync_eda_choices(self) -> None:
        clean = self.eda_clean_var.get()
        method = self.eda_method_var.get()
        self.eda_clean_display_var.set(EDA_CLEAN_OPTIONS.get(clean, clean))
        self.eda_method_display_var.set(EDA_METHOD_OPTIONS.get(method, method))

    def _settings_eda_choice_changed(self, kind: str, _event=None) -> None:
        if kind == "clean":
            reverse = {label: value for value, label in EDA_CLEAN_OPTIONS.items()}
            self.eda_clean_var.set(reverse[self.eda_clean_display_var.get()])
        else:
            reverse = {label: value for value, label in EDA_METHOD_OPTIONS.items()}
            self.eda_method_var.set(reverse[self.eda_method_display_var.get()])

    def _workflow_collect_parameters(self) -> dict:
        runtime = runtime_settings.load_runtime_settings()
        if self.raw_frame is None:
            time_column = self.time_col_var.get()
            parameters = {
                "prepare": {
                    **self._additional_config("prepare"),
                    "value_cols": [],
                    "time_col": None if time_column == "（无时间列）" else time_column,
                    "time_kind": {
                        "自动识别": "auto",
                        "数值秒": "numeric",
                        "数值时间": "numeric",
                        "日期时间": "datetime",
                    }.get(self.time_kind_var.get(), "auto"),
                    "fs": self._parse_float(self.fs_var, "采样率 fs"),
                    "missing": {
                        "拒绝缺失值": "reject",
                        "前向填充（仅探索）": "ffill",
                        "线性插值（仅探索）": "interpolate",
                    }[self.missing_var.get()],
                    "duplicate": {
                        "重复时间：报错": "reject",
                        "重复时间：取均值": "mean",
                        "重复时间：保留首条": "first",
                    }[self.duplicate_var.get()],
                    "resample": self._parse_float(self.resample_var, "重采样间隔", allow_blank=True),
                    "transform": {
                        "不变换": "none",
                        "去均值": "demean",
                        "去线性趋势": "detrend",
                        "z-score 标准化": "zscore",
                        "一阶差分": "difference",
                    }[self.transform_var.get()],
                },
                "analysis": {
                    **self._additional_config("analysis"),
                    "max_lag": self._parse_int(self.max_lag_var, "最大滞后"),
                    "period": self._parse_int(self.period_var, "季节周期", minimum=2),
                    "lag": self._parse_int(self.lag_var, "定向滞后"),
                    "band_low": self._parse_float(self.band_low_var, "频带下限"),
                    "band_high": self._parse_float(self.band_high_var, "频带上限", allow_blank=True),
                    "nperseg": self._parse_int(self.nperseg_var, "Welch 分段长度", minimum=2),
                },
                "forecast": self._workflow_collect_forecast_config(),
                "eda": self._workflow_collect_eda_parameters(),
                "event_mapping": {
                    "source": self.event_source_var.get(),
                    "path": str(self.event_path) if getattr(self, "event_path", None) else "",
                    "time_col": self.event_time_col_var.get(),
                    "label_col": self.event_label_col_var.get(),
                    "marker_col": self.marker_col_var.get(),
                },
            }
            parameters["analysis"].update(runtime)
        else:
            parameters = super()._workflow_collect_parameters()
            runtime = {"workers": parameters["analysis"]["workers"], "backend": parameters["analysis"]["backend"]}
        parameters["runtime"] = runtime
        return parameters

    def _workflow_collect_eda_parameters(self) -> dict:
        parameters = super()._workflow_collect_eda_parameters()
        config = parameters["config"]
        for key, variable, label in (
            ("min_us", self.eda_min_us_var, "皮电最小有效幅值（uS）"),
            ("max_us", self.eda_max_us_var, "皮电最大有效幅值（uS）"),
            ("max_slope_us_per_second", self.eda_max_slope_us_per_second_var, "皮电最大变化速率（uS/s）"),
        ):
            config[key] = self._parse_float(variable, label, allow_blank=True)
        return parameters

    def _workflow_apply_parameters_to_controls(self, parameters: dict) -> None:
        super()._workflow_apply_parameters_to_controls(parameters)
        runtime = parameters.get("runtime")
        if runtime is not None:
            validated = runtime_settings.validate_runtime_settings(runtime)
            self.settings_workers_var.set(str(validated["workers"]))
            self.settings_backend_var.set(str(validated["backend"]))
        eda = parameters.get("eda")
        config = eda.get("config", eda) if isinstance(eda, dict) else {}
        if isinstance(config, dict):
            for key, variable in (
                ("min_us", self.eda_min_us_var),
                ("max_us", self.eda_max_us_var),
                ("max_slope_us_per_second", self.eda_max_slope_us_per_second_var),
            ):
                if key in config:
                    variable.set("" if config[key] is None else str(config[key]))
        self._settings_sync_eda_choices()

