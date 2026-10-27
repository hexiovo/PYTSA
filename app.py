from __future__ import annotations

import argparse
import copy
import ctypes
import io
import json
import math
import os
import queue
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from tkinter import font as tkfont

import workflow_ui as _workflow_ui
from workflow_ui import WorkflowCancelled, WorkflowMixin
from additional_controls import AdditionalControlsMixin


APP_NAME = "PYTSA"
APP_VERSION = "1.2.0"
APP_USER_MODEL_ID = "PYTSA.TimeSeriesLab.1"
PAGE_ORDER = ("input", "prepare", "matrices", "forecast", "eda", "workflow")
CAPTURE_STEPS = (
    ("input", 0.0, "input"),
    ("input", 0.55, "input-time-options"),
    ("prepare", 0.0, "prepare"),
    ("prepare", 0.27, "prepare-filter-options"),
    ("prepare", 0.55, "prepare-spectral-options"),
    ("prepare", 1.0, "prepare-results"),
    ("matrices", 0.0, "matrices"),
    ("matrices", 1.0, "matrix-results"),
    ("forecast", 0.0, "forecast"),
    ("forecast", 0.25, "forecast-validation-options"),
    ("forecast", 0.95, "forecast-curves"),
    ("forecast", 1.0, "forecast-results"),
    ("eda", 0.0, "eda"),
    ("eda", 0.28, "eda-window-options"),
    ("eda", 0.60, "eda-curves"),
    ("eda", 1.0, "eda-results"),
    ("workflow", 0.0, "workflow"),
)
PAGE_TITLES = {
    "input": ("01", "数据输入", "导入单段数据"),
    "prepare": ("02", "预处理与分析", "设置规则并计算"),
    "matrices": ("03", "指标矩阵与图表", "查看指标与关系"),
    "forecast": ("04", "预测、导出与方法", "比较模型并保存"),
    "eda": ("05", "实验 / 皮电", "分析皮电与事件"),
    "workflow": ("06", "工作台", "项目、批次与历史"),
}

COLORS = {
    "canvas": "#FCF8F2",
    "ink": "#27324A",
    "green": "#20C46A",
    "mint": "#E9FBF1",
    "sky": "#E6F7FB",
    "coral": "#FFB4AA",
    "lemon": "#F1F9D8",
    "lavender": "#E6E6FA",
    "white": "#FFFFFF",
    "muted": "#5D687D",
    "warning": "#8D3A2F",
    "line": "#C7CFD9",
}
# The workflow mixin shares the application's single palette at widget-build time.
_workflow_ui.COLORS = COLORS

MATRIX_LABELS = {
    "pearson": "Pearson 相关矩阵",
    "pearson_correlation": "Pearson 相关矩阵",
    "spearman": "Spearman 秩相关矩阵",
    "spearman_correlation": "Spearman 秩相关矩阵",
    "covariance": "协方差矩阵",
    "partial": "偏相关矩阵",
    "partial_correlation": "偏相关矩阵",
    "lagged": "指定滞后相关矩阵",
    "lagged_correlation": "指定滞后相关矩阵",
    "coherence": "频带平均平方相干矩阵",
    "standardized_euclidean": "标准化欧式距离矩阵",
    "euclidean": "标准化欧式距离矩阵",
}


def resource_path(relative: str) -> Path:
    """Resolve resources in both source and PyInstaller one-file runs."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def set_windows_identity_and_dpi() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(1) == 0:
            return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _pandas():
    import pandas as pd

    return pd


def _numpy():
    import numpy as np

    return np


def _body_font(root: tk.Misc) -> str:
    try:
        installed = set(tkfont.families(root))
    except Exception:
        installed = set()
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI"):
        if family in installed:
            return family
    return "TkDefaultFont"


def _configure_matplotlib_fonts() -> None:
    """Prefer the Windows Chinese UI font for chart labels and titles."""
    try:
        import matplotlib
        from matplotlib import font_manager

        preferred = ["Microsoft YaHei UI", "Microsoft YaHei", "SimHei"]
        available = {font.name for font in font_manager.fontManager.ttflist}
        family = next((name for name in preferred if name in available), None)
        if family:
            matplotlib.rcParams["font.family"] = family
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        return


class ScrollablePage(tk.Frame):
    """A vertical page host; its inner width follows the available content width."""

    def __init__(self, master: tk.Misc, background: str) -> None:
        super().__init__(master, bg=background)
        self.canvas = tk.Canvas(
            self, bg=background, bd=0, highlightthickness=0, takefocus=False
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = tk.Frame(self.canvas, bg=background)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._fit_inner_width)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def _update_scroll_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit_inner_width(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class PytsaApp(AdditionalControlsMixin, WorkflowMixin):
    def __init__(
        self,
        root: tk.Tk,
        capture_dir: Path | None = None,
        capture_eda_only: bool = False,
    ) -> None:
        self.root = root
        self.capture_dir = capture_dir
        self.capture_mode = capture_dir is not None
        self.capture_eda_only = capture_eda_only
        self.capture_steps = (
            tuple(step for step in CAPTURE_STEPS if step[0] == "eda")
            if capture_eda_only
            else CAPTURE_STEPS
        )
        self.font_family = _body_font(root)
        self.raw_frame = None
        self.raw_path: Path | None = None
        self.raw_source_label = "尚未载入数据"
        self.excel_sheets: list[str] = []
        self.prepared = None
        self.analysis = None
        self.forecast_result = None
        self.forecast_column = ""
        self.eda_result = None
        self.eda_meta = None
        self.prep_config_used = {}
        self.analysis_config_used = {}
        self.forecast_config_used = {}
        self.eda_config_used = {}
        self.event_frame = None
        self.event_path: Path | None = None
        self.last_warnings: list[str] = []
        self.busy = False
        self.task_queue: queue.Queue = queue.Queue()
        self._active_task_id = ""
        self._active_cancel_event: threading.Event | None = None
        self._workflow_editable_widgets: list[tk.Widget] = []
        self.action_buttons: list[tk.Button] = []
        self.page_buttons: dict[str, tk.Button] = {}
        self.pages: dict[str, ScrollablePage] = {}
        self.table_widgets: dict[str, ttk.Treeview] = {}
        self.chart_widgets: dict[str, tuple[object, object]] = {}
        self._matrix_options: dict[str, str] = {}

        self._init_variables()
        self._init_workflow_state()
        _configure_matplotlib_fonts()
        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_pages()
        self.show_page("input")
        self._populate_method_notes()
        self.root.after(100, self._drain_worker_queue)
        if self.capture_mode:
            self.root.after(500, self.load_eda_demo)

    def _init_variables(self) -> None:
        self._init_additional_variables()
        self.source_var = tk.StringVar(value="")
        self.sheet_var = tk.StringVar(value="")
        self.encoding_var = tk.StringVar(value="utf-8-sig")
        self.separator_var = tk.StringVar(value="自动识别")
        self.time_col_var = tk.StringVar(value="（无时间列）")
        self.time_kind_var = tk.StringVar(value="自动识别")
        self.fs_var = tk.StringVar(value="1")

        self.missing_var = tk.StringVar(value="拒绝缺失值")
        self.duplicate_var = tk.StringVar(value="重复时间：报错")
        self.resample_var = tk.StringVar(value="")
        self.transform_var = tk.StringVar(value="不变换")
        self.max_lag_var = tk.StringVar(value="30")
        self.period_var = tk.StringVar(value="12")
        self.lag_var = tk.StringVar(value="1")
        self.band_low_var = tk.StringVar(value="0")
        self.band_high_var = tk.StringVar(value="")
        self.nperseg_var = tk.StringVar(value="256")

        self.forecast_col_var = tk.StringVar(value="")
        self.horizon_var = tk.StringVar(value="12")
        self.forecast_missing_var = tk.StringVar(value="拒绝缺失值")
        self.arima_order_var = tk.StringVar(value="1,1,1")
        self.seasonal_order_var = tk.StringVar(value="1,0,1,12")
        self.model_vars = {
            name: tk.BooleanVar(value=True)
            for name in ("Naive", "SeasonalNaive", "ETS", "ARIMA", "SARIMA")
        }
        self.output_dir_var = tk.StringVar(value=str(Path.home() / "PYTSA_results"))

        self.eda_column_var = tk.StringVar(value="")
        self.eda_unit_var = tk.StringVar(value="")
        self.eda_method_var = tk.StringVar(value="highpass")
        self.eda_clean_var = tk.StringVar(value="neurokit")
        self.eda_cutoff_var = tk.StringVar(value="0.05")
        self.eda_relative_var = tk.StringVar(value="0.1")
        self.eda_absolute_var = tk.StringVar(value="0.01")
        self.eda_window_var = tk.StringVar(value="60")
        self.eda_start_var = tk.StringVar(value="0")
        self.eda_end_var = tk.StringVar(value="")
        self.baseline_start_var = tk.StringVar(value="-2")
        self.baseline_end_var = tk.StringVar(value="0")
        self.response_start_var = tk.StringVar(value="1")
        self.response_end_var = tk.StringVar(value="5")
        self.event_source_var = tk.StringVar(value="无事件")
        self.event_time_col_var = tk.StringVar(value="")
        self.event_label_col_var = tk.StringVar(value="")
        self.marker_col_var = tk.StringVar(value="")
        self.eda_matrix_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="就绪。请选择一个文件、粘贴表格或载入合成示例。")

    def _configure_window(self) -> None:
        self.root.title(f"{APP_NAME} · 通用时间序列与实验信号分析")
        self.root.configure(bg=COLORS["canvas"])
        self.root.geometry("1380x880")
        self.root.minsize(1080, 720)
        self.root.option_add("*tearOff", False)
        icon = resource_path("assets/PYTSA.ico")
        if os.name == "nt" and icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except Exception:
                pass

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            font=(self.font_family, 10),
            rowheight=25,
            background=COLORS["white"],
            fieldbackground=COLORS["white"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["ink"],
            borderwidth=1,
        )
        style.configure(
            "Treeview.Heading",
            font=(self.font_family, 10, "bold"),
            background=COLORS["lavender"],
            foreground=COLORS["ink"],
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", COLORS["sky"])],
            foreground=[("selected", COLORS["ink"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=COLORS["white"],
            background=COLORS["white"],
            foreground=COLORS["ink"],
            arrowcolor=COLORS["ink"],
            padding=5,
        )
        style.configure(
            "TNotebook",
            background=COLORS["canvas"],
            borderwidth=0,
            tabmargins=(2, 2, 2, 0),
        )
        style.configure(
            "TNotebook.Tab",
            font=(self.font_family, 10, "bold"),
            padding=(12, 8),
            background=COLORS["lavender"],
            foreground=COLORS["ink"],
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["green"])],
            foreground=[("selected", COLORS["ink"])],
        )

    def _build_shell(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(self.root, bg=COLORS["sky"], width=245)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.workspace = tk.Frame(self.root, bg=COLORS["canvas"])
        self.workspace.grid(row=0, column=1, sticky="nsew", padx=(16, 18), pady=(16, 8))
        self.workspace.grid_rowconfigure(0, weight=1)
        self.workspace.grid_columnconfigure(0, weight=1)

        tk.Label(
            self.sidebar,
            text="PYTSA",
            bg=COLORS["sky"],
            fg=COLORS["ink"],
            font=(self.font_family, 25, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(22, 0))
        tk.Label(
            self.sidebar,
            text="TIME SERIES LAB",
            bg=COLORS["sky"],
            fg=COLORS["muted"],
            font=(self.font_family, 9, "bold"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 18))

        self.nav_host = tk.Frame(self.sidebar, bg=COLORS["sky"])
        self.nav_host.pack(fill="x", padx=12)
        for page_id in PAGE_ORDER:
            index, title, subtitle = PAGE_TITLES[page_id]
            btn = tk.Button(
                self.nav_host,
                text=f"{index}   {title}\n       {subtitle}",
                anchor="w",
                justify="left",
                wraplength=205,
                font=(self.font_family, 10, "bold"),
                fg=COLORS["ink"],
                bg=COLORS["white"],
                activebackground=COLORS["lemon"],
                activeforeground=COLORS["ink"],
                relief="solid",
                bd=2,
                padx=10,
                pady=10,
                command=lambda pid=page_id: self.show_page(pid),
            )
            btn.pack(fill="x", pady=5)
            self.page_buttons[page_id] = btn

        tk.Frame(self.sidebar, bg=COLORS["sky"]).pack(fill="both", expand=True)
        tk.Label(
            self.sidebar,
            text="离线分析 · 原始文件只读\n不连接传感器或远程服务",
            bg=COLORS["sky"],
            fg=COLORS["muted"],
            justify="left",
            font=(self.font_family, 9),
        ).pack(fill="x", padx=20, pady=(0, 18))

        self.status_bar = tk.Frame(
            self.root, bg=COLORS["lemon"], highlightbackground=COLORS["ink"], highlightthickness=2
        )
        self.status_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(2, 12))
        self.status_label = tk.Label(
            self.status_bar,
            textvariable=self.status_var,
            bg=COLORS["lemon"],
            fg=COLORS["ink"],
            anchor="w",
            font=(self.font_family, 9),
            padx=10,
            pady=6,
        )
        self.status_label.pack(fill="x")

    def _build_pages(self) -> None:
        self.pages = {}
        for page_id in PAGE_ORDER:
            page = ScrollablePage(self.workspace, COLORS["canvas"])
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self.pages[page_id] = page
        self._build_input_page(self.pages["input"].inner)
        self._build_prepare_page(self.pages["prepare"].inner)
        self._build_matrix_page(self.pages["matrices"].inner)
        self._build_forecast_page(self.pages["forecast"].inner)
        self._build_eda_page(self.pages["eda"].inner)
        self._build_workflow_page(self.pages["workflow"].inner)

    def _title_plaque(self, parent: tk.Misc, title: str, subtitle: str, color: str) -> None:
        plaque = tk.Frame(
            parent,
            bg=color,
            highlightbackground=COLORS["ink"],
            highlightthickness=2,
        )
        plaque.pack(fill="x", padx=10, pady=(10, 13))
        tk.Label(
            plaque,
            text=title,
            bg=color,
            fg=COLORS["ink"],
            anchor="w",
            font=(self.font_family, 18, "bold"),
            padx=14,
            pady=8,
        ).pack(fill="x")
        tk.Label(
            plaque,
            text=subtitle,
            bg=color,
            fg=COLORS["ink"],
            anchor="w",
            font=(self.font_family, 10),
            padx=14,
            pady=8,
        ).pack(fill="x")

    def _card(self, parent: tk.Misc, title: str, subtitle: str = "", color: str | None = None):
        outer = tk.Frame(parent, bg=COLORS["ink"], padx=0, pady=0)
        outer.pack(fill="x", padx=(10, 14), pady=(0, 12))
        card = tk.Frame(
            outer,
            bg=color or COLORS["white"],
            highlightbackground=COLORS["ink"],
            highlightthickness=2,
        )
        card.pack(fill="both", expand=True, padx=(0, 5), pady=(0, 5))
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text=title,
            bg=color or COLORS["white"],
            fg=COLORS["ink"],
            anchor="w",
            font=(self.font_family, 13, "bold"),
            padx=13,
            pady=8,
        ).grid(row=0, column=0, sticky="ew")
        if subtitle:
            tk.Label(
                card,
                text=subtitle,
                bg=color or COLORS["white"],
                fg=COLORS["muted"],
                anchor="w",
                justify="left",
                wraplength=980,
                font=(self.font_family, 9),
                padx=13,
                pady=4,
            ).grid(row=1, column=0, sticky="ew")
        card.content = tk.Frame(card, bg=color or COLORS["white"])
        card.content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(3, 12))
        card.grid_rowconfigure(2, weight=1)
        return card.content

    def _field(self, parent: tk.Misc, label: str, widget: tk.Widget, row: int, column: int = 0):
        background = parent.cget("bg")
        tk.Label(
            parent,
            text=label,
            bg=background,
            fg=COLORS["ink"],
            anchor="w",
            font=(self.font_family, 9, "bold"),
        ).grid(row=row * 2, column=column, sticky="ew", padx=6, pady=(5, 1))
        widget.grid(row=row * 2 + 1, column=column, sticky="ew", padx=6, pady=(0, 5))
        return widget

    def _entry(self, parent: tk.Misc, variable: tk.StringVar, width: int = 18) -> tk.Entry:
        widget = tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            relief="flat",
            bd=0,
            bg=COLORS["white"],
            fg=COLORS["ink"],
            insertbackground=COLORS["ink"],
            highlightbackground=COLORS["ink"],
            highlightcolor=COLORS["green"],
            highlightthickness=2,
            font=(self.font_family, 10),
        )
        self._workflow_register_editable(widget)
        return widget

    def _combo(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        values: list[str],
        width: int = 20,
        readonly: bool = True,
    ) -> ttk.Combobox:
        widget = ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            width=width,
            state="readonly" if readonly else "normal",
            style="TCombobox",
            font=(self.font_family, 10),
        )
        self._workflow_register_editable(widget)
        return widget

    def _action_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        color: str = "green",
        track_busy: bool = True,
    ) -> tk.Button:
        wrapper = tk.Frame(parent, bg=COLORS["ink"])
        button = tk.Button(
            wrapper,
            text=text,
            command=command,
            font=(self.font_family, 10, "bold"),
            fg=COLORS["ink"],
            bg=COLORS[color],
            activebackground=COLORS[color],
            activeforeground=COLORS["ink"],
            relief="solid",
            bd=2,
            padx=12,
            pady=6,
            cursor="hand2",
        )
        button.pack(fill="both", expand=True, padx=(0, 4), pady=(0, 4))
        wrapper.pack_propagate(True)
        if track_busy:
            button.idle_state = tk.NORMAL
            self.action_buttons.append(button)
        button.shadow_wrapper = wrapper
        return button

    def _label(self, parent: tk.Misc, text: str, color: str | None = None, **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=color or COLORS["ink"],
            font=(self.font_family, 9),
            justify="left",
            **kwargs,
        )

    def _make_table(self, parent: tk.Misc, key: str, height: int = 10) -> ttk.Treeview:
        holder = tk.Frame(parent, bg=parent.cget("bg"))
        holder.pack(fill="both", expand=True, padx=4, pady=4)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, show="headings", height=height, selectmode="browse")
        ybar = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.table_widgets[key] = tree
        return tree

    def _set_table(self, key: str, data, max_rows: int = 500) -> None:
        tree = self.table_widgets.get(key)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        if data is None:
            tree["columns"] = ()
            return
        pd = _pandas()
        if not hasattr(data, "columns"):
            try:
                data = pd.DataFrame(data)
            except Exception:
                tree["columns"] = ("value",)
                tree.heading("value", text="结果")
                tree.insert("", "end", values=(str(data),))
                return
        columns = [str(column) for column in data.columns]
        if not columns:
            tree["columns"] = ("message",)
            tree.heading("message", text="结果")
            tree.insert("", "end", values=("无可显示记录",))
            return
        ids = [f"c{i}" for i in range(len(columns))]
        tree["columns"] = ids
        for item_id, label in zip(ids, columns):
            tree.heading(item_id, text=label)
            width = max(110, min(420, tkfont.Font(family=self.font_family, size=10, weight="bold").measure(label) + 32))
            tree.column(item_id, width=width, minwidth=width, stretch=False, anchor="center")
        for row in data.head(max_rows).itertuples(index=False, name=None):
            values = [self._format_value(value) for value in row]
            tree.insert("", "end", values=values)
        if len(data) > max_rows:
            tree.insert("", "end", values=[f"仅显示前 {max_rows} 行；统计使用全量数据。"] + [""] * (len(columns) - 1))

    @staticmethod
    def _format_value(value) -> str:
        if value is None:
            return "NA"
        try:
            if hasattr(value, "item"):
                value = value.item()
        except Exception:
            pass
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return "NA"
            return f"{value:.5g}"
        try:
            if value != value:
                return "NA"
        except Exception:
            pass
        text = str(value)
        return text if len(text) < 180 else text[:177] + "…"

    def show_page(self, page_id: str) -> None:
        for key, page in self.pages.items():
            page.grid_remove()
            button = self.page_buttons.get(key)
            if button is not None:
                active = key == page_id
                button.configure(
                    bg=COLORS["green"] if active else COLORS["white"],
                    relief="raised" if active else "solid",
                    bd=3 if active else 2,
                )
        self.pages[page_id].grid()
        self.pages[page_id].canvas.yview_moveto(0)
        self.current_page = page_id

    def _set_status(self, message: str, warning: bool = False) -> None:
        self.status_var.set(message)
        self.status_label.configure(
            fg=COLORS["warning"] if warning else COLORS["ink"],
            bg=COLORS["coral"] if warning else COLORS["lemon"],
        )

    def _build_input_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "数据输入",
            "载入一个本地连续记录，预览列与采样信息；原始文件保持只读。",
            COLORS["sky"],
        )
        source = self._card(
            parent,
            "选择数据来源",
            "支持 CSV、TSV、TXT、XLSX、剪贴板与内置合成示例。每个文件按单个被试/条件的一段连续记录处理；请先筛选好单次记录，不会自动拼接多名被试，也不连接传感器。",
            COLORS["white"],
        )
        source.grid_columnconfigure(1, weight=1)
        self._label(source, "文件 / 当前来源").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.source_display = tk.Label(
            source,
            textvariable=self.source_var,
            bg=COLORS["white"],
            fg=COLORS["muted"],
            anchor="w",
            font=(self.font_family, 9),
            relief="solid",
            bd=1,
            padx=8,
            pady=5,
        )
        self.source_display.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        file_buttons = tk.Frame(source, bg=COLORS["white"])
        file_buttons.grid(row=0, column=2, sticky="e", padx=6, pady=4)
        for text, command, color in (
            ("选择文件", self.choose_file, "green"),
            ("读取剪贴板", self.load_clipboard, "sky"),
            ("合成示例", self.load_demo, "coral"),
        ):
            button = self._action_button(file_buttons, text, command, color)
            button.shadow_wrapper.pack(side="left", padx=(0, 6))

        options = tk.Frame(source, bg=COLORS["white"])
        options.grid(row=1, column=0, columnspan=3, sticky="ew")
        for column in range(5):
            options.grid_columnconfigure(column, weight=1)
        self.sheet_combo = self._combo(options, self.sheet_var, ["首个工作表"], width=18)
        self.encoding_combo = self._combo(
            options, self.encoding_var, ["utf-8-sig", "utf-8", "gb18030", "cp1252"], width=15
        )
        self.separator_combo = self._combo(
            options, self.separator_var, ["自动识别", "逗号 ,", "制表符", "分号 ;", "竖线 |"], width=15
        )
        self._field(options, "Excel 工作表", self.sheet_combo, 0, 0)
        self._field(options, "文本编码", self.encoding_combo, 0, 1)
        self._field(options, "分隔符", self.separator_combo, 0, 2)
        reload_row = tk.Frame(options, bg=COLORS["white"])
        reload_row.grid(row=0, column=3, columnspan=2, sticky="ew", padx=6, pady=(20, 2))
        reload_btn = self._action_button(
            reload_row, "读取所选工作表 / 格式", self.reload_selected_source, "lavender"
        )
        reload_btn.shadow_wrapper.pack(side="left")

        fields = self._card(
            parent,
            "时间列与分析变量",
            "无时间列时以采样率生成等间隔时间轴；数值时间的单位在下方选择，也可使用日期时间。",
            COLORS["mint"],
        )
        fields.grid_columnconfigure(1, weight=1)
        fields.grid_columnconfigure(3, weight=1)
        self.time_combo = self._combo(
            fields, self.time_col_var, ["（无时间列）"], width=22
        )
        self.time_kind_combo = self._combo(
            fields, self.time_kind_var, ["自动识别", "数值时间", "日期时间"], width=15
        )
        self.fs_entry = self._entry(fields, self.fs_var, width=12)
        self._field(fields, "时间列", self.time_combo, 0, 0)
        self._field(fields, "时间格式", self.time_kind_combo, 0, 1)
        self._field(fields, "采样率 fs（Hz）", self.fs_entry, 0, 2)
        self._label(
            fields,
            "选中一个或多个数值列。采样率需填写真实记录值；不规则时间会在后续提示等间隔方法的处理要求。",
            color=COLORS["muted"],
            wraplength=950,
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(5, 4))
        self.value_list = tk.Listbox(
            fields,
            selectmode=tk.EXTENDED,
            height=7,
            exportselection=False,
            bg=COLORS["white"],
            fg=COLORS["ink"],
            selectbackground=COLORS["lavender"],
            selectforeground=COLORS["ink"],
            relief="solid",
            bd=2,
            font=(self.font_family, 10),
        )
        self.value_list.grid(row=5, column=0, columnspan=4, sticky="ew", padx=6, pady=4)
        self._workflow_register_editable(self.value_list)

        self._additional_card(parent, "prepare", "时间单位与时区",
                              "s=秒，ms=毫秒，us=微秒。数值时间转换为秒；无时区日期按所填时区解释，例如 Asia/Shanghai。",
                              COLORS["sky"], keys={"time_scale", "timezone"})
        preview = self._card(
            parent,
            "数据预览",
            "只显示前 20 行供检查；显示截断不改变后续统计使用的完整数据。",
            COLORS["lemon"],
        )
        self.preview_summary = self._label(preview, "尚未载入数据", color=COLORS["muted"])
        self.preview_summary.pack(fill="x", padx=5, pady=(0, 6))
        self._make_table(preview, "preview", height=11)

    def _build_prepare_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "预处理与分析",
            "分析前明确缺失值、时间重复、重采样和变换规则；原始数据不被改写。",
            COLORS["mint"],
        )
        controls = self._card(
            parent,
            "探索分析设置",
            "缺失值默认拒绝；前向填充和线性插值只用于探索性分析。异常值按 1.5×IQR 标记并保留，不会自动删除。",
            COLORS["white"],
        )
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)
        self.missing_combo = self._combo(
            controls,
            self.missing_var,
            ["拒绝缺失值", "前向填充（仅探索）", "线性插值（仅探索）"],
        )
        self.duplicate_combo = self._combo(
            controls,
            self.duplicate_var,
            ["重复时间：报错", "重复时间：取均值", "重复时间：保留首条"],
        )
        self.transform_combo = self._combo(
            controls,
            self.transform_var,
            ["不变换", "去均值", "去线性趋势", "z-score 标准化", "一阶差分"],
        )
        self.resample_entry = self._entry(controls, self.resample_var)
        self._field(controls, "缺失值处理", self.missing_combo, 0, 0)
        self._field(controls, "重复时间处理", self.duplicate_combo, 0, 1)
        self._field(controls, "探索性变换", self.transform_combo, 1, 0)
        self._field(controls, "重采样间隔（秒；留空不重采样）", self.resample_entry, 1, 1)

        self._additional_card(parent, "prepare", "填补上限与通用滤波",
                              "上限按连续缺失段检查。lowpass=低通，highpass=高通，bandpass=带通；zero=零相位，causal=因果滤波。",
                              COLORS["mint"], keys={"max_gap_seconds", "max_gap_samples", "filter_type", "filter_low", "filter_high", "filter_order", "filter_phase"})
        measure = self._card(
            parent,
            "指标参数",
            "样本不足、常数序列或零分母会得到 NA / 跳过说明，不伪造为 0。",
            COLORS["lavender"],
        )
        measure.grid_columnconfigure(0, weight=1)
        measure.grid_columnconfigure(1, weight=1)
        self.max_lag_entry = self._entry(measure, self.max_lag_var)
        self.period_entry = self._entry(measure, self.period_var)
        self.lag_entry = self._entry(measure, self.lag_var)
        self.band_low_entry = self._entry(measure, self.band_low_var)
        self.band_high_entry = self._entry(measure, self.band_high_var)
        self.nperseg_entry = self._entry(measure, self.nperseg_var)
        self._field(measure, "ACF / PACF 最大滞后", self.max_lag_entry, 0, 0)
        self._field(measure, "季节周期（样本点）", self.period_entry, 0, 1)
        self._field(measure, "定向滞后（样本点）", self.lag_entry, 1, 0)
        self._field(measure, "相干频带下限（Hz）", self.band_low_entry, 1, 1)
        self._field(measure, "相干频带上限（Hz；留空至 Nyquist）", self.band_high_entry, 2, 0)
        self._field(measure, "Welch 分段长度", self.nperseg_entry, 2, 1)
        self._label(
            measure,
            "频谱 / 相干要求等间隔且对齐的序列。相干使用多个 Welch 分段；采样过短、空频带或常数信号时会说明未定义。",
            color=COLORS["muted"],
            wraplength=940,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=5)

        self._additional_card(parent, "analysis", "频谱与滑动统计",
                              "功率汇总使用上面的频带范围；可另设多个命名频段（Hz）。步长留空时由重叠比例计算；明确步长优先。末尾不足窗口保留并标记 PARTIAL。",
                              COLORS["lavender"])
        action = self._card(
            parent,
            "运行探索性分析",
            "将依次准备数据、计算描述统计与检验、绘制常用结构图和七类变量矩阵。",
            COLORS["coral"],
        )
        action_row = tk.Frame(action, bg=COLORS["coral"])
        action_row.pack(fill="x", padx=5, pady=3)
        run_btn = self._action_button(action_row, "准备数据并分析", self.run_analysis, "green")
        run_btn.shadow_wrapper.pack(side="left", padx=(0, 10))
        self.analysis_summary = self._label(
            action_row,
            "尚未运行；请先在“数据输入”页读取文件并选择至少一个数值变量。",
            color=COLORS["muted"],
            wraplength=850,
        )
        self.analysis_summary.pack(side="left", fill="x", expand=True, padx=5)

        results = self._card(
            parent,
            "概览表与诊断",
            "结果不替代领域判断；ADF 与 KPSS 原假设相反，检验结论需结合趋势、周期和样本量解释。",
            COLORS["sky"],
        )
        notebook = ttk.Notebook(results)
        notebook.pack(fill="both", expand=True, padx=4, pady=4)
        for key, title in (
            ("summary", "描述统计"),
            ("band_power", "频段功率"),
            ("windows", "滑动统计"),
            ("tests", "平稳性与残差检验"),
            ("outliers", "异常值标记"),
        ):
            tab = tk.Frame(notebook, bg=COLORS["white"])
            notebook.add(tab, text=title)
            self._make_table(tab, key, height=9)

    def _build_matrix_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "指标矩阵与图表",
            "矩阵和图表使用完整分析结果；图形可抽样显示，统计值仍使用全部有效样本。",
            COLORS["lavender"],
        )
        matrix = self._card(
            parent,
            "变量关系矩阵",
            "偏相关矩阵遇到奇异/秩不足时按 NA 处理；定向滞后相关的行是来源变量、列是目标变量。所有关系指标均不代表因果。",
            COLORS["white"],
        )
        matrix.grid_columnconfigure(1, weight=1)
        matrix.grid_rowconfigure(1, weight=1)
        tk.Label(
            matrix,
            text="矩阵类型",
            bg=COLORS["white"],
            fg=COLORS["ink"],
            font=(self.font_family, 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.matrix_combo = self._combo(matrix, self.eda_matrix_var, [], width=34)
        self.matrix_combo.grid(row=0, column=1, sticky="w", padx=6, pady=6)
        self.matrix_combo.bind("<<ComboboxSelected>>", self._on_matrix_selected)
        self.matrix_note = self._label(
            matrix,
            "Pearson / Spearman / 协方差 / 偏相关 / 指定滞后相关 / 频带平均平方相干 / 标准化欧式距离",
            color=COLORS["muted"],
            wraplength=900,
        )
        self.matrix_note.grid(row=0, column=2, sticky="e", padx=6, pady=6)
        table_host = tk.Frame(matrix, bg=COLORS["white"])
        table_host.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.matrix_tree = self._make_table(table_host, "matrix", height=13)

        charts = self._card(
            parent,
            "时间结构图",
            "ACF / PACF 查看滞后关系；Welch PSD 查看频率功率；STL 显示趋势、季节与残差分量。",
            COLORS["lemon"],
        )
        self.chart_notebook = ttk.Notebook(charts)
        self.chart_notebook.pack(fill="both", expand=True, padx=4, pady=4)
        self.chart_frames: dict[str, tk.Frame] = {}
        for key, title in (
            ("series", "时域"),
            ("acf", "ACF / PACF"),
            ("psd", "Welch PSD"),
            ("decomposition", "STL 分解"),
        ):
            frame = tk.Frame(self.chart_notebook, bg=COLORS["white"])
            self.chart_notebook.add(frame, text=title)
            self.chart_frames[key] = frame
            self._chart_placeholder(frame, "运行分析后显示图表。")

    def _build_forecast_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "预测、导出与方法",
            "预测从原始尺度的独立分支开始；留后表现只代表当前记录与留后段，不是外部验证。",
            COLORS["coral"],
        )
        self.forecast_notebook = ttk.Notebook(parent)
        self.forecast_notebook.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.forecast_tab = tk.Frame(self.forecast_notebook, bg=COLORS["canvas"])
        self.export_tab = tk.Frame(self.forecast_notebook, bg=COLORS["canvas"])
        self.methods_tab = tk.Frame(self.forecast_notebook, bg=COLORS["canvas"])
        self.forecast_notebook.add(self.forecast_tab, text="预测")
        self.forecast_notebook.add(self.export_tab, text="结果导出")
        self.forecast_notebook.add(self.methods_tab, text="方法与文献")
        self._build_forecast_controls(self.forecast_tab)
        self._build_export_controls(self.export_tab)
        self._build_methods_page(self.methods_tab)

    def _build_eda_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "实验 / 皮电",
            "此页面面向单个连续实验记录中的皮电通道；离线处理，不连接设备、不推断压力、情绪或疾病。",
            COLORS["mint"],
        )
        self._build_eda_controls(parent)
        self._on_event_source_changed()
        self._on_eda_unit_changed()

    def _chart_placeholder(self, parent: tk.Misc, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=COLORS["white"],
            fg=COLORS["muted"],
            anchor="center",
            font=(self.font_family, 10),
            pady=24,
        ).pack(fill="both", expand=True)

    def _start_worker(
        self,
        title: str,
        operation,
        on_success,
        *,
        cancelable: bool = False,
        contextual: bool = False,
    ) -> None:
        if self.busy:
            self._set_status("已有任务正在运行，请稍候。", warning=True)
            return
        task_id = uuid.uuid4().hex
        cancel_event = threading.Event() if cancelable else None
        started_at = time.monotonic()
        self._active_task_id = task_id
        self._active_cancel_event = cancel_event
        self.busy = True
        self._set_busy_state(True)
        self._set_status(f"{title}…")
        self._workflow_on_task_started(task_id, title, cancel_event)

        def progress(event):
            if isinstance(event, str):
                event = {"stage": title, "message": event}
            elif not isinstance(event, dict):
                event = {"stage": title, "message": str(event)}
            payload = dict(event)
            payload.setdefault("elapsed_seconds", max(0.0, time.monotonic() - started_at))
            self.task_queue.put(("progress", task_id, title, payload))

        def worker():
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise WorkflowCancelled("任务已取消。")
                result = operation(cancel_event, progress) if contextual else operation()
                if cancel_event is not None and not contextual and cancel_event.is_set():
                    raise WorkflowCancelled("任务已在当前操作返回后取消。")
                self._workflow_write_log(
                    self._workflow_result_log_id(result) or task_id,
                    "INFO",
                    f"{title}完成。",
                    details={"task_id": task_id},
                )
                self.task_queue.put(("done", task_id, title, result, None, on_success, False))
            except Exception as exc:
                cancelled = isinstance(exc, WorkflowCancelled) or (
                    exc.__class__.__name__ == "ExportCancelledError"
                    or (cancel_event is not None and cancel_event.is_set())
                )
                trace = traceback.format_exc()
                self._workflow_write_log(
                    task_id,
                    "WARNING" if cancelled else "ERROR",
                    f"{title}{'已取消' if cancelled else '失败'}：{exc}",
                    details={"task_id": task_id},
                    exception=None if cancelled else trace,
                )
                self.task_queue.put(
                    ("done", task_id, title, None, f"{exc}\n\n{trace}", on_success, cancelled)
                )

        threading.Thread(target=worker, name=f"pytsa-{title}", daemon=True).start()

    @staticmethod
    def _workflow_result_log_id(result) -> str:
        if isinstance(result, dict):
            for key in ("history_run_id", "run_id", "batch_id"):
                value = result.get(key)
                if value:
                    return str(value)
        return ""

    def _drain_worker_queue(self) -> None:
        try:
            while True:
                event = self.task_queue.get_nowait()
                if event[0] == "progress":
                    _, task_id, _title, payload = event
                    self._workflow_on_task_progress(task_id, payload)
                    continue
                _, task_id, title, result, error, callback, cancelled = event
                if task_id != self._active_task_id:
                    continue
                self.busy = False
                self._active_cancel_event = None
                self._set_busy_state(False)
                self._workflow_on_task_finished(task_id, cancelled)
                if error:
                    short_error = error.split("\n\n", 1)[0]
                    if cancelled:
                        self._set_status(f"{title}已取消。", warning=True)
                    else:
                        self._set_status(
                            f"{title}失败：{short_error}（任务 ID：{task_id}）", warning=True
                        )
                        messagebox.showerror(
                            f"{title}失败",
                            f"{short_error}\n\n任务 ID：{task_id}",
                            parent=self.root,
                        )
                else:
                    try:
                        callback(result)
                    except Exception as exc:
                        self._set_status(f"显示结果时出错：{exc}", warning=True)
                        self._workflow_write_log(
                            task_id,
                            "ERROR",
                            f"显示 {title} 结果时出错：{exc}",
                            exception=traceback.format_exc(),
                        )
                        messagebox.showerror(
                            "显示结果时出错", f"{exc}\n\n任务 ID：{task_id}", parent=self.root
                        )
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._drain_worker_queue)

    def _set_busy_state(self, busy: bool) -> None:
        for button in self.action_buttons:
            try:
                if busy:
                    button.configure(state=tk.DISABLED)
                else:
                    button.configure(state=getattr(button, "idle_state", tk.NORMAL))
            except tk.TclError:
                pass
        self._workflow_set_busy_state(busy)

    def _separator_value(self):
        return {
            "自动识别": None,
            "逗号 ,": ",",
            "制表符": "\t",
            "分号 ;": ";",
            "竖线 |": "|",
        }.get(self.separator_var.get())

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择时间序列文件",
            filetypes=[
                ("支持的文件", "*.csv *.tsv *.txt *.xlsx"),
                ("CSV", "*.csv"),
                ("TSV / TXT", "*.tsv *.txt"),
                ("Excel", "*.xlsx"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.raw_path = Path(path)
        self.source_var.set(path)
        self.reload_selected_source()

    def reload_selected_source(self) -> None:
        path = self.raw_path
        if path is None or not path.is_file():
            typed = self.source_var.get().strip()
            path = Path(typed) if typed else None
        if path is None or not path.is_file():
            messagebox.showinfo("需要选择文件", "请先选择 CSV、TSV、TXT 或 XLSX 文件。", parent=self.root)
            return
        suffix = path.suffix.lower()
        if suffix not in {".csv", ".tsv", ".txt", ".xlsx"}:
            messagebox.showerror("不支持的文件", "本版输入支持 CSV、TSV、TXT 与 XLSX。", parent=self.root)
            return
        sheet_choice = self.sheet_var.get().strip()
        if not self.excel_sheets:
            sheet_choice = ""
        encoding = self.encoding_var.get().strip() or "utf-8-sig"
        separator = self._separator_value()
        chosen_sheet = sheet_choice

        def load():
            import core

            pd = _pandas()
            sheets = []
            if suffix == ".xlsx":
                with pd.ExcelFile(path) as workbook:
                    sheets = list(workbook.sheet_names)
                if not sheets:
                    raise ValueError("Excel 文件中没有可读取的工作表。")
                sheet = chosen_sheet if chosen_sheet in sheets else sheets[0]
            else:
                sheet = 0
            frame = core.load_table(
                path,
                sheet_name=sheet,
                encoding=encoding,
                sep=separator,
            )
            self._workflow_remember_data_in_worker(path, str(path))
            return frame, sheets, sheet

        def loaded(result):
            frame, sheets, sheet = result
            self.raw_path = path
            self.raw_source_label = str(path)
            self.excel_sheets = sheets
            self.sheet_combo.configure(values=sheets or ["（不适用）"])
            self.sheet_var.set(sheet if sheets else "（不适用）")
            self._accept_dataframe(frame, self.raw_source_label)

        self._start_worker("读取数据", load, loaded)

    def load_clipboard(self) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("剪贴板为空", "没有可读取的表格文本。", parent=self.root)
            return
        if not text.strip():
            messagebox.showerror("剪贴板为空", "没有可读取的表格文本。", parent=self.root)
            return

        def load():
            pd = _pandas()
            return pd.read_csv(io.StringIO(text), sep=None, engine="python")

        def loaded(frame):
            self.raw_path = None
            self.excel_sheets = []
            self.sheet_combo.configure(values=["（不适用）"])
            self.sheet_var.set("（不适用）")
            self.raw_source_label = "剪贴板（本地粘贴表格）"
            self._accept_dataframe(frame, self.raw_source_label)

        self._start_worker("读取剪贴板", load, loaded)

    def load_demo(self) -> None:
        def load():
            import core

            return core.demo_data()

        def loaded(frame):
            self.raw_path = None
            self.excel_sheets = []
            self.raw_source_label = "合成演示数据（SYNTHETIC_ONLY）"
            self.sheet_combo.configure(values=["（不适用）"])
            self.sheet_var.set("（不适用）")
            self._accept_dataframe(frame, self.raw_source_label)
            self._select_time_and_signal_columns(time_hint=None, preferred=None)

        self._start_worker("生成合成示例", load, loaded)

    def load_eda_demo(self) -> None:
        def load():
            import experimental

            return experimental.demo_eda()

        def loaded(frame):
            self.raw_path = None
            self.excel_sheets = []
            self.raw_source_label = "皮电合成演示（SYNTHETIC_ONLY；单位 uS）"
            self.sheet_combo.configure(values=["（不适用）"])
            self.sheet_var.set("（不适用）")
            self._accept_dataframe(frame, self.raw_source_label)
            self._select_time_and_signal_columns(time_hint="time", preferred=["EDA_uS"])
            self.fs_var.set("10")
            self.time_kind_var.set("数值时间")
            self.eda_unit_var.set("uS")
            self.event_source_var.set("原始标签列")
            self._on_event_source_changed()
            self.marker_combo.set("event")
            self.eda_column_combo.set("EDA_uS")
            self._set_status("皮电合成示例已载入；单位 uS 已明确设置。")
            self._on_eda_unit_changed()
            if self.capture_mode:
                if not self.capture_eda_only:
                    self.model_vars["Naive"].set(True)
                    self.model_vars["SeasonalNaive"].set(True)
                    for name in ("ETS", "ARIMA", "SARIMA"):
                        self.model_vars[name].set(False)
                self.root.after(250, self.run_analysis)

        self._start_worker("生成皮电示例", load, loaded)

    def _accept_dataframe(self, frame, source_label: str) -> None:
        pd = _pandas()
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("导入结果不是表格数据。")
        if frame.empty:
            raise ValueError("表格为空。")
        if len(frame.columns) == 0:
            raise ValueError("表格没有列。")
        string_names = [str(column) for column in frame.columns]
        if len(set(string_names)) != len(string_names):
            raise ValueError(
                "列名转换为界面文字后会重复（例如 1 与 '1'）；请先修改源文件列名，程序不会合并重复列。"
            )
        frame = frame.copy(deep=False)
        frame.columns = string_names
        if not frame.columns.is_unique:
            raise ValueError("表格列名重复，请先修正列名后再导入。")
        if any(str(column).strip() == "" for column in frame.columns):
            raise ValueError("表格包含空列名，请先修正列名后再导入。")
        self.raw_frame = frame
        self.raw_source_label = source_label
        self.source_var.set(source_label)
        self._clear_results()
        self._populate_input_columns()
        self._set_table("preview", frame.head(20), max_rows=20)
        self.preview_summary.configure(
            text=f"{len(frame):,} 行 × {len(frame.columns)} 列；来源：{source_label}"
        )
        self._set_status(f"已读入 {len(frame):,} 行、{len(frame.columns)} 列；原文件保持不变。")
        self._workflow_on_source_accepted()

    def _populate_input_columns(self) -> None:
        if self.raw_frame is None:
            return
        columns = [str(column) for column in self.raw_frame.columns]
        time_values = ["（无时间列）"] + columns
        self.time_combo.configure(values=time_values)
        numeric = [
            str(column)
            for column in self.raw_frame.columns
            if getattr(self.raw_frame[column].dtype, "kind", "") in "iuf"
        ]
        self.value_list.delete(0, tk.END)
        for column in columns:
            label = column + ("  · 数值" if column in numeric else "  · 非数值")
            self.value_list.insert(tk.END, label)
        self._select_time_and_signal_columns()
        if hasattr(self, "eda_column_combo"):
            self.eda_column_combo.configure(values=numeric)
            self.marker_combo.configure(values=columns)
        if hasattr(self, "forecast_column_combo"):
            self.forecast_column_combo.configure(values=numeric)

    def _select_time_and_signal_columns(
        self, time_hint: str | None = None, preferred: list[str] | None = None
    ) -> None:
        if self.raw_frame is None:
            return
        columns = [str(column) for column in self.raw_frame.columns]
        if time_hint in columns:
            self.time_col_var.set(time_hint)
        elif self.time_col_var.get() in ["", "（无时间列）"] or self.time_col_var.get() not in columns:
            likely_names = {
                "time", "time_s", "time_sec", "time_seconds", "timestamp",
                "datetime", "date", "relative_time", "elapsed_seconds",
            }
            candidate = next(
                (column for column in columns if column.casefold() in likely_names),
                None,
            )
            self.time_col_var.set(candidate or "（无时间列）")
        numeric = [
            str(column)
            for column in self.raw_frame.columns
            if getattr(self.raw_frame[column].dtype, "kind", "") in "iuf"
        ]
        if preferred:
            selected = [column for column in preferred if column in numeric]
        else:
            selected = [
                column
                for column in numeric
                if column != self.time_col_var.get()
                and column.lower() not in {"time", "timestamp", "datetime", "date"}
            ]
        self.value_list.selection_clear(0, tk.END)
        for index, column in enumerate(columns):
            if column in selected:
                self.value_list.selection_set(index)
        if selected:
            self.eda_column_combo.set(selected[0])
            self.forecast_col_var.set(selected[0])
            self.forecast_column_combo.set(selected[0])

    def _clear_results(self) -> None:
        self.prep_config_used = {}
        self.analysis_config_used = {}
        self.forecast_config_used = {}
        self.eda_config_used = {}
        self.prepared = None
        self.analysis = None
        self.forecast_result = None
        self.eda_result = None
        self.eda_meta = None
        self.event_frame = None
        for key in (
            "summary",
            "band_power",
            "windows",
            "tests",
            "outliers",
            "matrix",
            "forecast_metrics",
            "forecast_backtest",
            "forecast_future",
            "forecast_diagnostics",
            "forecast_rolling_metrics",
            "forecast_rolling_predictions",
            "eda_windows",
            "eda_peaks",
            "eda_events",
            "eda_event_summary",
        ):
            self._set_table(key, None)
        if hasattr(self, "analysis_summary"):
            self.analysis_summary.configure(text="数据已更换；请重新运行分析。")
        if hasattr(self, "forecast_status"):
            self.forecast_status.configure(text="等待分析结果。")
        if hasattr(self, "eda_status"):
            self.eda_status.configure(text="等待皮电输入与单位选择。")
        if hasattr(self, "matrix_combo"):
            self.matrix_combo.configure(values=[])
            self.matrix_combo.set("")
        self._clear_chart_widgets()

    def _value_columns(self) -> list[str]:
        if self.raw_frame is None:
            return []
        columns = [str(column) for column in self.raw_frame.columns]
        selected = []
        for index in self.value_list.curselection():
            name = self.value_list.get(index).split("  · ", 1)[0]
            if name in columns:
                selected.append(name)
        return selected

    def _parse_float(self, variable: tk.StringVar, label: str, *, allow_blank=False):
        text = variable.get().strip()
        if allow_blank and not text:
            return None
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"{label}必须是数值。") from exc
        if not math.isfinite(value):
            raise ValueError(f"{label}必须是有限数值。")
        return value

    def _parse_int(self, variable: tk.StringVar, label: str, *, minimum: int = 1) -> int:
        text = variable.get().strip()
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if value < minimum:
            raise ValueError(f"{label}不能小于 {minimum}。")
        return value

    def _collect_prepare_config(self) -> tuple[dict, dict]:
        if self.raw_frame is None:
            raise ValueError("请先载入一个数据表。")
        values = self._value_columns()
        if not values:
            raise ValueError("请至少选择一个数值变量。")
        fs = self._parse_float(self.fs_var, "采样率 fs")
        if fs <= 0:
            raise ValueError("采样率 fs 必须大于 0。")
        selected_time = self.time_col_var.get()
        time_col = None if selected_time == "（无时间列）" else selected_time
        if time_col in values:
            raise ValueError("时间列不能同时作为信号变量；请在变量列表中取消选中该列。")
        time_kind = {
            "自动识别": "auto",
            "数值秒": "numeric",
            "数值时间": "numeric",
            "日期时间": "datetime",
        }.get(self.time_kind_var.get(), "auto")
        resample = self._parse_float(self.resample_var, "重采样间隔", allow_blank=True)
        if resample is not None and resample <= 0:
            raise ValueError("重采样间隔必须大于 0 秒。")
        prep_config = {
            **self._additional_config("prepare"),
            "value_cols": values,
            "time_col": time_col,
            "time_kind": time_kind,
            "fs": fs,
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
            "resample": resample,
            "transform": {
                "不变换": "none",
                "去均值": "demean",
                "去线性趋势": "detrend",
                "z-score 标准化": "zscore",
                "一阶差分": "difference",
            }[self.transform_var.get()],
        }
        analysis_config = {
            **self._additional_config("analysis"),
            "max_lag": self._parse_int(self.max_lag_var, "最大滞后"),
            "period": self._parse_int(self.period_var, "季节周期", minimum=2),
            "lag": self._parse_int(self.lag_var, "定向滞后"),
            "band_low": self._parse_float(self.band_low_var, "频带下限"),
            "band_high": self._parse_float(self.band_high_var, "频带上限", allow_blank=True),
            "nperseg": self._parse_int(self.nperseg_var, "Welch 分段长度", minimum=2),
        }
        if analysis_config["band_low"] < 0:
            raise ValueError("频带下限不能小于 0 Hz。")
        if analysis_config["band_high"] is not None and analysis_config["band_high"] <= analysis_config["band_low"]:
            raise ValueError("频带上限必须高于下限。")
        if len(values) > 32:
            self._set_status(
                "变量数超过 32；矩阵计算的时间和内存会增加，数值仍使用全部样本。",
                warning=True,
            )
        return prep_config, analysis_config

    def run_analysis(self) -> None:
        try:
            prep_config, analysis_config = self._collect_prepare_config()
        except Exception as exc:
            messagebox.showerror("设置不完整", str(exc), parent=self.root)
            return
        raw = self.raw_frame
        source = self.raw_source_label
        source_info = copy.deepcopy(self.workflow_source_info)
        parameters = {
            "prepare": copy.deepcopy(prep_config),
            "analysis": copy.deepcopy(analysis_config),
            "annotations": copy.deepcopy(self.workflow_annotations),
        }

        def run(cancel_event, progress):
            import core

            # Keep the imported frame as a read-only snapshot for all analysis branches.
            snapshot = raw.copy(deep=True)
            progress({"stage": "预处理", "message": "正在整理时间与信号列。"})
            prepared = core.prepare_data(snapshot, prep_config)
            self._workflow_check_cancel(cancel_event)
            n = len(prepared["data"])
            config = dict(analysis_config)
            config["max_lag"] = min(config["max_lag"], max(1, n - 1))
            # Welch coherence needs at least two segments. The core routine also guards
            # very short records and returns an explicit warning rather than a fake value.
            if n >= 4:
                config["nperseg"] = min(config["nperseg"], n // 2)
            progress({"stage": "统计分析", "message": "正在计算指标与图表数据。"})
            analysis = core.analyze(prepared, config)
            self._workflow_check_cancel(cancel_event)
            tables = analysis.setdefault("tables", {})
            if "outliers" not in tables:
                tables["outliers"] = self._compute_outlier_flags(prepared["data"], prep_config["value_cols"])
            parameters["analysis"] = copy.deepcopy(config)
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "保存历史", "message": "正在保存本次不可变结果快照。"})
            history_run_id, history_error = self._workflow_record_history(
                "analysis", prepared, analysis, None, None, parameters, source_info
            )
            return {
                "payload": (prepared, analysis, config, source, prep_config),
                "history_run_id": history_run_id,
                "history_error": history_error,
            }

        self._workflow_start_task("预处理与分析", run, self._analysis_complete)

    @staticmethod
    def _compute_outlier_flags(frame, columns):
        pd = _pandas()
        rows = []
        for column in columns:
            series = pd.to_numeric(frame[column], errors="coerce").dropna()
            n = len(series)
            if n < 4:
                rows.append(
                    {
                        "variable": column,
                        "method": "1.5×IQR",
                        "lower_fence": float("nan"),
                        "upper_fence": float("nan"),
                        "valid_n": n,
                        "flagged_n": 0,
                        "status": "样本不足，未判定",
                    }
                )
                continue
            q1, q3 = series.quantile([0.25, 0.75]).tolist()
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            flagged = int(((series < low) | (series > high)).sum())
            rows.append(
                {
                    "variable": column,
                    "method": "1.5×IQR（标记，不删除）",
                    "lower_fence": low,
                    "upper_fence": high,
                    "valid_n": n,
                    "flagged_n": flagged,
                    "status": "已标记" if flagged else "未发现超出围栏值",
                }
            )
        return pd.DataFrame(rows)

    def _analysis_complete(self, result) -> None:
        self.workflow_current_run_id = str(result.get("history_run_id") or "")
        prepared, analysis, config, source, prep_config = result["payload"]
        self.prepared = prepared
        self.analysis = analysis
        self.prep_config_used = dict(prep_config)
        self.analysis_config_used = dict(config)
        self.forecast_config_used = {}
        self.eda_config_used = {}
        self.forecast_result = None
        self.eda_result = None
        self.eda_meta = None
        meta = prepared.get("meta", {})
        fs = prepared.get("fs", meta.get("fs"))
        unit = prepared.get("time_unit", meta.get("time_unit", ""))
        warnings = list(prepared.get("warnings", [])) + list(analysis.get("warnings", []))
        self.last_warnings = warnings
        self._set_table("summary", analysis.get("tables", {}).get("summary"))
        self._set_table("band_power", analysis.get("tables", {}).get("band_power"))
        self._set_table("windows", analysis.get("tables", {}).get("windows"))
        self._set_table("tests", analysis.get("tables", {}).get("tests"))
        self._set_table("outliers", analysis.get("tables", {}).get("outliers"))
        values = meta.get("value_cols", self._value_columns())
        self.forecast_column_combo.configure(values=values)
        if self.forecast_col_var.get() not in values and values:
            self.forecast_col_var.set(values[0])
        self.forecast_column_combo.set(self.forecast_col_var.get())
        self.eda_column_combo.configure(values=values)
        if self.eda_column_var.get() not in values and values:
            self.eda_column_var.set(values[0])
        self.eda_column_combo.set(self.eda_column_var.get())
        self.marker_combo.configure(values=[str(column) for column in self.raw_frame.columns])
        self._populate_matrices(analysis.get("matrices", {}))
        self._draw_analysis_charts(prepared, analysis)
        self.analysis_summary.configure(
            text=(
                f"完成：{len(prepared['data']):,} 行，{len(values)} 个变量；"
                f"fs={self._format_value(fs)} Hz；时间单位={unit or '样本序号'}；"
                f"来源={source}。"
            )
        )
        self.eda_scope_label.configure(
            text=(
                f"当前记录采样率 {self._format_value(fs)} Hz；皮电要求至少 2 Hz、等间隔，"
                "且至少一段连续有效数据达到 20 秒。每个文件是一名被试/条件的一段记录；"
                "原始文件不会被修改。"
            )
        )
        status = "分析完成。"
        if warnings:
            status += f" 有 {len(warnings)} 条提示，请查看结果说明。"
        if result.get("history_error"):
            status += f" 历史快照保存失败：{result['history_error']}。"
        elif self.workflow_current_run_id and not self.capture_mode:
            self.workflow_refresh_history()
        self._set_status(status, warning=bool(warnings or result.get("history_error")))
        self._set_table("forecast_metrics", None)
        self._set_table("forecast_backtest", None)
        self._set_table("forecast_future", None)
        self._set_table("forecast_diagnostics", None)
        self._set_table("forecast_rolling_metrics", None)
        self._set_table("forecast_rolling_predictions", None)
        if self.capture_mode:
            next_capture_task = self.run_eda if self.capture_eda_only else self.run_forecast
            self.root.after(250, next_capture_task)

    def _populate_matrices(self, matrices: dict) -> None:
        if not matrices:
            self._matrix_options = {}
            self.matrix_combo.configure(values=[])
            self.matrix_combo.set("")
            self._set_table("matrix", None)
            return
        self._matrix_options = {
            key: MATRIX_LABELS.get(key.lower(), key.replace("_", " ").title())
            for key in matrices
        }
        display_values = list(self._matrix_options.values())
        self.matrix_combo.configure(values=display_values)
        first = next(iter(matrices))
        self.eda_matrix_var.set(self._matrix_options[first])
        self._render_matrix(first)

    def _on_matrix_selected(self, _event=None) -> None:
        selected_label = self.eda_matrix_var.get()
        key = next((k for k, value in self._matrix_options.items() if value == selected_label), None)
        if key is not None:
            self._render_matrix(key)

    def _render_matrix(self, key: str) -> None:
        if not self.analysis:
            return
        matrix = self.analysis.get("matrices", {}).get(key)
        if matrix is None:
            return
        self._set_table("matrix", matrix, max_rows=150)
        self.matrix_note.configure(
            text=(
                f"{self._matrix_options.get(key, key)} · 行=来源/变量，列=目标/变量。"
                "显示 NA 表示该位置不可定义或样本不足；关系矩阵不作因果解释。"
            )
        )

    def choose_event_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择独立事件表",
            filetypes=[
                ("支持的文件", "*.csv *.tsv *.txt *.xlsx"),
                ("CSV / TSV / TXT", "*.csv *.tsv *.txt"),
                ("Excel", "*.xlsx"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        event_path = Path(path)

        def load():
            import core

            frame = core.load_table(event_path)
            return frame

        def loaded(frame):
            self.event_path = event_path
            self.event_path_var.set(str(event_path))
            self.event_frame = frame
            columns = [str(column) for column in frame.columns]
            numeric = [
                column
                for column in columns
                if getattr(frame[column].dtype, "kind", "") in "iuf"
            ]
            label_candidates = [column for column in columns if column not in numeric]
            self.event_time_combo.configure(values=numeric or columns)
            self.event_label_combo.configure(values=columns)
            self.event_time_col_var.set(
                next((column for column in numeric if column.lower() in {"time", "seconds", "time_s"}), numeric[0] if numeric else "")
            )
            self.event_label_col_var.set(
                next((column for column in label_candidates if column.lower() in {"label", "event", "condition"}), label_candidates[0] if label_candidates else "")
            )
            self._set_status(f"事件表已载入 {len(frame):,} 行；时间列应为相对记录起点的秒。")

        self._start_worker("读取事件表", load, loaded)

    def _on_event_source_changed(self, _event=None) -> None:
        source = self.event_source_var.get()
        event_file = source == "事件文件"
        marker = source == "原始标签列"
        self.event_browse_btn.idle_state = tk.NORMAL if event_file else tk.DISABLED
        self.event_browse_btn.configure(
            state=tk.DISABLED if self.busy else self.event_browse_btn.idle_state
        )
        self.event_time_combo.configure(state="readonly" if event_file else tk.DISABLED)
        self.event_label_combo.configure(state="readonly" if event_file else tk.DISABLED)
        self.marker_combo.configure(state="readonly" if marker else tk.DISABLED)

    def _on_eda_unit_changed(self, _event=None) -> None:
        state = tk.DISABLED if self.eda_unit_var.get() == "a.u." else tk.NORMAL
        self.eda_absolute_entry.configure(state=state)

    def _read_event_data(
        self,
        source: str,
        event_frame,
        event_time_column: str,
        event_label_column: str,
        marker_column: str,
        raw_frame,
        selected_time: str,
        time_kind_label: str,
        fs: float,
        time_scale: str = "s",
        timezone: str = "UTC",
    ):
        if source == "无事件":
            return None
        import experimental

        if source == "事件文件":
            if event_frame is None:
                raise ValueError("请选择事件文件。")
            time_col = event_time_column
            label_col = event_label_column
            if not time_col or not label_col or time_col == label_col:
                raise ValueError("请为事件表选择不同的时间列和标签列。")
            pd = _pandas()
            events = event_frame[[time_col, label_col]].copy()
            events.columns = ["time", "label"]
            events["time"] = pd.to_numeric(events["time"], errors="coerce")
            if events["time"].isna().any():
                raise ValueError("事件时间列必须是相对记录起点的秒数，且不能有空值。")
            return events
        marker = marker_column
        if raw_frame is None or not marker:
            raise ValueError("请选择原始记录中的连续标签列。")
        time_col = None if selected_time == "（无时间列）" else selected_time
        time_kind = {
            "自动识别": "auto",
            "数值秒": "numeric",
            "数值时间": "numeric",
            "日期时间": "datetime",
        }.get(time_kind_label, "auto")
        return experimental.markers_to_events(
            raw_frame,
            marker_col=marker,
            time_col=time_col,
            fs=fs,
            time_kind=time_kind,
            time_scale=time_scale,
            timezone=timezone,
        )

    def _collect_eda_config(self) -> dict:
        if self.prepared is None:
            raise ValueError("请先在“预处理与分析”页整理时间并运行分析。")
        column = self.eda_column_var.get()
        allowed = self.prepared.get("meta", {}).get("value_cols", [])
        if not column or column not in allowed:
            raise ValueError("请选择一个已纳入分析的数值皮电通道。")
        unit = self.eda_unit_var.get()
        if unit not in {"uS", "mS", "nS", "a.u."}:
            raise ValueError("请明确选择皮电单位：uS、mS、nS 或 a.u.。")
        end_seconds = self._parse_float(self.eda_end_var, "分析终点", allow_blank=True)
        config = {
            **self._additional_config("eda"),
            "unit": unit,
            "method": self.eda_method_var.get(),
            "clean": self.eda_clean_var.get(),
            "cutoff": self._parse_float(self.eda_cutoff_var, "高通截止频率"),
            "relative_threshold": self._parse_float(self.eda_relative_var, "相对峰阈值"),
            "absolute_threshold": (
                0.0
                if unit == "a.u."
                else self._parse_float(self.eda_absolute_var, "绝对峰幅值阈值（uS）")
            ),
            "window_seconds": self._parse_float(self.eda_window_var, "固定窗口长度"),
            "start_seconds": self._parse_float(self.eda_start_var, "分析起点"),
            "end_seconds": end_seconds,
            "baseline_start": self._parse_float(self.baseline_start_var, "事件基线起点"),
            "baseline_end": self._parse_float(self.baseline_end_var, "事件基线终点"),
            "response_start": self._parse_float(self.response_start_var, "事件响应起点"),
            "response_end": self._parse_float(self.response_end_var, "事件响应终点"),
        }
        return column, config

    def run_eda(self) -> None:
        try:
            column, config = self._collect_eda_config()
            event_source = self.event_source_var.get()
            if event_source == "事件文件" and self.event_frame is None:
                raise ValueError("请选择事件文件。")
            if event_source == "原始标签列" and not self.marker_col_var.get():
                raise ValueError("请选择原始记录中的连续标签列。")
        except Exception as exc:
            messagebox.showerror("皮电设置不完整", str(exc), parent=self.root)
            return
        prepared = self.prepared
        event_frame = self.event_frame
        event_time_column = self.event_time_col_var.get()
        event_label_column = self.event_label_col_var.get()
        marker_column = self.marker_col_var.get()
        raw_frame = self.raw_frame
        selected_time = self.time_col_var.get()
        time_kind_label = self.time_kind_var.get()
        fs = float(prepared.get("fs", self._parse_float(self.fs_var, "采样率 fs")))
        source_info = copy.deepcopy(self.workflow_source_info)
        event_path_snapshot = str(self.event_path) if self.event_path else ""
        parameter_snapshot = {
            "prepare": copy.deepcopy(self.prep_config_used or {}),
            "analysis": copy.deepcopy(self.analysis_config_used or {}),
            "eda": {"column": column, "config": copy.deepcopy(config)},
            "event_mapping": {
                "source": event_source,
                "path": event_path_snapshot,
                "time_col": event_time_column,
                "label_col": event_label_column,
                "marker_col": marker_column,
            },
            "annotations": copy.deepcopy(self.workflow_annotations),
        }
        analysis_snapshot = self.analysis
        forecast_snapshot = self.forecast_result

        def run(cancel_event, progress):
            import experimental

            progress({"stage": "事件映射", "message": "正在整理事件表或标签列。"})
            events = self._read_event_data(
                event_source,
                event_frame,
                event_time_column,
                event_label_column,
                marker_column,
                raw_frame,
                selected_time,
                time_kind_label,
                fs,
                parameter_snapshot["prepare"].get("time_scale", "s"),
                parameter_snapshot["prepare"].get("timezone", "UTC"),
            )
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "皮电分析", "message": "正在计算连续信号与事件统计。"})
            result = experimental.analyze_eda(prepared, column, config, event_df=events)
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "保存历史", "message": "正在保存本次不可变结果快照。"})
            history_run_id, history_error = self._workflow_record_history(
                "eda", prepared, analysis_snapshot, forecast_snapshot, result,
                parameter_snapshot, source_info,
            )
            return {
                "payload": (result, config, column),
                "history_run_id": history_run_id,
                "history_error": history_error,
            }

        self._workflow_start_task("皮电分析", run, self._eda_complete)

    def _eda_complete(self, result) -> None:
        self.workflow_current_run_id = str(result.get("history_run_id") or "")
        eda, config, column = result["payload"]
        self.eda_result = eda
        self.eda_meta = eda.get("meta", {})
        tables = eda.get("tables", {})
        for key in ("eda_windows", "eda_peaks", "eda_events", "eda_event_summary"):
            self._set_table(key, tables.get(key))
        warnings = list(eda.get("warnings", []))
        self.last_warnings = list(self.last_warnings) + warnings
        self._replace_text(self.eda_warnings, "\n".join(warnings) or "本次皮电分析没有额外提示。")
        self.eda_status.configure(
            text=(
                f"完成：{column}；输入单位 {config['unit']}；"
                f"分析方法={config['method']}；清理={config['clean']}。"
            )
        )
        self._draw_eda_chart(tables.get("eda_signals"))
        self._set_status(
            f"皮电分析完成：{column}，{len(tables.get('eda_peaks', []))} 个候选 SCR 峰。",
            warning=bool(warnings),
        )
        if result.get("history_error"):
            self._set_status(f"皮电已完成，但历史快照保存失败：{result['history_error']}", warning=True)
        elif self.workflow_current_run_id and not self.capture_mode:
            self.workflow_refresh_history()
        self.eda_config_used = dict(config)
        if self.capture_mode:
            self.root.after(250, self._begin_capture_pages)

    def run_forecast(self) -> None:
        if self.prepared is None:
            messagebox.showerror("缺少分析数据", "请先完成数据预处理与分析。", parent=self.root)
            return
        column = self.forecast_col_var.get()
        if column not in self.prepared.get("meta", {}).get("value_cols", []):
            messagebox.showerror("未选择预测变量", "请选择一个已分析的数值变量。", parent=self.root)
            return
        models = [name for name, variable in self.model_vars.items() if variable.get()]
        if not models:
            messagebox.showerror("未选择模型", "请至少选择一个预测模型。", parent=self.root)
            return
        try:
            extra_forecast = self._additional_config("forecast")
            horizon = self._parse_int(self.horizon_var, "预测长度")
            period = self._parse_int(self.period_var, "季节周期", minimum=2)
            order = self._parse_order(self.arima_order_var, 3, "ARIMA 阶数")
            seasonal_order = self._parse_order(self.seasonal_order_var, 4, "SARIMA 阶数")
        except Exception as exc:
            messagebox.showerror("预测设置有误", str(exc), parent=self.root)
            return
        forecast_config = {
            "horizon": horizon,
            **extra_forecast,
            "period": period,
            "order": order,
            "seasonal_order": seasonal_order,
            "models": models,
            "missing": {
                "拒绝缺失值": "reject",
                "因果前向填充": "ffill",
            }[self.forecast_missing_var.get()],
        }
        prepared = self.prepared
        analysis_snapshot = self.analysis
        source_info = copy.deepcopy(self.workflow_source_info)
        eda_snapshot = self.eda_result
        parameters = {
            "prepare": copy.deepcopy(self.prep_config_used or {}),
            "analysis": copy.deepcopy(self.analysis_config_used or {}),
            "forecast": copy.deepcopy(forecast_config),
            "forecast_column": column,
            "eda": copy.deepcopy(self.eda_config_used or {}),
            "annotations": copy.deepcopy(self.workflow_annotations),
        }

        def run(cancel_event, progress):
            import core

            progress({"stage": "时间留后预测", "message": f"正在拟合 {len(models)} 个模型。"})
            result = core.forecast(prepared, column, forecast_config)
            self._workflow_check_cancel(cancel_event)
            forecast_for_history = dict(result)
            forecast_for_history["column"] = column
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "保存历史", "message": "正在保存本次不可变结果快照。"})
            history_run_id, history_error = self._workflow_record_history(
                "forecast", prepared, analysis_snapshot, forecast_for_history,
                eda_snapshot, parameters, source_info,
            )
            return {
                "payload": (result, column, forecast_config),
                "history_run_id": history_run_id,
                "history_error": history_error,
            }

        self._workflow_start_task("时间留后预测", run, self._forecast_complete)

    def _forecast_complete(self, result) -> None:
        self.workflow_current_run_id = str(result.get("history_run_id") or "")
        forecast, column, config = result["payload"]
        self.forecast_result = forecast
        self.forecast_column = column
        self.forecast_config_used = config
        self._set_table("forecast_metrics", forecast.get("metrics"))
        self._set_table("forecast_backtest", forecast.get("backtest"))
        self._set_table("forecast_future", forecast.get("future"))
        self._set_table("forecast_diagnostics", forecast.get("diagnostics"))
        self._set_table("forecast_rolling_metrics", forecast.get("rolling_metrics"))
        self._set_table("forecast_rolling_predictions", forecast.get("rolling_predictions"))
        warnings = list(forecast.get("warnings", []))
        self.last_warnings = list(self.last_warnings) + warnings
        self._replace_text(self.forecast_warnings, "\n".join(warnings) or "本次预测没有额外提示。")
        self.forecast_status.configure(
            text=f"完成：{column}；模型={', '.join(config['models'])}；留后比例={config.get('test_fraction', .2):.0%}；区间={config.get('confidence', .95):.0%}；验证={config.get('validation', 'holdout')}。"
        )
        self._draw_forecast_chart(forecast)
        self._set_status(
            f"留后比较完成：{column}，使用 {len(config['models'])} 个模型。",
            warning=bool(warnings),
        )
        if result.get("history_error"):
            self._set_status(f"预测已完成，但历史快照保存失败：{result['history_error']}", warning=True)
        elif self.workflow_current_run_id and not self.capture_mode:
            self.workflow_refresh_history()
        if self.capture_mode and not self.capture_eda_only:
            self.root.after(250, self.run_eda)

    @staticmethod
    def _parse_order(variable: tk.StringVar, count: int, label: str) -> tuple[int, ...]:
        try:
            parts = tuple(int(value.strip()) for value in variable.get().split(","))
        except ValueError as exc:
            raise ValueError(f"{label}请按逗号分隔的整数填写。") from exc
        if len(parts) != count or any(value < 0 for value in parts):
            raise ValueError(f"{label}需要 {count} 个非负整数。")
        return parts

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root, title="选择结果文件夹", mustexist=False
        )
        if path:
            self.output_dir_var.set(path)

    def export_results(self) -> None:
        if self.prepared is None or self.analysis is None:
            messagebox.showerror("没有结果可导出", "请先完成探索性分析。", parent=self.root)
            return
        output_text = self.output_dir_var.get().strip()
        if not output_text:
            messagebox.showerror("缺少导出路径", "请选择结果保存文件夹。", parent=self.root)
            return
        base = Path(output_text).expanduser()
        from datetime import datetime

        folder = base / f"PYTSA_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        prepared = self.prepared
        analysis = self.analysis
        forecast = self.forecast_result
        eda_result = self.eda_result
        source = self.raw_source_label
        prep_config = getattr(self, "prep_config_used", {})
        analysis_config = getattr(self, "analysis_config_used", {})
        forecast_config = getattr(self, "forecast_config_used", {})
        forecast_column = self.forecast_column
        annotations = copy.deepcopy(self.workflow_annotations)
        try:
            report_template = self._workflow_collect_report_template()
        except Exception as exc:
            messagebox.showerror("报告设置有误", str(exc), parent=self.root)
            return

        def export(cancel_event, progress):
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "准备报告", "message": "正在整理已完成结果和图表。"})
            prepared_copy = dict(prepared)
            prepared_copy["meta"] = dict(prepared.get("meta", {}))
            prepared_copy["meta"]["source"] = source
            prepared_copy["meta"]["preprocess_parameters"] = prep_config
            prepared_copy["meta"]["analysis_parameters"] = analysis_config
            analysis_copy = dict(analysis)
            analysis_copy["tables"] = dict(analysis.get("tables", {}))
            analysis_copy["matrices"] = dict(analysis.get("matrices", {}))
            analysis_copy["meta"] = dict(analysis.get("meta", {}))
            if eda_result:
                for name, frame in eda_result.get("tables", {}).items():
                    analysis_copy["tables"][f"eda_{name}"] = frame
                analysis_copy["warnings"] = list(analysis_copy.get("warnings", [])) + list(
                    eda_result.get("warnings", [])
                )
                analysis_copy["meta"]["eda"] = dict(eda_result.get("meta", {}))
            if forecast:
                analysis_copy["meta"]["forecast_parameters"] = forecast_config
                analysis_copy["meta"]["forecast_column"] = forecast_column
            figures = self._build_export_figures(prepared_copy, analysis_copy, forecast, eda_result)
            self._workflow_check_cancel(cancel_event)
            report = self._workflow_get_report_module()
            result = report.export_report(
                folder,
                prepared_copy,
                analysis_copy,
                forecast_result=forecast,
                figures=figures,
                research_path=resource_path("research_notes.json"),
                template=report_template,
                annotations=annotations,
                progress=progress,
                cancel=cancel_event.is_set,
            )
            actual_folder = Path(result.get("folder", folder)) if isinstance(result, dict) else folder
            return {"result": result, "folder": str(actual_folder)}

        def exported(payload):
            data = payload.get("result", {})
            actual_folder = Path(payload.get("folder", folder))
            files = data.get("files", []) if isinstance(data, dict) else []
            self.export_status.configure(
                text=f"导出完成：{actual_folder}；文件数 {len(files)}。",
                fg=COLORS["ink"],
            )
            self._set_status(f"结果已保存到 {actual_folder}")
            messagebox.showinfo("导出完成", f"结果已保存到：\n{actual_folder}", parent=self.root)

        self._workflow_start_task("导出结果", export, exported)

    def _populate_method_notes(self) -> None:
        path = resource_path("research_notes.json")
        try:
            raw = path.read_text(encoding="utf-8-sig")
            notes = json.loads(raw)
        except Exception as exc:
            raw = ""
            notes = {}
            error = f"无法读取研究资料：{exc}"
        else:
            error = ""
        lines = [
            "PYTSA 方法依据与文献记录",
            f"研究文件：{path}",
            f"应用版本：{APP_VERSION}",
            f"运行解释器：{sys.version.split()[0]}",
            "",
        ]
        if error:
            lines.append(error)
        else:
            for key, title in (
                ("review_type", "研究范围"),
                ("scope", "覆盖领域"),
                ("decision", "实现原则"),
            ):
                if notes.get(key):
                    lines.extend([f"{title}：", str(notes[key]), ""])
            lines.extend(["论文与官方资料：", ""])
            for reference in notes.get("references", []):
                lines.append(
                    f"[{reference.get('id', '')}] {reference.get('title', '')} "
                    f"({reference.get('year', '')})"
                )
                authors = reference.get("authors", [])
                if authors:
                    lines.append("作者：" + ", ".join(str(name) for name in authors))
                if reference.get("url"):
                    lines.append("链接：" + str(reference["url"]))
                for key, label in (
                    ("read_scope", "阅读范围"),
                    ("status", "核对状态"),
                    ("supports", "支持内容"),
                    ("implementation", "本工具实现"),
                    ("assumptions", "适用条件"),
                ):
                    if reference.get(key):
                        lines.append(f"{label}：{reference[key]}")
                lines.append("")
            lines.extend(["研究限制："])
            for limitation in notes.get("limitations", []):
                lines.append(f"• {limitation}")
            lines.extend(
                [
                    "",
                    "完整 JSON 原文随 EXE 一并嵌入；同一文件也由离线 HTML 导出接口引用。",
                    "",
                    "本地数据处理，不调用远程模型或服务。预印本和摘要证据会明确标注；"
                    "工具实现不代表复现文献实验结果。",
                ]
            )
            if raw:
                lines.extend(["", "完整研究 JSON：", raw])
        self._replace_text(self.methods_text, "\n".join(lines))
        license_path = resource_path("assets/THIRD_PARTY_LICENSES.txt")
        if license_path.is_file():
            self._replace_text(self.license_text, license_path.read_text(encoding="utf-8-sig"))
        else:
            self._replace_text(
                self.license_text,
                "构建清单尚未生成 assets/THIRD_PARTY_LICENSES.txt；最终 EXE 构建会附带当前隔离环境的软件版本与许可证摘要。",
            )

    @staticmethod
    def _replace_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _clear_chart_widgets(self) -> None:
        for key, frame in getattr(self, "chart_frames", {}).items():
            for child in frame.winfo_children():
                child.destroy()
            self._chart_placeholder(frame, "载入新数据后运行分析以显示图表。")
        for host_name, message in (
            ("forecast_chart_host", "完成预测后显示留后与未来预测曲线。"),
            ("eda_chart_host", "完成皮电分析后显示原始、清理、tonic 与 phasic 曲线。"),
        ):
            host = getattr(self, host_name, None)
            if host is not None:
                for child in host.winfo_children():
                    child.destroy()
                self._chart_placeholder(host, message)
        self.chart_widgets.clear()
        self._workflow_chart_selectors.clear()
        self._workflow_chart_readouts.clear()
        self._workflow_chart_time_meta.clear()

    def _render_figure(self, parent: tk.Misc, figure, key: str, axes=None) -> None:
        for child in parent.winfo_children():
            child.destroy()
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

        canvas = FigureCanvasTkAgg(figure, master=parent)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, parent, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="top", fill="x")
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        readout = tk.StringVar(value="移动指针查看坐标。")
        self._workflow_chart_readouts[key] = readout
        tk.Label(
            parent,
            textvariable=readout,
            bg=COLORS["white"],
            fg=COLORS["muted"],
            anchor="w",
            font=(self.font_family, 8),
        ).pack(side="bottom", fill="x", padx=4, pady=(0, 2))
        self.chart_widgets[key] = (canvas, toolbar)
        self._workflow_attach_chart_interactions(
            key, canvas, figure.axes if axes is None else axes
        )

    def _draw_analysis_charts(self, prepared, analysis) -> None:
        try:
            from matplotlib.figure import Figure
        except Exception as exc:
            self._set_status(f"无法载入图形组件：{exc}", warning=True)
            return
        data = prepared["data"]
        meta = prepared.get("meta", {})
        value_cols = list(meta.get("value_cols", []))
        time_col = meta.get("time_col")
        if time_col and time_col in data.columns:
            x = data[time_col]
            x_label = str(meta.get("time_unit", "时间"))
            original_axis = prepared.get("original", data)[time_col]
            origin = original_axis.iloc[0]
            axis_kind = "datetime" if meta.get("time_kind") == "datetime" else "numeric"
        else:
            np = _numpy()
            fs = float(prepared.get("fs", 1.0))
            x = np.arange(len(data), dtype=float) / fs
            x_label = "记录起点后的秒"
            origin = 0.0
            axis_kind = "relative_seconds"
        self._workflow_chart_time_meta["series"] = {"kind": axis_kind, "origin": origin}

        series_fig = Figure(figsize=(8.5, 3.2), dpi=100, facecolor=COLORS["white"])
        ax = series_fig.add_subplot(111)
        for column in value_cols[:8]:
            ax.plot(x, data[column], label=column, linewidth=1.0)
        ax.set_title("处理后时间序列（仅图形显示前 8 个变量）")
        ax.set_xlabel(x_label)
        ax.set_ylabel("原单位 / 变换后单位")
        if value_cols:
            ax.legend(loc="best", fontsize=8, ncol=min(4, len(value_cols)))
        ax.grid(alpha=0.2)
        series_fig.tight_layout()
        self._render_figure(self.chart_frames["series"], series_fig, "series")

        acf = analysis.get("tables", {}).get("acf")
        if acf is not None and not acf.empty and {"variable", "lag", "acf", "pacf"}.issubset(acf.columns):
            acf_fig = Figure(figsize=(8.5, 3.6), dpi=100, facecolor=COLORS["white"])
            axes = acf_fig.subplots(1, 2, sharex=True)
            for variable, group in acf.groupby("variable", sort=False):
                if sum(1 for _ in axes[0].lines) >= 6:
                    break
                axes[0].plot(group["lag"], group["acf"], marker="o", markersize=2, label=str(variable))
                axes[1].plot(group["lag"], group["pacf"], marker="o", markersize=2, label=str(variable))
            axes[0].set_title("自相关 ACF")
            axes[1].set_title("偏自相关 PACF")
            for ax in axes:
                ax.set_xlabel("滞后（样本点）")
                ax.grid(alpha=0.2)
                ax.legend(fontsize=7)
            acf_fig.tight_layout()
            self._render_figure(self.chart_frames["acf"], acf_fig, "acf")
        else:
            self._set_chart_message(self.chart_frames["acf"], "当前样本或参数下没有可绘制的 ACF / PACF。")

        psd = analysis.get("tables", {}).get("psd")
        if psd is not None and not psd.empty and {"variable", "frequency", "psd"}.issubset(psd.columns):
            psd_fig = Figure(figsize=(8.5, 3.2), dpi=100, facecolor=COLORS["white"])
            ax = psd_fig.add_subplot(111)
            for variable, group in psd.groupby("variable", sort=False):
                if sum(1 for _ in ax.lines) >= 8:
                    break
                positive = group["psd"] > 0
                ax.semilogy(
                    group.loc[positive, "frequency"],
                    group.loc[positive, "psd"],
                    label=str(variable),
                    linewidth=1.0,
                )
            ax.set_title("Welch 功率谱密度")
            ax.set_xlabel("频率（Hz）")
            ax.set_ylabel("PSD")
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
            psd_fig.tight_layout()
            self._render_figure(self.chart_frames["psd"], psd_fig, "psd")
        else:
            self._set_chart_message(self.chart_frames["psd"], "当前样本或采样规则下没有可绘制的 PSD。")

        decomposition = analysis.get("tables", {}).get("decomposition")
        decomp_columns = {"variable", "time", "observed", "trend", "seasonal", "residual"}
        if decomposition is not None and not decomposition.empty and decomp_columns.issubset(decomposition.columns):
            variable = decomposition["variable"].iloc[0]
            group = decomposition[decomposition["variable"] == variable]
            decomp_fig = Figure(figsize=(8.5, 5.2), dpi=100, facecolor=COLORS["white"])
            axes = decomp_fig.subplots(4, 1, sharex=True)
            for ax, key, title in zip(
                axes, ("observed", "trend", "seasonal", "residual"),
                ("观察值", "趋势", "季节项", "残差")
            ):
                ax.plot(group["time"], group[key], linewidth=0.9)
                ax.set_ylabel(title)
                ax.grid(alpha=0.15)
            axes[0].set_title(f"STL 分解：{variable}")
            axes[-1].set_xlabel("时间")
            decomp_fig.tight_layout()
            self._render_figure(self.chart_frames["decomposition"], decomp_fig, "decomposition")
        else:
            self._set_chart_message(self.chart_frames["decomposition"], "当前样本未生成 STL 分解结果。")

    def _set_chart_message(self, parent: tk.Misc, message: str) -> None:
        for child in parent.winfo_children():
            child.destroy()
        tk.Label(
            parent,
            text=message,
            bg=COLORS["white"],
            fg=COLORS["muted"],
            anchor="center",
            font=(self.font_family, 10),
            pady=24,
        ).pack(fill="both", expand=True)

    def _draw_forecast_chart(self, forecast: dict) -> None:
        from matplotlib.figure import Figure

        backtest = forecast.get("backtest")
        future = forecast.get("future")
        if backtest is None or future is None or backtest.empty or future.empty:
            self._set_chart_message(self.forecast_chart_host, "没有足够数据绘制留后与未来预测。")
            return
        fig = Figure(figsize=(8.5, 3.6), dpi=100, facecolor=COLORS["white"])
        axes = fig.subplots(1, 2)
        models = list(dict.fromkeys(backtest["model"].astype(str).tolist()))
        actual = backtest.drop_duplicates("time").sort_values("time")
        axes[0].plot(actual["time"], actual["actual"], color=COLORS["ink"], label="实际值", linewidth=1.2)
        for model in models[:5]:
            group = backtest[backtest["model"].astype(str) == model]
            axes[0].plot(group["time"], group["predicted"], label=model, linewidth=1.0)
        axes[0].set_title(f"时间留后区间（{forecast.get('meta', {}).get('test_fraction_actual', .2):.0%}）")
        axes[0].legend(fontsize=7)
        models = list(dict.fromkeys(future["model"].astype(str).tolist()))
        for model in models[:5]:
            group = future[future["model"].astype(str) == model]
            axes[1].plot(group["time"], group["predicted"], label=model, linewidth=1.1)
            if {"lower", "upper"}.issubset(group.columns):
                try:
                    axes[1].fill_between(group["time"], group["lower"], group["upper"], alpha=0.12)
                except Exception:
                    pass
        axes[1].set_title("未来预测（区间仅适用于返回模型区间的模型）")
        axes[1].legend(fontsize=7)
        for ax in axes:
            ax.grid(alpha=0.2)
            ax.set_xlabel("时间 / 预测步")
        fig.tight_layout()
        self._render_figure(self.forecast_chart_host, fig, "forecast")

    def _draw_eda_chart(self, signals) -> None:
        if signals is None or signals.empty:
            self._set_chart_message(self.eda_chart_host, "没有可绘制的皮电信号。")
            return
        from matplotlib.figure import Figure

        data = signals
        stride = max(1, len(data) // 7000)
        display = data.iloc[::stride]
        times = display["time"]
        self._workflow_chart_time_meta["eda"] = {"kind": "relative_seconds", "origin": 0.0}
        fig = Figure(figsize=(8.5, 4.2), dpi=100, facecolor=COLORS["white"])
        axes = fig.subplots(2, 2, sharex=True)
        for ax, name, title in (
            (axes[0, 0], "raw", "原始皮电信号"),
            (axes[0, 1], "clean", "清理后信号"),
            (axes[1, 0], "tonic", "Tonic 慢变分量"),
            (axes[1, 1], "phasic", "Phasic 快变分量"),
        ):
            if name in display:
                ax.plot(times, display[name], linewidth=0.8)
            ax.set_title(title)
            ax.grid(alpha=0.2)
        for ax in axes[1, :]:
            ax.set_xlabel("相对记录起点时间（秒）")
        axes[0, 0].set_ylabel("信号单位")
        axes[1, 0].set_ylabel("信号单位")
        fig.tight_layout()
        self._render_figure(self.eda_chart_host, fig, "eda")

    def _build_export_figures(self, prepared, analysis, forecast, eda_result) -> dict:
        from matplotlib.figure import Figure

        np = _numpy()
        data = prepared["data"]
        meta = prepared.get("meta", {})
        value_cols = list(meta.get("value_cols", []))
        time_col = meta.get("time_col")
        if time_col and time_col in data.columns:
            x = data[time_col]
        else:
            x = np.arange(len(data), dtype=float) / float(prepared.get("fs", 1.0))
        stride = max(1, len(data) // 12000)
        display = data.iloc[::stride]
        display_x = x.iloc[::stride] if hasattr(x, "iloc") else x[::stride]
        figures = {}

        fig = Figure(figsize=(10, 4.5), dpi=140, facecolor="white")
        ax = fig.add_subplot(111)
        for column in value_cols[:12]:
            ax.plot(display_x, display[column], label=column, linewidth=0.9)
        ax.set_title("时间序列")
        ax.set_xlabel(str(meta.get("time_unit", "相对时间")))
        ax.grid(alpha=0.2)
        if value_cols:
            ax.legend(fontsize=7, ncol=min(4, len(value_cols)))
        fig.tight_layout()
        figures["time_series"] = fig

        for key, matrix in analysis.get("matrices", {}).items():
            if matrix is None or getattr(matrix, "empty", True):
                continue
            try:
                values = np.asarray(matrix, dtype=float)
            except Exception:
                continue
            size = max(5, min(10, 4 + len(values) * 0.12))
            matrix_fig = Figure(figsize=(size, size * 0.88), dpi=140, facecolor="white")
            matrix_ax = matrix_fig.add_subplot(111)
            im = matrix_ax.imshow(values, aspect="auto", interpolation="nearest", cmap="coolwarm")
            labels = [str(name) for name in getattr(matrix, "columns", range(len(values)))]
            matrix_ax.set_xticks(range(len(labels)), labels=labels, rotation=60, ha="right", fontsize=7)
            matrix_ax.set_yticks(range(len(labels)), labels=labels, fontsize=7)
            matrix_ax.set_title(MATRIX_LABELS.get(key.lower(), key))
            matrix_fig.colorbar(im, ax=matrix_ax, shrink=0.8)
            matrix_fig.tight_layout()
            figures[f"matrix_{key}"] = matrix_fig

        psd = analysis.get("tables", {}).get("psd")
        if psd is not None and not getattr(psd, "empty", True):
            psd_fig = Figure(figsize=(9, 4.5), dpi=140, facecolor="white")
            psd_ax = psd_fig.add_subplot(111)
            for variable, group in psd.groupby("variable", sort=False):
                group = group[group["psd"] > 0]
                if not group.empty:
                    psd_ax.semilogy(group["frequency"], group["psd"], label=str(variable), linewidth=0.9)
            psd_ax.set_title("Welch 功率谱密度")
            psd_ax.set_xlabel("频率（Hz）")
            psd_ax.grid(alpha=0.2)
            psd_ax.legend(fontsize=7)
            psd_fig.tight_layout()
            figures["welch_psd"] = psd_fig

        if forecast:
            backtest = forecast.get("backtest")
            future = forecast.get("future")
            if backtest is not None and future is not None and not backtest.empty and not future.empty:
                ffig = Figure(figsize=(10, 4.5), dpi=140, facecolor="white")
                axes = ffig.subplots(1, 2)
                actual = backtest.drop_duplicates("time").sort_values("time")
                axes[0].plot(actual["time"], actual["actual"], color="#27324A", label="实际值")
                for model in list(dict.fromkeys(backtest["model"].astype(str).tolist()))[:5]:
                    group = backtest[backtest["model"].astype(str) == model]
                    axes[0].plot(group["time"], group["predicted"], label=model)
                axes[0].set_title("留后预测")
                axes[0].legend(fontsize=7)
                for model in list(dict.fromkeys(future["model"].astype(str).tolist()))[:5]:
                    group = future[future["model"].astype(str) == model]
                    axes[1].plot(group["time"], group["predicted"], label=model)
                    if {"lower", "upper"}.issubset(group.columns):
                        try:
                            axes[1].fill_between(group["time"], group["lower"], group["upper"], alpha=0.12)
                        except Exception:
                            pass
                axes[1].set_title("未来预测")
                axes[1].legend(fontsize=7)
                ffig.tight_layout()
                figures["forecast"] = ffig

        if eda_result:
            eda_signals = eda_result.get("tables", {}).get("eda_signals")
            if eda_signals is not None and not eda_signals.empty:
                eda_fig = Figure(figsize=(10, 5.5), dpi=140, facecolor="white")
                axes = eda_fig.subplots(2, 2, sharex=True)
                stride = max(1, len(eda_signals) // 12000)
                eda_display = eda_signals.iloc[::stride]
                for ax, key, title in (
                    (axes[0, 0], "raw", "原始信号"),
                    (axes[0, 1], "clean", "清理后"),
                    (axes[1, 0], "tonic", "Tonic"),
                    (axes[1, 1], "phasic", "Phasic"),
                ):
                    if key in eda_display:
                        ax.plot(eda_display["time"], eda_display[key], linewidth=0.8)
                    ax.set_title(title)
                    ax.grid(alpha=0.2)
                eda_fig.tight_layout()
                figures["eda_components"] = eda_fig
        return figures

    def _begin_capture_pages(self) -> None:
        if self.capture_dir is not None:
            self.root.after(300, self._capture_next_page, 0)

    def _capture_next_page(self, index: int) -> None:
        if self.capture_dir is None:
            return
        if index >= len(self.capture_steps):
            self.root.destroy()
            return
        page_id, scroll_fraction, capture_name = self.capture_steps[index]
        self.show_page(page_id)
        self.root.update_idletasks()
        self.pages[page_id].canvas.yview_moveto(scroll_fraction)
        self.root.update_idletasks()
        self.root.update()
        try:
            from PIL import ImageGrab

            self.capture_dir.mkdir(parents=True, exist_ok=True)
            x = self.root.winfo_rootx()
            y = self.root.winfo_rooty()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            image = ImageGrab.grab(window=int(self.root.winfo_id()))
            path = self.capture_dir / f"{capture_name}.png"
            image.save(path)
            print(str(path))
        except Exception as exc:
            print(f"UI capture failed for {page_id}: {exc}", file=sys.stderr)
        self.root.after(250, self._capture_next_page, index + 1)


    def _build_forecast_controls(self, parent: tk.Misc) -> None:
        controls = self._card(
            parent,
            "预测设置",
            "按时间留出末段作为比较区间，可附加固定训练窗口的滚动验证。所有模型使用相同验证窗口。",
            COLORS["white"],
        )
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)
        self.forecast_column_combo = self._combo(
            controls, self.forecast_col_var, ["请先运行分析"], width=24
        )
        self.horizon_entry = self._entry(controls, self.horizon_var)
        self.forecast_missing_combo = self._combo(
            controls, self.forecast_missing_var, ["拒绝缺失值", "因果前向填充"]
        )
        self.arima_entry = self._entry(controls, self.arima_order_var)
        self.seasonal_entry = self._entry(controls, self.seasonal_order_var)
        self._field(controls, "预测变量（一次选一个）", self.forecast_column_combo, 0, 0)
        self._field(controls, "未来预测长度（样本点）", self.horizon_entry, 0, 1)
        self._field(controls, "预测分支缺失值", self.forecast_missing_combo, 1, 0)
        self._field(controls, "ARIMA 阶数 p,d,q", self.arima_entry, 1, 1)
        self._field(controls, "SARIMA 阶数 P,D,Q,period", self.seasonal_entry, 2, 0)
        tk.Label(
            controls,
            text="预测使用原始尺度序列。置信水平用于 ARIMA/SARIMA 的模型预测区间；其它方法未提供区间时显示 NA。滚动验证的指标与预测保留每折记录。",
            bg=COLORS["white"],
            fg=COLORS["muted"],
            anchor="w",
            justify="left",
            wraplength=970,
            font=(self.font_family, 9),
        ).grid(row=6, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 3))

        self._additional_card(parent, "forecast", "留后比例、预测区间与滚动验证",
                              "holdout=仅留后，rolling=附加滚动验证。训练窗口、验证长度、步长留空时分别为半段记录、未来预测长度、验证长度。最多 500 次滚动拟合。",
                              COLORS["sky"])
        model_card = self._card(
            parent,
            "参与比较的模型",
            "选择 Naive、SeasonalNaive、ETS、ARIMA、SARIMA 中的一个或多个。模型拟合失败或不收敛会在表格中标明。",
            COLORS["lemon"],
        )
        model_grid = tk.Frame(model_card, bg=COLORS["lemon"])
        model_grid.pack(fill="x", padx=4, pady=3)
        labels = {
            "Naive": "Naive（最近值）",
            "SeasonalNaive": "SeasonalNaive（季节重复）",
            "ETS": "ETS（指数平滑）",
            "ARIMA": "ARIMA",
            "SARIMA": "SARIMA",
        }
        for index, model in enumerate(self.model_vars):
            checkbox = ttk.Checkbutton(
                model_grid,
                text=labels[model],
                variable=self.model_vars[model],
            )
            checkbox.grid(row=index // 3, column=index % 3, sticky="w", padx=8, pady=5)
            self._workflow_register_editable(checkbox)
        forecast_action = self._card(
            parent,
            "运行预测",
            "误差指标：MAE、RMSE、sMAPE、MASE。MASE 的缩放分母只从训练段计算；分母为零时显示未定义。",
            COLORS["coral"],
        )
        forecast_row = tk.Frame(forecast_action, bg=COLORS["coral"])
        forecast_row.pack(fill="x", padx=4, pady=3)
        forecast_btn = self._action_button(
            forecast_row, "运行留后比较与未来预测", self.run_forecast, "green"
        )
        forecast_btn.shadow_wrapper.pack(side="left", padx=(0, 10))
        self.forecast_status = self._label(
            forecast_action, "等待分析结果。", color=COLORS["muted"], wraplength=950
        )
        self.forecast_status.pack(fill="x", expand=True, padx=6, pady=5)

        outputs = self._card(
            parent,
            "预测结果",
            "留后表现用于本次探索性比较；模型区间与残差诊断需结合模型假设阅读。",
            COLORS["sky"],
        )
        self.forecast_chart_host = tk.Frame(outputs, bg=COLORS["white"], height=400)
        self.forecast_chart_host.pack_propagate(False)
        self.forecast_chart_host.pack(fill="x", expand=False, padx=5, pady=4)
        self._chart_placeholder(self.forecast_chart_host, "完成预测后显示留后与未来预测曲线。")
        tabs = ttk.Notebook(outputs)
        tabs.pack(fill="both", expand=True, padx=4, pady=4)
        for key, title in (
            ("forecast_metrics", "留后误差"),
            ("forecast_backtest", "留后预测"),
            ("forecast_future", "未来预测"),
            ("forecast_diagnostics", "拟合与残差诊断"),
            ("forecast_rolling_metrics", "滚动误差"),
            ("forecast_rolling_predictions", "滚动预测"),
        ):
            tab = tk.Frame(tabs, bg=COLORS["white"])
            tabs.add(tab, text=title)
            self._make_table(tab, key, height=8)
        self.forecast_warnings = tk.Text(
            outputs,
            height=4,
            wrap="word",
            bg=COLORS["lemon"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            font=(self.font_family, 9),
        )
        self.forecast_warnings.pack(fill="x", padx=5, pady=(4, 8))

    def _build_export_controls(self, parent: tk.Misc) -> None:
        export = self._card(
            parent,
            "选择导出位置",
            "导出 CSV / XLSX 表格、PNG 图和离线 HTML 报告；报告保留数据来源、参数、方法说明、预警与原始研究资料。",
            COLORS["white"],
        )
        export.grid_columnconfigure(1, weight=1)
        tk.Label(
            export,
            text="结果文件夹",
            bg=COLORS["white"],
            fg=COLORS["ink"],
            font=(self.font_family, 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=6, pady=5)
        self.output_entry = self._entry(export, self.output_dir_var)
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        choose = self._action_button(export, "浏览…", self.choose_output_dir, "lavender")
        choose.shadow_wrapper.grid(row=0, column=2, sticky="e", padx=6, pady=4)
        run = self._action_button(export, "导出全部结果", self.export_results, "green")
        run.shadow_wrapper.grid(row=1, column=0, sticky="w", padx=6, pady=(12, 8))
        self.export_status = self._label(
            export,
            "导出前需先完成探索分析；预测和皮电结果为可选内容。",
            color=COLORS["muted"],
            wraplength=780,
        )
        self.export_status.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(12, 8))

        help_card = self._card(
            parent,
            "结果含义与复现信息",
            "计算使用本地文件；导出前可选择输出文件夹。程序不会把数据上传到远程服务。",
            COLORS["mint"],
        )
        self.export_help = tk.Text(
            help_card,
            height=9,
            wrap="word",
            bg=COLORS["mint"],
            fg=COLORS["ink"],
            relief="flat",
            font=(self.font_family, 9),
        )
        self.export_help.pack(fill="both", expand=True, padx=5, pady=4)
        self.export_help.insert(
            "1.0",
            "结果表保留方法名、参数、采样信息与警告。预测只做一次时间留后比较，不自动搜索参数；需要在新数据上再次评估。相关、偏相关、滞后相关与相干描述变量关系，不构成因果结论。常数序列、空频带、零分母与样本不足会显示为未定义或跳过。",
        )
        self.export_help.configure(state="disabled")
        self._build_report_template_controls(parent)

    def _build_methods_page(self, parent: tk.Misc) -> None:
        methods = self._card(
            parent,
            "方法依据与文献",
            "研究文件随应用一起打包；以下信息保留论文/官方文档范围、实现决策与限制。",
            COLORS["white"],
        )
        self.methods_text = tk.Text(
            methods,
            height=24,
            wrap="word",
            bg=COLORS["white"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=(self.font_family, 9),
        )
        self.methods_text.pack(fill="both", expand=True, padx=5, pady=4)
        license_card = self._card(
            parent,
            "软件组件与许可证",
            "本地科学计算组件与它们的许可证摘要随 EXE 一起提供。",
            COLORS["lavender"],
        )
        self.license_text = tk.Text(
            license_card,
            height=8,
            wrap="word",
            bg=COLORS["lavender"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            padx=10,
            pady=8,
            font=(self.font_family, 9),
        )
        self.license_text.pack(fill="both", expand=True, padx=5, pady=4)
        self.license_text.insert(
            "1.0",
            "组件许可证摘要将在构建时由当前隔离环境的包元数据生成，并以 assets/THIRD_PARTY_LICENSES.txt 随 EXE 分发。",
        )
        self.license_text.configure(state="disabled")

    def _build_eda_controls(self, parent: tk.Misc) -> None:
        scope = self._card(
            parent,
            "记录范围与安全边界",
            "每个文件按单个被试/条件的一段连续记录处理；请先在文件中筛选目标记录。不会跨被试自动拼接，不连接设备。皮电结果不能直接解释为压力、情绪或疾病。",
            COLORS["white"],
        )
        demo = self._action_button(
            scope, "载入皮电合成演示（单位已知：uS）", self.load_eda_demo, "sky"
        )
        demo.shadow_wrapper.pack(side="left", padx=5, pady=4)
        self.eda_scope_label = self._label(
            scope,
            "请先在“数据输入”页载入单段连续记录，并在“预处理与分析”页完成时间整理；皮电采样率必须至少 2 Hz，等间隔，至少有一段连续有效数据达到 20 秒。",
            color=COLORS["muted"],
            wraplength=620,
        )
        self.eda_scope_label.pack(fill="x", padx=6, pady=5)

        signal = self._card(
            parent,
            "皮电通道与单位",
            "单位必须由使用者明确选择；程序不会假设输入一定是微西门子。电压或电阻列不能直接作为皮电导。",
            COLORS["sky"],
        )
        signal.grid_columnconfigure(0, weight=1)
        signal.grid_columnconfigure(1, weight=1)
        self.eda_column_combo = self._combo(signal, self.eda_column_var, [], width=24)
        self.eda_unit_combo = self._combo(
            signal, self.eda_unit_var, ["uS", "mS", "nS", "a.u."], width=12
        )
        self.eda_unit_combo.bind("<<ComboboxSelected>>", self._on_eda_unit_changed)
        self._field(signal, "皮电通道", self.eda_column_combo, 0, 0)
        self._field(signal, "输入单位（必选）", self.eda_unit_combo, 0, 1)

        settings = self._card(
            parent,
            "清理、分解与幅值阈值",
            "NeuroKit 清理与高通分解为默认推荐；可切换中值分解或不清理。幅值阈值只筛选峰，不改动原始幅度。",
            COLORS["mint"],
        )
        settings.grid_columnconfigure(0, weight=1)
        settings.grid_columnconfigure(1, weight=1)
        self.eda_clean_combo = self._combo(
            settings, self.eda_clean_var, ["neurokit", "none"], width=14
        )
        self.eda_method_combo = self._combo(
            settings, self.eda_method_var, ["highpass", "median"], width=14
        )
        self.eda_cutoff_entry = self._entry(settings, self.eda_cutoff_var)
        self.eda_relative_entry = self._entry(settings, self.eda_relative_var)
        self.eda_absolute_entry = self._entry(settings, self.eda_absolute_var)
        self._field(settings, "清理方法", self.eda_clean_combo, 0, 0)
        self._field(settings, "tonic / phasic 分解", self.eda_method_combo, 0, 1)
        self._field(settings, "高通截止频率（Hz）", self.eda_cutoff_entry, 1, 0)
        self._field(settings, "相对峰显著度阈值（0–1）", self.eda_relative_entry, 1, 1)
        self._field(settings, "绝对峰幅值阈值（uS；a.u. 不适用）", self.eda_absolute_entry, 2, 0)
        self._label(
            settings,
            "短于 20 秒的连续段标为无效；不会跨缺失段滤波。uS/mS/nS 统一换算到 uS 后检测，a.u. 保持任意单位且禁用绝对 uS 阈值。",
            color=COLORS["muted"],
            wraplength=940,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=5)

        windows = self._card(
            parent,
            "固定窗口与事件窗",
            "时间均以记录首样本为 0 秒。事件文件的时间列需表示相对记录起点的秒；重叠、越界或缺段事件保留状态但不计入有效标签汇总。",
            COLORS["lemon"],
        )
        windows.grid_columnconfigure(0, weight=1)
        windows.grid_columnconfigure(1, weight=1)
        self.eda_window_entry = self._entry(windows, self.eda_window_var)
        self.eda_start_entry = self._entry(windows, self.eda_start_var)
        self.eda_end_entry = self._entry(windows, self.eda_end_var)
        self.baseline_start_entry = self._entry(windows, self.baseline_start_var)
        self.baseline_end_entry = self._entry(windows, self.baseline_end_var)
        self.response_start_entry = self._entry(windows, self.response_start_var)
        self.response_end_entry = self._entry(windows, self.response_end_var)
        self._field(windows, "固定窗口长度（秒）", self.eda_window_entry, 0, 0)
        self._field(windows, "分析起点（秒）", self.eda_start_entry, 0, 1)
        self._field(windows, "分析终点（秒；留空至记录末尾）", self.eda_end_entry, 1, 0)
        self._field(windows, "事件前基线起点（秒）", self.baseline_start_entry, 1, 1)
        self._field(windows, "事件前基线终点（秒）", self.baseline_end_entry, 2, 0)
        self._field(windows, "事件后响应起点（秒）", self.response_start_entry, 2, 1)
        self._field(windows, "事件后响应终点（秒）", self.response_end_entry, 3, 0)

        self._additional_card(parent, "eda", "重叠窗口与事件基线校正",
                              "subtract_mean/median=减基线均值/中位数；none=不校正；ratio=响应/基线；percent=相对变化百分比。AUC 是有效响应窗内校正信号的有符号面积；无响应比例按有效事件统计。",
                              COLORS["lemon"])
        events = self._card(
            parent,
            "事件标签来源（可选）",
            "可使用独立事件表，或从原记录中连续变化的非空标签段提取事件起点；不会把每个重复采样标签当成一个新事件。",
            COLORS["lavender"],
        )
        events.grid_columnconfigure(1, weight=1)
        self.event_source_combo = self._combo(
            events, self.event_source_var, ["无事件", "事件文件", "原始标签列"]
        )
        self.event_source_combo.grid(row=0, column=0, sticky="w", padx=6, pady=5)
        self.event_source_combo.bind("<<ComboboxSelected>>", self._on_event_source_changed)
        self.event_path_var = tk.StringVar(value="")
        self.event_path_label = tk.Label(
            events,
            textvariable=self.event_path_var,
            bg=COLORS["lavender"],
            fg=COLORS["muted"],
            anchor="w",
            font=(self.font_family, 9),
        )
        self.event_path_label.grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        self.event_browse_btn = self._action_button(
            events, "选择事件表", self.choose_event_file, "coral"
        )
        self.event_browse_btn.shadow_wrapper.grid(row=0, column=2, sticky="e", padx=6, pady=4)
        self.event_time_combo = self._combo(events, self.event_time_col_var, [], width=18)
        self.event_label_combo = self._combo(events, self.event_label_col_var, [], width=18)
        self.marker_combo = self._combo(events, self.marker_col_var, [], width=20)
        self._field(events, "事件表时间列（秒）", self.event_time_combo, 1, 0)
        self._field(events, "事件表标签列", self.event_label_combo, 1, 1)
        self._field(events, "原始连续标签列", self.marker_combo, 2, 0)

        action = self._card(
            parent,
            "运行皮电分析",
            "输出原始/清理/tonic/phasic 信号、SCR 峰、固定窗口统计，以及可选的基线响应和标签汇总。",
            COLORS["coral"],
        )
        row = tk.Frame(action, bg=COLORS["coral"])
        row.pack(fill="x", padx=5, pady=3)
        self.eda_run_button = self._action_button(row, "分析当前皮电通道", self.run_eda, "green")
        self.eda_run_button.shadow_wrapper.pack(side="left", padx=(0, 10))
        self.eda_status = self._label(row, "等待皮电输入与单位选择。", color=COLORS["muted"], wraplength=850)
        self.eda_status.pack(side="left", fill="x", expand=True)

        output = self._card(
            parent,
            "皮电结果",
            "峰检测与分解是方法估计；请查看图形、有效性状态和参数，不直接将信号强度解释为心理或临床标签。",
            COLORS["white"],
        )
        self.eda_chart_host = tk.Frame(output, bg=COLORS["white"], height=480)
        self.eda_chart_host.pack_propagate(False)
        self.eda_chart_host.pack(fill="x", expand=False, padx=5, pady=4)
        self._chart_placeholder(
            self.eda_chart_host, "完成皮电分析后显示原始、清理、tonic 与 phasic 曲线。"
        )
        self._build_eda_range_controls(output)
        tabs = ttk.Notebook(output)
        tabs.pack(fill="both", expand=True, padx=4, pady=4)
        for key, title in (
            ("eda_windows", "固定窗口"),
            ("eda_peaks", "SCR 峰"),
            ("eda_events", "逐事件"),
            ("eda_event_summary", "按标签汇总"),
        ):
            tab = tk.Frame(tabs, bg=COLORS["white"])
            tabs.add(tab, text=title)
            self._make_table(tab, key, height=8)
        self.eda_warnings = tk.Text(
            output,
            height=4,
            wrap="word",
            bg=COLORS["lemon"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            font=(self.font_family, 9),
        )
        self.eda_warnings.pack(fill="x", padx=5, pady=(4, 8))


def run_smoke_test(output_path: Path | None = None) -> int:
    """Run one bounded, synthetic, end-to-end integration without opening Tk."""
    import experimental
    import core

    pd = _pandas()
    np = _numpy()
    required_matrices = {
        "pearson",
        "spearman",
        "covariance",
        "partial",
        "lagged",
        "coherence",
        "standardized_euclidean",
    }
    demo = core.demo_data()
    prepared = core.prepare_data(
        demo.copy(deep=True),
        {
            "value_cols": ["signal_a", "signal_b"],
            "time_col": "time_seconds",
            "time_kind": "numeric",
            "fs": 10.0,
            "missing": "reject",
            "duplicate": "reject",
            "resample": None,
            "transform": "none",
        },
    )
    analysis = core.analyze(
        prepared,
        {
            "max_lag": 24,
            "period": 12,
            "lag": 1,
            "band_low": 0.05,
            "band_high": 4.5,
            "nperseg": 128,
        },
    )
    missing_matrices = sorted(required_matrices - set(analysis.get("matrices", {})))
    if missing_matrices:
        raise RuntimeError(f"Missing matrix outputs: {missing_matrices}")
    pearson = np.asarray(analysis["matrices"]["pearson"], dtype=float)
    if not np.allclose(np.diag(pearson), 1.0, atol=1e-8, equal_nan=False):
        raise RuntimeError("Pearson diagonal numeric check failed.")
    coherence = np.asarray(analysis["matrices"]["coherence"], dtype=float)
    off_diagonal = coherence[~np.eye(coherence.shape[0], dtype=bool)]
    finite_coherence = off_diagonal[np.isfinite(off_diagonal)]
    if finite_coherence.size and np.allclose(finite_coherence, 1.0, atol=1e-12):
        raise RuntimeError("Coherence appears to use only one Welch segment.")

    forecast_result = core.forecast(
        prepared,
        "signal_a",
        {
            "horizon": 8,
            "test_fraction": 0.2,
            "period": 12,
            "order": (1, 1, 1),
            "seasonal_order": (1, 0, 1, 12),
            "models": ["Naive", "SeasonalNaive", "ETS", "ARIMA", "SARIMA"],
            "missing": "reject",
        },
    )

    eda_raw = experimental.demo_eda()
    eda_prepared = core.prepare_data(
        eda_raw.copy(deep=True),
        {
            "value_cols": ["EDA_uS"],
            "time_col": "time",
            "time_kind": "numeric",
            "fs": 10.0,
            "missing": "reject",
            "duplicate": "reject",
            "resample": None,
            "transform": "none",
        },
    )
    events = experimental.markers_to_events(
        eda_raw, "event", time_col="time", fs=10.0, time_kind="numeric"
    )
    eda_result = experimental.analyze_eda(
        eda_prepared,
        "EDA_uS",
        {
            "unit": "uS",
            "method": "highpass",
            "clean": "neurokit",
            "cutoff": 0.05,
            "relative_threshold": 0.1,
            "absolute_threshold": 0.01,
            "window_seconds": 60.0,
            "start_seconds": 0.0,
            "end_seconds": None,
            "baseline_start": -2.0,
            "baseline_end": 0.0,
            "response_start": 1.0,
            "response_end": 5.0,
        },
        event_df=events,
    )
    required_eda_tables = {
        "eda_signals",
        "eda_peaks",
        "eda_windows",
        "eda_events",
        "eda_event_summary",
    }
    missing_eda = sorted(required_eda_tables - set(eda_result.get("tables", {})))
    if missing_eda:
        raise RuntimeError(f"Missing EDA outputs: {missing_eda}")

    export_analysis = dict(analysis)
    export_analysis["tables"] = dict(analysis.get("tables", {}))
    export_analysis["matrices"] = dict(analysis.get("matrices", {}))
    export_analysis["meta"] = dict(analysis.get("meta", {}))
    for name, frame in eda_result.get("tables", {}).items():
        export_analysis["tables"][f"eda_{name}"] = frame
    export_analysis["warnings"] = list(analysis.get("warnings", [])) + list(
        eda_result.get("warnings", [])
    )
    export_analysis["meta"]["eda"] = dict(eda_result.get("meta", {}))
    research_path = resource_path("research_notes.json")
    if not research_path.is_file():
        raise RuntimeError(f"Research notes are missing from the application resources: {research_path}")

    with tempfile.TemporaryDirectory(prefix="pytsa-smoke-") as temp:
        output = Path(temp) / "integration-output"
        export = core.export_results(
            output,
            prepared,
            export_analysis,
            forecast_result=forecast_result,
            figures={},
            research_path=research_path,
        )
        filenames = [Path(path).name for path in export.get("files", [])]
        if not any(name.lower().endswith(".xlsx") for name in filenames):
            raise RuntimeError("XLSX export was not produced.")
        if not any(name.lower().endswith(".html") for name in filenames):
            raise RuntimeError("Offline HTML export was not produced.")
        if not any(name.lower().endswith(".csv") for name in filenames):
            raise RuntimeError("CSV export was not produced.")
        forecast_models = (
            forecast_result.get("metrics", pd.DataFrame())["model"].astype(str).tolist()
            if not forecast_result.get("metrics", pd.DataFrame()).empty
            else []
        )
        forecast_metric_records = json.loads(
            forecast_result.get("metrics", pd.DataFrame()).to_json(orient="records")
        )
        welch_segment_length = min(
            int(analysis.get("meta", {}).get("parameters", {}).get("nperseg", 256)),
            int(analysis.get("meta", {}).get("n", len(prepared["data"])) // 2),
        )
        smoke_report = {
                    "overall": "PASS",
                    "source": "SYNTHETIC_ONLY",
                    "input_rows": int(len(demo)),
                    "analysis_matrices": sorted(analysis["matrices"]),
                    "pearson_diagonal": np.diag(pearson).tolist(),
                    "welch_segment_length": welch_segment_length,
                    "forecast_models": forecast_models,
                    "forecast_metrics": forecast_metric_records,
                    "eda_tables": sorted(eda_result["tables"]),
                    "eda_events": int(len(events)),
                    "export_files": filenames,
                    "research_sha256": _file_sha256(research_path),
        }
        rendered_report = json.dumps(smoke_report, ensure_ascii=False, indent=2)
        print(rendered_report)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered_report + "\n", encoding="utf-8")
    return 0


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-output", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--ui-capture",
        nargs="?",
        const="",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--ui-capture-eda",
        nargs="?",
        const="",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.smoke_test:
        try:
            return run_smoke_test(args.smoke_output)
        except BaseException as exc:
            if args.smoke_output is not None:
                failure = {
                    "overall": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
                try:
                    args.smoke_output.parent.mkdir(parents=True, exist_ok=True)
                    args.smoke_output.write_text(
                        json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            print(f"Smoke test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    capture_eda_only = args.ui_capture_eda is not None
    capture_value = args.ui_capture_eda if capture_eda_only else args.ui_capture
    capture_dir = None
    if capture_value is not None:
        capture_dir = (
            Path(capture_value).expanduser()
            if capture_value
            else Path(tempfile.gettempdir()) / "pytsa-ui-capture"
        )
    set_windows_identity_and_dpi()
    root = tk.Tk()
    PytsaApp(root, capture_dir=capture_dir, capture_eda_only=capture_eda_only)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

