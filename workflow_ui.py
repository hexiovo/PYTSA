"""Workflow workbench presentation mixin for PYTSA's Tk application.

This module owns workflow widgets and their UI-side state. Scientific work is
delegated to the existing core/experimental functions and WorkspaceStore.
"""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk


class WorkflowCancelled(RuntimeError):
    """Raised when a cooperative workflow task reaches a cancellation point."""


class WorkflowMixin:
    def _init_workflow_state(self) -> None:
        self._workflow_store_instance = None
        self._workflow_report_module = None
        self._workflow_editable_widgets: list[tk.Widget] = []
        self._workflow_chart_selectors: dict[str, object] = {}
        self._workflow_chart_readouts: dict[str, tk.StringVar] = {}
        self._workflow_chart_time_meta: dict[str, dict] = {}
        self.workflow_annotations: list[dict] = []
        self.workflow_selected_interval: dict | None = None
        self.workflow_preflight_issues: list[dict] = []
        self.workflow_preflight_signature: tuple | None = None
        self.workflow_preflight_source_identity = ""
        self.workflow_source_info: dict = {}
        self.workflow_current_project_path: Path | None = None
        self.workflow_current_run_id = ""
        self.workflow_last_batch_id = ""
        self.workflow_last_batch_result: dict | None = None
        self.workflow_parameter_records: dict[str, dict] = {}
        self.workflow_report_records: dict[str, dict] = {}
        self.workflow_history_records: dict[str, dict] = {}
        self.workflow_recent_records: list[dict] = []
        self.workflow_comparison_result: dict | None = None
        self.workflow_report_template: dict | None = None
        self.workflow_task_id = ""
        self.workflow_cancel_event: threading.Event | None = None
        self.workflow_progress_var = tk.StringVar(value="就绪。")
        self.workflow_task_id_var = tk.StringVar(value="")
        self.workflow_project_name_var = tk.StringVar(value="")
        self.workflow_parameter_name_var = tk.StringVar(value="")
        self.workflow_parameter_kind_var = tk.StringVar(value="combined")
        self.workflow_batch_output_var = tk.StringVar(
            value=str(Path.home() / "PYTSA_batch_results")
        )
        self.workflow_batch_event_policy_var = tk.StringVar(value="无事件文件")
        self.workflow_batch_shared_event_path_var = tk.StringVar(value="")
        self.workflow_batch_event_time_col_var = tk.StringVar(value="time")
        self.workflow_batch_event_label_col_var = tk.StringVar(value="label")
        self.workflow_batch_marker_col_var = tk.StringVar(value="")
        self.workflow_batch_forecast_var = tk.BooleanVar(value=False)
        self.workflow_batch_eda_var = tk.BooleanVar(value=False)
        self.workflow_preflight_note_var = tk.StringVar(value="当前数据尚未运行预检。")
        self.workflow_history_left_var = tk.StringVar(value="")
        self.workflow_history_right_var = tk.StringVar(value="")
        self.workflow_log_detail_var = tk.StringVar(value="选择日志查看详细记录。")
        self.workflow_annotation_label_var = tk.StringVar(value="")
        self.workflow_annotation_note_var = tk.StringVar(value="")
        self.workflow_range_var = tk.StringVar(value="尚未选择区间。")
        self.workflow_eda_range_note_var = tk.StringVar(value="")
        self.workflow_report_name_var = tk.StringVar(value="默认报告")
        self.workflow_report_title_var = tk.StringVar(value="PYTSA 分析报告")
        self.workflow_report_figure_key_var = tk.StringVar(value="time_series")
        self.workflow_report_figure_title_var = tk.StringVar(value="时间序列")
        self.workflow_report_all_figures_var = tk.BooleanVar(value=True)
        self.workflow_report_experiment_vars = {
            key: tk.StringVar(value="")
            for key in ("study", "date", "subject", "condition", "notes")
        }
        self.workflow_report_section_vars = {
            key: tk.BooleanVar(value=True)
            for key in (
                "summary", "tests", "matrices", "forecast", "eda",
                "warnings", "methods", "annotations",
            )
        }
        self.workflow_report_format_vars = {
            key: tk.BooleanVar(value=True)
            for key in ("csv", "xlsx", "json", "png", "html")
        }
        self.workflow_report_figure_titles: dict[str, str] = {}
        self.workflow_report_templates_by_name: dict[str, dict] = {}
        self.workflow_batch_files: list[str] = []
        self.workflow_batch_event_map: dict[str, str] = {}
        self.workflow_batch_result_rows: list[dict] = []
        self.workflow_suppress_preflight = False
        self.workflow_recent_kind_var = tk.StringVar(value="全部")
        self.workflow_template_selected_id = ""

    def _workflow_register_editable(self, widget: tk.Widget) -> tk.Widget:
        if widget not in self._workflow_editable_widgets:
            self._workflow_editable_widgets.append(widget)
        return widget

    def _workflow_set_busy_state(self, busy: bool) -> None:
        for widget in self._workflow_editable_widgets:
            try:
                if busy:
                    widget._workflow_previous_state = str(widget.cget("state"))
                    widget.configure(state=tk.DISABLED)
                else:
                    old_state = getattr(widget, "_workflow_previous_state", None)
                    if old_state is not None:
                        widget.configure(state=old_state)
                        del widget._workflow_previous_state
            except (tk.TclError, AttributeError):
                continue

    def _workflow_get_store(self):
        if self._workflow_store_instance is None:
            try:
                from workspace_service import WorkspaceStore
            except Exception as exc:
                raise RuntimeError(f"工作台服务未就绪：{exc}") from exc
            self._workflow_store_instance = WorkspaceStore()
        return self._workflow_store_instance

    def _workflow_get_report_module(self):
        if self._workflow_report_module is None:
            try:
                import report_export
            except Exception as exc:
                raise RuntimeError(f"报告导出模块未就绪：{exc}") from exc
            self._workflow_report_module = report_export
        return self._workflow_report_module

    @staticmethod
    def _workflow_normalize_path(path: str | Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))

    @staticmethod
    def _workflow_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _workflow_resource_path(relative: str) -> Path:
        import sys

        app_module = sys.modules.get("app")
        resolver = getattr(app_module, "resource_path", None) if app_module is not None else None
        if callable(resolver):
            return Path(resolver(relative))
        return Path(__file__).resolve().parent / relative

    def _workflow_write_log(
        self,
        run_id: str,
        level: str,
        message: str,
        *,
        details=None,
        exception=None,
    ) -> None:
        if not run_id:
            return
        try:
            self._workflow_get_store().write_run_log(
                run_id,
                level,
                message,
                details=details,
                exception=exception,
            )
        except Exception:
            # Log persistence must not mask the original analysis/export result.
            pass

    def _workflow_on_task_started(self, task_id: str, title: str, cancel_event) -> None:
        self.workflow_task_id = task_id
        self.workflow_task_id_var.set(task_id)
        self.workflow_cancel_event = cancel_event
        self.workflow_progress_var.set(f"{title}：正在启动。")
        cancel_button = getattr(self, "workflow_cancel_button", None)
        if cancel_button is not None:
            cancel_button.configure(state=tk.NORMAL if cancel_event is not None else tk.DISABLED)
        bar = getattr(self, "workflow_progress_bar", None)
        if bar is not None:
            bar.configure(mode="determinate", maximum=1, value=0)

    def _workflow_on_task_progress(self, task_id: str, event: dict) -> None:
        if task_id != self.workflow_task_id:
            return
        stage = str(event.get("stage", "处理中"))
        message = str(event.get("message", ""))
        completed = event.get("completed")
        total = event.get("total")
        elapsed = event.get("elapsed_seconds")
        parts = [stage]
        if message:
            parts.append(message)
        if completed is not None and total is not None:
            parts.append(f"文件 {completed}/{total}")
        if isinstance(elapsed, (int, float)) and math.isfinite(float(elapsed)):
            parts.append(f"已用 {float(elapsed):.1f} 秒")
        status = event.get("status")
        if status:
            parts.append(str(status))
        self.workflow_progress_var.set(" · ".join(parts))
        bar = getattr(self, "workflow_progress_bar", None)
        if bar is not None and completed is not None and total is not None:
            try:
                bar.configure(mode="determinate", maximum=max(1, int(total)))
                bar.configure(value=max(0, min(int(completed), int(total))))
            except (TypeError, ValueError, tk.TclError):
                pass
        if message:
            self._set_status(message)

    def _workflow_on_task_finished(self, task_id: str, cancelled: bool = False) -> None:
        if task_id != self.workflow_task_id:
            return
        self.workflow_cancel_event = None
        cancel_button = getattr(self, "workflow_cancel_button", None)
        if cancel_button is not None:
            cancel_button.configure(state=tk.DISABLED)
        self.workflow_progress_var.set(
            "当前任务已取消。" if cancelled else "当前任务已结束。"
        )

    def _workflow_cancel_active_task(self) -> None:
        event = self.workflow_cancel_event
        if event is None:
            return
        event.set()
        self.workflow_progress_var.set(
            "已请求取消；当前不可中断的计算会先返回，再在阶段边界停止。"
        )
        self._set_status("已请求取消；正在等待当前可中断阶段结束。", warning=True)

    def _workflow_start_task(self, title: str, operation, on_success) -> None:
        self._start_worker(
            title,
            operation,
            on_success,
            cancelable=True,
            contextual=True,
        )

    @staticmethod
    def _workflow_check_cancel(cancel_event) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise WorkflowCancelled("任务已在安全阶段边界取消。")

    def _build_workflow_page(self, parent: tk.Misc) -> None:
        self._title_plaque(
            parent,
            "工作台",
            "管理项目、参数模板、批次、历史、日志与最近使用；预检只报告问题，不会自动修改数据。",
            COLORS["lemon"],
        )
        status = self._card(
            parent,
            "任务进度与取消",
            "进度按实际阶段或已完成文件显示；不可中断的核心计算会在返回后响应取消。",
            COLORS["coral"],
        )
        status.grid_columnconfigure(0, weight=1)
        self.workflow_progress_label = tk.Label(
            status,
            textvariable=self.workflow_progress_var,
            bg=COLORS["coral"],
            fg=COLORS["ink"],
            anchor="w",
            justify="left",
            wraplength=740,
            font=(self.font_family, 9),
        )
        self.workflow_progress_label.grid(row=0, column=0, sticky="ew", padx=6, pady=5)
        self.workflow_progress_bar = ttk.Progressbar(status, mode="determinate", maximum=1)
        self.workflow_progress_bar.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        self.workflow_cancel_button = self._action_button(
            status,
            "取消当前任务",
            self._workflow_cancel_active_task,
            "coral",
            track_busy=False,
        )
        self.workflow_cancel_button.shadow_wrapper.grid(row=0, column=1, rowspan=2, padx=6, pady=5)
        self.workflow_cancel_button.configure(state=tk.DISABLED)

        self.workflow_notebook = ttk.Notebook(parent)
        self.workflow_notebook.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        tabs = (
            ("project", "项目"),
            ("templates", "参数模板"),
            ("preflight", "数据预检"),
            ("batch", "批量处理"),
            ("history", "历史比较"),
            ("annotations", "区间与标注"),
            ("logs", "持久日志"),
            ("recent", "最近使用"),
        )
        self.workflow_tabs: dict[str, tk.Frame] = {}
        for key, label in tabs:
            frame = tk.Frame(self.workflow_notebook, bg=COLORS["canvas"])
            self.workflow_notebook.add(frame, text=label)
            self.workflow_tabs[key] = frame
        self._build_workflow_project_tab(self.workflow_tabs["project"])
        self._build_workflow_templates_tab(self.workflow_tabs["templates"])
        self._build_workflow_preflight_tab(self.workflow_tabs["preflight"])
        self._build_workflow_batch_tab(self.workflow_tabs["batch"])
        self._build_workflow_history_tab(self.workflow_tabs["history"])
        self._build_workflow_annotations_tab(self.workflow_tabs["annotations"])
        self._build_workflow_logs_tab(self.workflow_tabs["logs"])
        self._build_workflow_recent_tab(self.workflow_tabs["recent"])

    def _build_workflow_project_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "项目保存与恢复",
            "项目内嵌原始表、事件表、参数、分析结果与人工标注；恢复不会自动读取源文件。",
            COLORS["white"],
        )
        card.grid_columnconfigure(1, weight=1)
        self._label(card, "项目名称").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        name_entry = self._entry(card, self.workflow_project_name_var, width=32)
        name_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        self._workflow_register_editable(name_entry)
        self.workflow_project_path_label = self._label(
            card, "尚未保存为项目文件。", color=COLORS["muted"], wraplength=690
        )
        self.workflow_project_path_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        row = tk.Frame(card, bg=COLORS["white"])
        row.grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=5)
        for label, command, color in (
            ("保存项目", self.workflow_save_project, "green"),
            ("另存为…", self.workflow_save_project_as, "lavender"),
            ("打开项目…", self.workflow_open_project, "sky"),
        ):
            button = self._action_button(row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=4)
        self.workflow_project_status = self._label(
            card, "项目文件需由用户明确选择；恢复后数据来自项目内嵌副本。", color=COLORS["muted"], wraplength=760
        )
        self.workflow_project_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=5)

    def _build_workflow_templates_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "参数模板",
            "保存可复用的预处理、分析、预测、皮电与事件列设置；应用前会检查列名、采样率和单位。",
            COLORS["white"],
        )
        card.grid_columnconfigure(0, weight=1)
        self.workflow_parameter_tree = self._workflow_tree(
            card,
            ("name", "kind", "updated"),
            ("模板名称", "范围", "更新时间"),
            height=7,
            selectmode="browse",
        )
        row = tk.Frame(card, bg=COLORS["white"])
        row.pack(fill="x", padx=4, pady=5)
        self._label(row, "名称").pack(side="left", padx=(4, 3))
        name = self._entry(row, self.workflow_parameter_name_var, width=22)
        name.pack(side="left", padx=3)
        self._workflow_register_editable(name)
        self._label(row, "保存范围").pack(side="left", padx=(12, 3))
        kind = self._combo(
            row,
            self.workflow_parameter_kind_var,
            ["combined", "prepare", "analysis", "forecast", "eda"],
            width=14,
        )
        kind.pack(side="left", padx=3)
        self._workflow_register_editable(kind)
        button_row = tk.Frame(card, bg=COLORS["white"])
        button_row.pack(fill="x", padx=4, pady=3)
        for label, command, color in (
            ("刷新", self.workflow_refresh_parameter_templates, "sky"),
            ("保存当前设置", self.workflow_save_parameter_template, "green"),
            ("应用所选", self.workflow_apply_parameter_template, "coral"),
            ("复制", self.workflow_copy_parameter_template, "lavender"),
            ("导入…", self.workflow_import_parameter_template, "sky"),
            ("导出…", self.workflow_export_parameter_template, "lavender"),
            ("删除", self.workflow_delete_parameter_template, "coral"),
        ):
            button = self._action_button(button_row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=3, pady=3)
        self.workflow_parameter_note = self._label(
            card,
            "未匹配的列不会自动选择；请在映射对话框明确指定。单位或采样率不一致时会显示适配选项。",
            color=COLORS["muted"],
            wraplength=760,
        )
        self.workflow_parameter_note.pack(fill="x", padx=6, pady=5)

    def _build_workflow_preflight_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "数据质量预检",
            "预检只检查新来源边界一次或按需手动运行；发现值仅作提示，不会填补、删除、重采样或排除数据。",
            COLORS["lemon"],
        )
        card.grid_columnconfigure(0, weight=1)
        self.workflow_preflight_tree = self._workflow_tree(
            card,
            ("severity", "check", "column", "count", "rows", "times", "message"),
            ("级别", "检查项", "列", "数量", "行位置", "时间范围", "说明"),
            height=12,
            selectmode="browse",
        )
        row = tk.Frame(card, bg=COLORS["lemon"])
        row.pack(fill="x", padx=4, pady=4)
        refresh = self._action_button(row, "对当前选择重新预检", self.workflow_run_preflight, "green")
        refresh.shadow_wrapper.pack(side="left", padx=4)
        self._label(row, "", textvariable=self.workflow_preflight_note_var, color=COLORS["muted"], wraplength=650).pack(
            side="left", fill="x", expand=True, padx=8
        )

    def _build_workflow_batch_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "批量分析",
            "每个文件单独预检与处理，失败项隔离；事件表必须明确选择无事件、共用文件或逐文件映射。",
            COLORS["white"],
        )
        card.grid_columnconfigure(0, weight=1)
        self.workflow_batch_tree = self._workflow_tree(
            card,
            ("path", "event"),
            ("输入文件", "事件文件映射"),
            height=6,
            selectmode="extended",
        )
        row = tk.Frame(card, bg=COLORS["white"])
        row.pack(fill="x", padx=4, pady=4)
        for label, command, color in (
            ("添加文件…", self.workflow_batch_add_files, "sky"),
            ("移除所选", self.workflow_batch_remove_selected, "lavender"),
            ("为所选文件指定事件表…", self.workflow_batch_map_event, "coral"),
        ):
            button = self._action_button(row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=3)
            if label.startswith("为所选"):
                self.workflow_batch_map_event_button = button
        options = tk.Frame(card, bg=COLORS["white"])
        options.pack(fill="x", padx=5, pady=4)
        self._label(options, "事件文件策略").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        policy = self._combo(
            options,
            self.workflow_batch_event_policy_var,
            ["无事件文件", "所有文件共用事件表", "逐文件映射事件表"],
            width=22,
        )
        policy.grid(row=0, column=1, sticky="w", padx=4, pady=3)
        self._workflow_register_editable(policy)
        policy.bind("<<ComboboxSelected>>", self._workflow_batch_policy_changed)
        self._label(options, "事件时间列").grid(row=0, column=2, sticky="w", padx=4, pady=3)
        time_col = self._entry(options, self.workflow_batch_event_time_col_var, width=14)
        self.workflow_batch_event_time_widget = time_col
        time_col.grid(row=0, column=3, sticky="w", padx=4, pady=3)
        self._workflow_register_editable(time_col)
        self._label(options, "事件标签列").grid(row=0, column=4, sticky="w", padx=4, pady=3)
        label_col = self._entry(options, self.workflow_batch_event_label_col_var, width=14)
        self.workflow_batch_event_label_widget = label_col
        label_col.grid(row=0, column=5, sticky="w", padx=4, pady=3)
        self._workflow_register_editable(label_col)
        self._label(options, "或原始连续标签列").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        marker = self._entry(options, self.workflow_batch_marker_col_var, width=20)
        marker.grid(row=1, column=1, sticky="w", padx=4, pady=3)
        self._workflow_register_editable(marker)
        self._label(options, "共用事件表").grid(row=1, column=2, sticky="w", padx=4, pady=3)
        shared = self._entry(options, self.workflow_batch_shared_event_path_var, width=30)
        self.workflow_batch_shared_event_widget = shared
        shared.grid(row=1, column=3, columnspan=2, sticky="ew", padx=4, pady=3)
        self._workflow_register_editable(shared)
        shared_button = self._action_button(
            options, "选择…", self.workflow_batch_choose_shared_event, "sky"
        )
        self.workflow_batch_shared_event_button = shared_button
        shared_button.shadow_wrapper.grid(row=1, column=5, sticky="w", padx=4, pady=3)
        self._label(options, "每个文件输出目录").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        output = self._entry(options, self.workflow_batch_output_var, width=42)
        output.grid(row=2, column=1, columnspan=4, sticky="ew", padx=4, pady=3)
        self._workflow_register_editable(output)
        choose_out = self._action_button(
            options, "浏览…", self.workflow_batch_choose_output, "lavender"
        )
        choose_out.shadow_wrapper.grid(row=2, column=5, sticky="w", padx=4, pady=3)
        options.grid_columnconfigure(4, weight=1)

        flags = tk.Frame(card, bg=COLORS["white"])
        flags.pack(fill="x", padx=5, pady=4)
        for text, variable in (
            ("同时运行预测分支", self.workflow_batch_forecast_var),
            ("同时运行皮电分支", self.workflow_batch_eda_var),
        ):
            control = ttk.Checkbutton(flags, text=text, variable=variable)
            control.pack(side="left", padx=7)
            self._workflow_register_editable(control)
        run_row = tk.Frame(card, bg=COLORS["white"])
        run_row.pack(fill="x", padx=4, pady=4)
        for label, command, color in (
            ("开始批量分析", self.workflow_batch_start, "green"),
            ("重试上次失败项", self.workflow_batch_retry, "coral"),
            ("打开批量汇总…", self.workflow_batch_open_summary, "sky"),
        ):
            button = self._action_button(run_row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=3)
        self.workflow_batch_result_tree = self._workflow_tree(
            card,
            ("display_name", "status", "elapsed_seconds", "output_dir", "history_run_id", "error"),
            ("文件", "状态", "耗时（秒）", "输出目录", "历史 ID", "说明"),
            height=7,
            selectmode="browse",
        )
        self._workflow_batch_policy_changed()

    def _build_workflow_history_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "不可变运行历史",
            "历史记录保存当次参数与结果快照；曲线仅在分析类型、量纲和时间基准兼容时叠加，不插值或重采样。",
            COLORS["white"],
        )
        card.grid_columnconfigure(0, weight=1)
        self.workflow_history_tree = self._workflow_tree(
            card,
            ("run_id", "kind", "saved_at", "source", "metrics"),
            ("历史 ID", "类型", "保存时间", "来源", "指标概览"),
            height=8,
            selectmode="extended",
        )
        row = tk.Frame(card, bg=COLORS["white"])
        row.pack(fill="x", padx=4, pady=4)
        for label, command, color in (
            ("刷新历史", self.workflow_refresh_history, "sky"),
            ("比较所选两次", self.workflow_compare_selected_runs, "green"),
        ):
            button = self._action_button(row, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=4)
        self.workflow_history_details = self._workflow_tree(
            card,
            ("group", "name", "left", "right", "delta", "unit", "status"),
            ("类别", "项目", "左侧", "右侧", "差值", "单位", "可比性"),
            height=7,
            selectmode="browse",
        )
        self.workflow_comparison_chart_host = tk.Frame(
            card, bg=COLORS["white"], height=300,
            highlightbackground=COLORS["ink"], highlightthickness=1,
        )
        self.workflow_comparison_chart_host.pack_propagate(False)
        self.workflow_comparison_chart_host.pack(fill="x", expand=False, padx=5, pady=4)
        self._chart_placeholder(self.workflow_comparison_chart_host, "选择两次运行并比较后显示兼容曲线。")

    def _build_workflow_annotations_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "区间查看与人工标注",
            "拖动时域图上的选区会得到记录起点后的秒数。标注只作记录与报告内容，不会自动排除样本或改变计算。",
            COLORS["lemon"],
        )
        card.grid_columnconfigure(0, weight=1)
        self._label(card, "", textvariable=self.workflow_range_var, color=COLORS["muted"], wraplength=740).pack(
            fill="x", padx=6, pady=5
        )
        fields_row = tk.Frame(card, bg=COLORS["lemon"])
        fields_row.pack(fill="x", padx=4, pady=(4, 2))
        self._label(fields_row, "标注名称").pack(side="left", padx=(4, 3))
        label = self._entry(fields_row, self.workflow_annotation_label_var, width=20)
        label.pack(side="left", padx=3)
        self._workflow_register_editable(label)
        self._label(fields_row, "备注").pack(side="left", padx=(10, 3))
        note = self._entry(fields_row, self.workflow_annotation_note_var, width=38)
        note.pack(side="left", fill="x", expand=True, padx=3)
        self._workflow_register_editable(note)
        actions_row = tk.Frame(card, bg=COLORS["lemon"])
        actions_row.pack(fill="x", padx=4, pady=(2, 4))
        for title, command, color in (
            ("添加选区标注", self.workflow_add_annotation, "green"),
            ("编辑所选", self.workflow_edit_annotation, "sky"),
            ("删除所选", self.workflow_delete_annotation, "coral"),
        ):
            button = self._action_button(actions_row, title, command, color)
            button.shadow_wrapper.pack(side="left", padx=3)
        self.workflow_annotation_tree = self._workflow_tree(
            card,
            ("label", "start_seconds", "end_seconds", "chart", "note"),
            ("标注", "起点（秒）", "终点（秒）", "来源图表", "备注"),
            height=10,
            selectmode="browse",
        )

    def _build_workflow_logs_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "持久运行日志",
            "日志按运行 ID 关联；错误详情保留完整 traceback，可复制用于排查。",
            COLORS["white"],
        )
        card.grid_columnconfigure(0, weight=1)
        self.workflow_log_tree = self._workflow_tree(
            card,
            ("timestamp", "run_id", "level", "message"),
            ("时间", "运行 ID", "级别", "消息"),
            height=11,
            selectmode="browse",
        )
        self.workflow_log_tree.bind("<<TreeviewSelect>>", self._workflow_log_selected)
        row = tk.Frame(card, bg=COLORS["white"])
        row.pack(fill="x", padx=4, pady=4)
        refresh = self._action_button(row, "刷新日志", self.workflow_refresh_logs, "sky")
        refresh.shadow_wrapper.pack(side="left", padx=4)
        copy_btn = self._action_button(row, "复制全部日志", self.workflow_copy_logs, "lavender")
        copy_btn.shadow_wrapper.pack(side="left", padx=4)
        self.workflow_log_detail = tk.Text(
            card,
            height=8,
            wrap="word",
            bg=COLORS["mint"],
            fg=COLORS["ink"],
            relief="solid",
            bd=1,
            font=(self.font_family, 9),
        )
        self.workflow_log_detail.pack(fill="both", expand=True, padx=5, pady=4)
        self.workflow_log_detail.configure(state="disabled")

    def _build_workflow_recent_tab(self, parent: tk.Misc) -> None:
        card = self._card(
            parent,
            "最近使用",
            "最近项目与数据路径仅作快捷入口；文件缺失时不会自动替换或静默打开其他位置。",
            COLORS["sky"],
        )
        card.grid_columnconfigure(0, weight=1)
        row = tk.Frame(card, bg=COLORS["sky"])
        row.pack(fill="x", padx=4, pady=4)
        self._label(row, "类型").pack(side="left", padx=4)
        kind = self._combo(row, self.workflow_recent_kind_var, ["全部", "项目", "数据"], width=12)
        kind.pack(side="left", padx=4)
        self._workflow_register_editable(kind)
        refresh = self._action_button(row, "刷新", self.workflow_refresh_recent, "green")
        refresh.shadow_wrapper.pack(side="left", padx=4)
        open_button = self._action_button(row, "打开所选", self.workflow_open_recent, "coral")
        open_button.shadow_wrapper.pack(side="left", padx=4)
        remove_button = self._action_button(row, "移除记录", self.workflow_remove_recent, "lavender")
        remove_button.shadow_wrapper.pack(side="left", padx=4)
        self.workflow_recent_tree = self._workflow_tree(
            card,
            ("kind", "label", "path", "last_used", "exists"),
            ("类型", "名称", "路径", "最近使用", "存在"),
            height=13,
            selectmode="browse",
        )
        self.workflow_recent_tree.bind("<Double-1>", lambda _event: self.workflow_open_recent())

    def _build_report_template_controls(self, parent: tk.Misc) -> None:
        report = self._card(
            parent,
            "报告模板与章节",
            "选择章节、导出格式和图表；报告标题、实验信息及图名会随模板保存。",
            COLORS["lemon"],
        )
        report.grid_columnconfigure(0, weight=1)
        first = tk.Frame(report, bg=COLORS["lemon"])
        first.pack(fill="x", padx=4, pady=3)
        self._label(first, "模板").pack(side="left", padx=(4, 3))
        self.workflow_report_template_combo = self._combo(
            first, self.workflow_report_name_var, ["默认报告"], width=24
        )
        self.workflow_report_template_combo.pack(side="left", padx=3)
        self._workflow_register_editable(self.workflow_report_template_combo)
        for label, command, color in (
            ("刷新", self.workflow_refresh_report_templates, "sky"),
            ("加载所选", self.workflow_apply_report_template, "green"),
            ("保存当前为模板", self.workflow_save_report_template, "coral"),
            ("复制", self.workflow_copy_report_template, "lavender"),
            ("导入…", self.workflow_import_report_template, "sky"),
            ("导出模板…", self.workflow_export_report_template, "lavender"),
            ("删除", self.workflow_delete_report_template, "coral"),
        ):
            button = self._action_button(first, label, command, color)
            button.shadow_wrapper.pack(side="left", padx=2, pady=2)

        title_row = tk.Frame(report, bg=COLORS["lemon"])
        title_row.pack(fill="x", padx=4, pady=3)
        self._label(title_row, "报告标题").pack(side="left", padx=(4, 3))
        title_entry = self._entry(title_row, self.workflow_report_title_var, width=40)
        title_entry.pack(side="left", fill="x", expand=True, padx=3)
        self._workflow_register_editable(title_entry)

        experiment = tk.Frame(report, bg=COLORS["lemon"])
        experiment.pack(fill="x", padx=4, pady=3)
        exp_fields = (("实验/研究", "study"), ("日期", "date"), ("对象", "subject"), ("条件", "condition"), ("补充说明", "notes"))
        for index, (label, key) in enumerate(exp_fields):
            self._label(experiment, label).grid(row=0, column=index * 2, sticky="w", padx=3, pady=2)
            entry = self._entry(experiment, self.workflow_report_experiment_vars[key], width=14)
            entry.grid(row=0, column=index * 2 + 1, sticky="ew", padx=3, pady=2)
            self._workflow_register_editable(entry)
            experiment.grid_columnconfigure(index * 2 + 1, weight=1)

        sections = tk.LabelFrame(
            report, text="报告章节", bg=COLORS["lemon"], fg=COLORS["ink"],
            font=(self.font_family, 9, "bold"),
        )
        sections.pack(fill="x", padx=5, pady=4)
        section_labels = {
            "summary": "数据摘要", "tests": "检验结果", "matrices": "指标矩阵",
            "forecast": "预测", "eda": "皮电/事件", "warnings": "提示",
            "methods": "方法说明", "annotations": "人工标注",
        }
        for index, (key, text) in enumerate(section_labels.items()):
            widget = ttk.Checkbutton(sections, text=text, variable=self.workflow_report_section_vars[key])
            widget.grid(row=index // 4, column=index % 4, sticky="w", padx=6, pady=3)
            self._workflow_register_editable(widget)
        formats = tk.LabelFrame(
            report, text="导出格式", bg=COLORS["lemon"], fg=COLORS["ink"],
            font=(self.font_family, 9, "bold"),
        )
        formats.pack(fill="x", padx=5, pady=4)
        for index, key in enumerate(("csv", "xlsx", "json", "png", "html")):
            widget = ttk.Checkbutton(formats, text=key.upper(), variable=self.workflow_report_format_vars[key])
            widget.grid(row=0, column=index, sticky="w", padx=7, pady=3)
            self._workflow_register_editable(widget)

        figures = tk.Frame(report, bg=COLORS["lemon"])
        figures.pack(fill="x", padx=4, pady=3)
        all_figures = ttk.Checkbutton(
            figures,
            text="导出全部可用图表",
            variable=self.workflow_report_all_figures_var,
            command=self._workflow_report_all_figures_changed,
        )
        all_figures.grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self._workflow_register_editable(all_figures)
        self.workflow_report_figure_list = tk.Listbox(
            figures,
            selectmode=tk.EXTENDED,
            height=4,
            exportselection=False,
            bg=COLORS["white"],
            fg=COLORS["ink"],
            selectbackground=COLORS["lavender"],
            relief="solid",
            bd=1,
            font=(self.font_family, 9),
        )
        self.workflow_report_figure_list.grid(row=1, column=0, columnspan=4, sticky="ew", padx=5, pady=3)
        self._workflow_register_editable(self.workflow_report_figure_list)
        self._label(figures, "所选图名").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        key_combo = self._combo(figures, self.workflow_report_figure_key_var, ["time_series"], width=24)
        self.workflow_report_figure_key_combo = key_combo
        key_combo.grid(row=2, column=1, sticky="w", padx=4, pady=3)
        self._workflow_register_editable(key_combo)
        title_entry = self._entry(figures, self.workflow_report_figure_title_var, width=28)
        title_entry.grid(row=2, column=2, sticky="ew", padx=4, pady=3)
        self._workflow_register_editable(title_entry)
        set_title = self._action_button(figures, "设置图名", self.workflow_set_report_figure_title, "lavender")
        set_title.shadow_wrapper.grid(row=2, column=3, sticky="w", padx=3, pady=3)
        figures.grid_columnconfigure(2, weight=1)
        self._workflow_report_all_figures_changed()

    def _build_eda_range_controls(self, parent: tk.Misc) -> None:
        row = tk.Frame(parent, bg=COLORS["white"])
        row.pack(fill="x", padx=5, pady=(2, 5))
        self._label(row, "", textvariable=self.workflow_eda_range_note_var, color=COLORS["muted"], wraplength=560).pack(
            side="left", fill="x", expand=True, padx=4
        )
        button = self._action_button(
            row,
            "用于皮电统计区间",
            self._workflow_apply_interval_to_eda,
            "coral",
        )
        button.shadow_wrapper.pack(side="right", padx=4)

    def _workflow_tree(self, parent, columns, headings, *, height=8, selectmode="browse"):
        holder = tk.Frame(parent, bg=parent.cget("bg"))
        holder.pack(fill="both", expand=True, padx=4, pady=4)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=columns, show="headings", height=height, selectmode=selectmode)
        ybar = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(holder, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        for key, title in zip(columns, headings):
            tree.heading(key, text=title)
            width = 110 if key not in {"path", "message", "error", "note", "source", "output_dir"} else 260
            tree.column(key, width=width, minwidth=70, anchor="w", stretch=True)
        return tree

    def _workflow_clear_tree(self, tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _workflow_on_source_accepted(self) -> None:
        raw_path = getattr(self, "raw_path", None)
        label = str(getattr(self, "raw_source_label", ""))
        info = {"label": label, "path": str(raw_path) if raw_path else None, "kind": "file" if raw_path else "memory"}
        if raw_path:
            try:
                stat = Path(raw_path).stat()
                info["size_bytes"] = int(stat.st_size)
                info["mtime_ns"] = int(stat.st_mtime_ns)
                normalized = self._workflow_normalize_path(raw_path)
                info["identity"] = f"{normalized}|{stat.st_size}|{stat.st_mtime_ns}"
            except OSError:
                info["identity"] = self._workflow_normalize_path(raw_path)
        else:
            info["identity"] = f"{label or 'memory-source'}|{uuid.uuid4().hex}"
        self.workflow_source_info = info
        self.workflow_annotations = []
        self.workflow_selected_interval = None
        if hasattr(self, "event_path_var"):
            self.event_path_var.set("")
        if hasattr(self, "event_source_var"):
            self.event_source_var.set("无事件")
            self._on_event_source_changed()
        self.event_path = None
        self.workflow_preflight_issues = []
        self.workflow_preflight_signature = None
        self.workflow_preflight_source_identity = ""
        self.workflow_current_run_id = ""
        self.workflow_comparison_result = None
        if hasattr(self, "workflow_preflight_tree"):
            self._workflow_clear_tree(self.workflow_preflight_tree)
            self.workflow_preflight_note_var.set("新来源已载入；正在进行一次边界预检。")
        if hasattr(self, "workflow_annotation_tree"):
            self._workflow_render_annotations()
        self.workflow_range_var.set("尚未选择区间。")
        self.workflow_eda_range_note_var.set("")
        if not self.workflow_suppress_preflight:
            self.root.after_idle(self._workflow_preflight_new_source)

    def _workflow_preflight_new_source(self) -> None:
        if self.raw_frame is None or self.busy:
            return
        self._workflow_run_preflight(automatic=True)

    def _workflow_collect_parameters(self) -> dict:
        prep, analysis = self._collect_prepare_config()
        forecast = self._workflow_collect_forecast_config()
        eda = self._workflow_collect_eda_parameters()
        events = {
            "source": self.event_source_var.get(),
            "path": str(self.event_path) if getattr(self, "event_path", None) else "",
            "time_col": self.event_time_col_var.get(),
            "label_col": self.event_label_col_var.get(),
            "marker_col": self.marker_col_var.get(),
        }
        return {
            "prepare": prep,
            "analysis": analysis,
            "forecast": forecast,
            "eda": eda,
            "event_mapping": events,
        }

    def _workflow_collect_forecast_config(self) -> dict:
        models = [name for name, variable in self.model_vars.items() if variable.get()]
        return {
            "column": self.forecast_col_var.get(),
            "horizon": self._parse_int(self.horizon_var, "预测长度"),
            **self._additional_config("forecast"),
            "period": self._parse_int(self.period_var, "季节周期", minimum=2),
            "order": self._parse_order(self.arima_order_var, 3, "ARIMA 阶数"),
            "seasonal_order": self._parse_order(self.seasonal_order_var, 4, "SARIMA 阶数"),
            "models": models,
            "missing": {
                "拒绝缺失值": "reject",
                "因果前向填充": "ffill",
            }[self.forecast_missing_var.get()],
        }

    def _workflow_collect_eda_parameters(self) -> dict:
        unit = self.eda_unit_var.get()
        end = self._parse_float(self.eda_end_var, "分析终点", allow_blank=True)
        config = {
            **self._additional_config("eda"),
            "unit": unit,
            "method": self.eda_method_var.get(),
            "clean": self.eda_clean_var.get(),
            "cutoff": self._parse_float(self.eda_cutoff_var, "高通截止频率"),
            "relative_threshold": self._parse_float(self.eda_relative_var, "相对峰阈值"),
            "absolute_threshold": (
                0.0 if unit == "a.u." else self._parse_float(self.eda_absolute_var, "绝对峰幅值阈值")
            ),
            "window_seconds": self._parse_float(self.eda_window_var, "固定窗口长度"),
            "start_seconds": self._parse_float(self.eda_start_var, "分析起点"),
            "end_seconds": end,
            "baseline_start": self._parse_float(self.baseline_start_var, "事件基线起点"),
            "baseline_end": self._parse_float(self.baseline_end_var, "事件基线终点"),
            "response_start": self._parse_float(self.response_start_var, "事件响应起点"),
            "response_end": self._parse_float(self.response_end_var, "事件响应终点"),
        }
        return {"column": self.eda_column_var.get(), "config": config}

    def _workflow_collect_ui_state(self) -> dict:
        var_names = (
            "source_var", "sheet_var", "encoding_var", "separator_var", "time_col_var",
            "time_kind_var", "fs_var", "missing_var", "duplicate_var", "resample_var",
            "transform_var", "max_lag_var", "period_var", "lag_var", "band_low_var",
            "band_high_var", "nperseg_var", "forecast_col_var", "horizon_var",
            "forecast_missing_var", "arima_order_var", "seasonal_order_var", "output_dir_var",
            "eda_column_var", "eda_unit_var", "eda_method_var", "eda_clean_var",
            "eda_cutoff_var", "eda_relative_var", "eda_absolute_var", "eda_window_var",
            "eda_start_var", "eda_end_var", "baseline_start_var", "baseline_end_var",
            "response_start_var", "response_end_var", "event_source_var",
            "event_time_col_var", "event_label_col_var", "marker_col_var",
        )
        state = {name: getattr(self, name).get() for name in var_names}
        state["additional_options"] = self._additional_state()
        state["value_cols"] = list(self._value_columns()) if self.raw_frame is not None else []
        state["model_vars"] = {name: bool(variable.get()) for name, variable in self.model_vars.items()}
        state["excel_sheets"] = list(getattr(self, "excel_sheets", []))
        state["event_path"] = str(self.event_path) if getattr(self, "event_path", None) else ""
        state["project_name"] = self.workflow_project_name_var.get()
        try:
            state["report_template"] = self._workflow_collect_report_template()
        except Exception:
            state["report_template"] = None
            state["report_template_raw"] = {
                "name": self.workflow_report_name_var.get(),
                "title": self.workflow_report_title_var.get(),
                "sections": {key: bool(value.get()) for key, value in self.workflow_report_section_vars.items()},
                "formats": {key: bool(value.get()) for key, value in self.workflow_report_format_vars.items()},
                "figures": "all" if self.workflow_report_all_figures_var.get() else self._workflow_selected_report_figures(),
                "figure_titles": dict(self.workflow_report_figure_titles),
                "experiment": {key: value.get() for key, value in self.workflow_report_experiment_vars.items()},
            }
        return state

    def _workflow_build_project_state(self) -> dict:
        if self.raw_frame is None:
            raise ValueError("请先载入数据后再保存项目。")
        try:
            parameters = self._workflow_collect_parameters()
        except Exception:
            parameters = {
                "prepare": copy.deepcopy(getattr(self, "prep_config_used", {})),
                "analysis": copy.deepcopy(getattr(self, "analysis_config_used", {})),
                "forecast": copy.deepcopy(getattr(self, "forecast_config_used", {})),
                "eda": {"column": self.eda_column_var.get(), "config": copy.deepcopy(getattr(self, "eda_config_used", {}))},
                "event_mapping": {},
            }
        source = dict(self.workflow_source_info)
        source.update(
            {
                "label": self.raw_source_label,
                "sheet": self.sheet_var.get(),
                "columns": [str(name) for name in self.raw_frame.columns],
                "time_col": self.time_col_var.get(),
                "value_cols": list(self._value_columns()),
                "sampling_rate": self._parse_float(self.fs_var, "采样率 fs"),
                "encoding": self.encoding_var.get(),
                "separator": self.separator_var.get(),
                "event_path": str(self.event_path) if getattr(self, "event_path", None) else "",
            }
        )
        return {
            "name": self.workflow_project_name_var.get().strip() or self.raw_source_label,
            "source": source,
            "raw_frame": self.raw_frame,
            "event_frame": self.event_frame,
            "params": parameters,
            "results": {
                "prepared": self.prepared,
                "analysis": self.analysis,
                "forecast_result": self.forecast_result,
                "eda_result": self.eda_result,
                "prep_config_used": getattr(self, "prep_config_used", {}),
                "analysis_config_used": getattr(self, "analysis_config_used", {}),
                "forecast_config_used": getattr(self, "forecast_config_used", {}),
                "eda_config_used": getattr(self, "eda_config_used", {}),
                "forecast_column": self.forecast_column,
                "last_warnings": list(self.last_warnings),
            },
            "annotations": copy.deepcopy(self.workflow_annotations),
            "ui_state": self._workflow_collect_ui_state(),
            "preflight_issues": copy.deepcopy(self.workflow_preflight_issues),
            "preflight_signature": list(self.workflow_preflight_signature or ()),
            "preflight_source_identity": self.workflow_preflight_source_identity,
            "current_run_id": self.workflow_current_run_id,
        }

    def workflow_save_project(self) -> None:
        if self.raw_frame is None:
            messagebox.showerror("没有可保存的数据", "请先载入数据。", parent=self.root)
            return
        path = self.workflow_current_project_path
        if path is None:
            self.workflow_save_project_as()
            return
        self._workflow_save_project_to(path)

    def workflow_save_project_as(self) -> None:
        if self.raw_frame is None:
            messagebox.showerror("没有可保存的数据", "请先载入数据。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="保存 PYTSA 项目",
            defaultextension=".pytsa.json",
            initialfile=(self.workflow_project_name_var.get().strip() or "PYTSA_project") + ".pytsa.json",
            filetypes=(("PYTSA 项目", "*.pytsa.json *.json *.zip"), ("所有文件", "*.*")),
        )
        if not path:
            return
        target = Path(path)
        if target.exists() and target.resolve() != (self.workflow_current_project_path or Path(".")).resolve():
            if not messagebox.askyesno(
                "目标文件已存在",
                "目标路径已有文件。工作台不会静默覆盖其他项目；是否尝试保存到该路径？",
                parent=self.root,
            ):
                return
        self._workflow_save_project_to(target)

    def _workflow_save_project_to(self, path: Path) -> None:
        try:
            state = self._workflow_build_project_state()
        except Exception as exc:
            messagebox.showerror("项目设置不完整", str(exc), parent=self.root)
            return

        def save(cancel_event, progress):
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "保存项目", "message": "正在写入项目快照。"})
            saved_path = self._workflow_get_store().save_project(state, path=path)
            self._workflow_get_store().add_recent_item(
                "project", saved_path, label=state.get("name") or Path(saved_path).stem
            )
            return str(saved_path)

        def saved(saved_path):
            self.workflow_current_project_path = Path(saved_path)
            self.workflow_project_path_label.configure(text=str(saved_path))
            self.workflow_project_status.configure(text="项目已保存；原始文件仍以项目内嵌副本恢复。")
            self.workflow_project_name_var.set(Path(saved_path).stem.removesuffix(".pytsa"))
            self._set_status(f"项目已保存：{saved_path}")

        self._workflow_start_task("保存项目", save, saved)

    def workflow_open_project(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="打开 PYTSA 项目",
            filetypes=(("PYTSA 项目", "*.pytsa.json *.json *.zip"), ("所有文件", "*.*")),
        )
        if path:
            self._workflow_load_project(Path(path))

    def _workflow_load_project(self, path: Path) -> None:
        def load(cancel_event, progress):
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "打开项目", "message": "正在读取并检查项目文件。"})
            project = self._workflow_get_store().load_project(path)
            frame = project.get("raw_frame")
            if frame is None:
                raise ValueError("项目文件不含可恢复的原始表格。")
            self._workflow_get_store().add_recent_item(
                "project", path, label=project.get("name") or path.stem
            )
            return project

        def loaded(project):
            self._workflow_apply_project(project, path)

        self._workflow_start_task("打开项目", load, loaded)

    def _workflow_apply_project(self, project: dict, path: Path) -> None:
        source = dict(project.get("source") or {})
        ui_state = dict(project.get("ui_state") or {})
        frame = project.get("raw_frame")
        if frame is None:
            raise ValueError("项目文件不含可恢复的原始表格。")
        saved_path = source.get("path")
        self.workflow_suppress_preflight = True
        try:
            self.raw_path = Path(saved_path) if saved_path else None
            self.raw_source_label = source.get("label") or source.get("path") or "PYTSA 项目內嵌数据"
            self._accept_dataframe(frame, self.raw_source_label)
        finally:
            self.workflow_suppress_preflight = False

        self.workflow_source_info = source or self.workflow_source_info
        self.workflow_current_project_path = path
        self.workflow_project_name_var.set(project.get("name") or path.stem)
        self.workflow_project_path_label.configure(text=str(path))
        self._workflow_restore_ui_state(ui_state)

        event_frame = project.get("event_frame")
        self.event_frame = event_frame
        event_path = source.get("event_path") or ui_state.get("event_path") or ""
        self.event_path = Path(event_path) if event_path else None
        if hasattr(self, "event_path_var"):
            self.event_path_var.set(event_path)

        results = dict(project.get("results") or {})
        params = dict(project.get("params") or {})
        self.prepared = results.get("prepared")
        self.analysis = results.get("analysis")
        self.forecast_result = results.get("forecast_result")
        self.eda_result = results.get("eda_result")
        self.prep_config_used = dict(results.get("prep_config_used") or params.get("prepare") or {})
        self.analysis_config_used = dict(results.get("analysis_config_used") or params.get("analysis") or {})
        self.forecast_config_used = dict(results.get("forecast_config_used") or params.get("forecast") or {})
        self.eda_config_used = dict(results.get("eda_config_used") or params.get("eda", {}).get("config") or {})
        self.forecast_column = results.get("forecast_column") or self.forecast_col_var.get()
        self.last_warnings = list(results.get("last_warnings") or [])
        self.workflow_annotations = copy.deepcopy(project.get("annotations") or [])
        self.workflow_current_run_id = str(project.get("current_run_id") or "")
        self.workflow_preflight_issues = copy.deepcopy(project.get("preflight_issues") or [])
        signature = project.get("preflight_signature") or []
        self.workflow_preflight_signature = tuple(signature) if signature else None
        self.workflow_preflight_source_identity = str(
            project.get("preflight_source_identity") or source.get("identity") or ""
        )
        self._workflow_render_restored_results()
        self._workflow_render_annotations()
        self._workflow_render_preflight()
        self._workflow_update_report_figure_choices()
        status = project.get("source_status", "unknown")
        if status == "changed":
            note = "源文件状态已变化；当前恢复使用项目内嵌数据，未自动重载。重新选入其他数据会清除旧结果与标注。"
        elif status == "missing":
            note = "原始文件当前不可用；项目内嵌数据和已保存结果已恢复。"
        elif status == "unchanged":
            note = "项目及内嵌数据已恢复；源文件没有自动重载。"
        else:
            note = "项目及内嵌数据已恢复；未能确认原始文件状态。"
        self.workflow_project_status.configure(text=note)
        self._set_status("项目已恢复；已恢复参数、结果和人工标注。", warning=status in {"changed", "missing"})

    def _workflow_restore_ui_state(self, state: dict) -> None:
        self._restore_additional_state(state.get("additional_options", {}))
        for name, value in state.items():
            if name in {"value_cols", "model_vars", "excel_sheets", "event_path", "project_name", "report_template", "report_template_raw"}:
                continue
            variable = getattr(self, name, None)
            if variable is not None and hasattr(variable, "set"):
                try:
                    variable.set(value)
                except (tk.TclError, TypeError):
                    pass
        sheets = list(state.get("excel_sheets") or [])
        self.excel_sheets = sheets
        self.sheet_combo.configure(values=sheets or ["（不适用）"])
        columns = [str(name) for name in self.raw_frame.columns]
        selected = set(state.get("value_cols") or [])
        self.value_list.selection_clear(0, tk.END)
        for index, column in enumerate(columns):
            if column in selected:
                self.value_list.selection_set(index)
        for name, value in (state.get("model_vars") or {}).items():
            if name in self.model_vars:
                self.model_vars[name].set(bool(value))
        template = state.get("report_template")
        if isinstance(template, dict):
            self._workflow_apply_report_template_to_controls(template)
        else:
            raw_report = state.get("report_template_raw")
            if isinstance(raw_report, dict):
                self.workflow_report_name_var.set(raw_report.get("name", "默认报告"))
                self.workflow_report_title_var.set(raw_report.get("title", "PYTSA 分析报告"))
                sections = raw_report.get("sections", {})
                formats = raw_report.get("formats", {})
                for key, variable in self.workflow_report_section_vars.items():
                    variable.set(bool(sections.get(key, False)))
                for key, variable in self.workflow_report_format_vars.items():
                    variable.set(bool(formats.get(key, False)))
                for key, variable in self.workflow_report_experiment_vars.items():
                    variable.set(str(raw_report.get("experiment", {}).get(key, "")))
                self.workflow_report_figure_titles = dict(raw_report.get("figure_titles", {}))
                figures = raw_report.get("figures", "all")
                self.workflow_report_all_figures_var.set(figures == "all")
                self._workflow_update_report_figure_choices()
                if figures != "all":
                    for index, key in enumerate(getattr(self, "workflow_report_figure_keys", [])):
                        if key in set(figures):
                            self.workflow_report_figure_list.selection_set(index)
                self._workflow_report_all_figures_changed()
        self._on_event_source_changed()
        self._on_eda_unit_changed()

    def _workflow_render_restored_results(self) -> None:
        if self.analysis is None:
            return
        analysis = self.analysis
        self._set_table("summary", analysis.get("tables", {}).get("summary"))
        self._set_table("band_power", analysis.get("tables", {}).get("band_power"))
        self._set_table("windows", analysis.get("tables", {}).get("windows"))
        self._set_table("tests", analysis.get("tables", {}).get("tests"))
        self._set_table("outliers", analysis.get("tables", {}).get("outliers"))
        self._populate_matrices(analysis.get("matrices", {}))
        self._draw_analysis_charts(self.prepared, analysis)
        self.analysis_summary.configure(text="已从项目恢复分析结果；结果对应项目内嵌数据。")
        if self.forecast_result:
            for key, source_key in (
                ("forecast_metrics", "metrics"), ("forecast_backtest", "backtest"),
                ("forecast_future", "future"), ("forecast_diagnostics", "diagnostics"),
                ("forecast_rolling_metrics", "rolling_metrics"),
                ("forecast_rolling_predictions", "rolling_predictions"),
            ):
                self._set_table(key, self.forecast_result.get(source_key))
            self._draw_forecast_chart(self.forecast_result)
            self.forecast_status.configure(text="已从项目恢复预测结果。")
        if self.eda_result:
            tables = self.eda_result.get("tables", {})
            for key in ("eda_windows", "eda_peaks", "eda_events", "eda_event_summary"):
                self._set_table(key, tables.get(key))
            self._draw_eda_chart(tables.get("eda_signals"))
            self.eda_status.configure(text="已从项目恢复皮电结果。")

    def workflow_refresh_parameter_templates(self) -> None:
        def load():
            return self._workflow_get_store().list_parameter_templates()

        def loaded(records):
            self.workflow_parameter_records = {str(row["id"]): row for row in records}
            self._workflow_clear_tree(self.workflow_parameter_tree)
            for record in records:
                self.workflow_parameter_tree.insert(
                    "", "end", iid=str(record["id"]),
                    values=(record.get("name", ""), record.get("kind", "combined"), record.get("updated_at", "")),
                )

        self._start_worker("读取参数模板", load, loaded)

    def _workflow_selected_parameter_template(self) -> dict | None:
        selection = self.workflow_parameter_tree.selection()
        if not selection:
            messagebox.showinfo("请选择模板", "请先在列表中选择一个参数模板。", parent=self.root)
            return None
        template_id = str(selection[0])
        record = self.workflow_parameter_records.get(template_id)
        if record is None:
            messagebox.showinfo("模板列表已变化", "请刷新模板列表后重试。", parent=self.root)
            return None
        return record

    def workflow_save_parameter_template(self) -> None:
        name = self.workflow_parameter_name_var.get().strip()
        if not name:
            messagebox.showerror("缺少模板名称", "请先填写模板名称。", parent=self.root)
            return
        kind = self.workflow_parameter_kind_var.get()
        try:
            if kind == "combined":
                parameters = self._workflow_collect_parameters()
            elif kind in {"prepare", "analysis"}:
                prepare, analysis = self._collect_prepare_config()
                parameters = prepare if kind == "prepare" else analysis
            elif kind == "forecast":
                parameters = self._workflow_collect_forecast_config()
            else:
                parameters = self._workflow_collect_eda_parameters()
        except Exception as exc:
            messagebox.showerror("当前设置不完整", str(exc), parent=self.root)
            return

        def inspect():
            records = self._workflow_get_store().list_parameter_templates(kind=kind)
            existing = next((item for item in records if item.get("name") == name), None)
            return existing

        def inspected(existing):
            overwrite = False
            if existing:
                overwrite = messagebox.askyesno(
                    "模板名称已存在",
                    f"已存在同名模板“{name}”。是否覆盖？",
                    parent=self.root,
                )
                if not overwrite:
                    return
            def save():
                return self._workflow_get_store().save_parameter_template(
                    name, parameters, kind=kind, overwrite=overwrite
                )
            def saved(record):
                self.workflow_parameter_name_var.set(record.get("name", name))
                self._set_status(f"参数模板已保存：{name}")
                self.workflow_refresh_parameter_templates()
            self._start_worker("保存参数模板", save, saved)

        self._start_worker("检查参数模板名称", inspect, inspected)

    def workflow_apply_parameter_template(self) -> None:
        record = self._workflow_selected_parameter_template()
        if record is None:
            return
        template_id = str(record["id"])

        def load():
            return self._workflow_get_store().get_parameter_template(template_id)

        def loaded(template):
            self._workflow_apply_parameter_record(template)

        self._start_worker("读取参数模板", load, loaded)

    def _workflow_template_column_paths(self, parameters: dict) -> list[tuple[tuple, str]]:
        references: list[tuple[tuple, str]] = []
        prepare = parameters.get("prepare", {})
        time_col = prepare.get("time_col")
        if isinstance(time_col, str) and time_col and time_col != "（无时间列）":
            references.append((("prepare", "time_col"), time_col))
        for index, column in enumerate(prepare.get("value_cols", [])):
            if isinstance(column, str):
                references.append((("prepare", "value_cols", index), column))
        forecast = parameters.get("forecast", {})
        if isinstance(forecast, dict) and isinstance(forecast.get("column"), str) and forecast.get("column"):
            references.append((("forecast", "column"), forecast["column"]))
        eda = parameters.get("eda", {})
        if isinstance(eda, dict) and isinstance(eda.get("column"), str) and eda.get("column"):
            references.append((("eda", "column"), eda["column"]))
        events = parameters.get("event_mapping", {})
        for key in ("time_col", "label_col", "marker_col"):
            value = events.get(key) if isinstance(events, dict) else None
            if isinstance(value, str) and value:
                references.append((("event_mapping", key), value))
        return references

    @staticmethod
    def _workflow_set_path(mapping: dict, path: tuple, value) -> None:
        cursor = mapping
        for part in path[:-1]:
            cursor = cursor[part]
        cursor[path[-1]] = value

    def _workflow_map_template_columns(self, parameters: dict) -> dict | None:
        if self.raw_frame is None:
            messagebox.showerror("需要当前数据", "请先载入目标数据，再应用带列映射的模板。", parent=self.root)
            return None
        references = self._workflow_template_column_paths(parameters)
        columns = [str(column) for column in self.raw_frame.columns]
        missing = [(path, old) for path, old in references if old not in columns]
        if not missing:
            return parameters
        window = tk.Toplevel(self.root)
        window.title("模板列映射")
        window.transient(self.root)
        window.grab_set()
        window.configure(bg=COLORS["canvas"])
        tk.Label(
            window,
            text="模板列在当前数据中不存在。请逐项明确选择对应列；留空会取消应用。",
            bg=COLORS["canvas"],
            fg=COLORS["ink"],
            anchor="w",
            wraplength=650,
            font=(self.font_family, 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=12)
        choices = {}
        for row, (path, old) in enumerate(missing, start=1):
            label = ".".join(str(part) for part in path[:-1]) + f" · {old}"
            tk.Label(window, text=label, bg=COLORS["white"], fg=COLORS["ink"], anchor="w").grid(
                row=row, column=0, sticky="ew", padx=10, pady=4
            )
            variable = tk.StringVar(value="")
            combo = self._combo(window, variable, ["", *columns], width=34)
            combo.configure(state="readonly")
            combo.grid(row=row, column=1, sticky="ew", padx=10, pady=4)
            choices[path] = variable
        window.grid_columnconfigure(1, weight=1)
        result = {"accepted": False, "parameters": None}
        buttons = tk.Frame(window, bg=COLORS["canvas"])
        buttons.grid(row=len(missing) + 1, column=0, columnspan=2, sticky="e", padx=10, pady=12)
        def accept():
            if any(not variable.get() for variable in choices.values()):
                messagebox.showerror("映射不完整", "请为每个缺失列选择对应列。", parent=window)
                return
            mapped = copy.deepcopy(parameters)
            for path, variable in choices.items():
                self._workflow_set_path(mapped, path, variable.get())
            result["accepted"] = True
            result["parameters"] = mapped
            window.destroy()
        tk.Button(buttons, text="应用映射", command=accept, bg=COLORS["green"], fg=COLORS["ink"]).pack(side="left", padx=4)
        tk.Button(buttons, text="取消", command=window.destroy, bg=COLORS["white"], fg=COLORS["ink"]).pack(side="left", padx=4)
        self.root.wait_window(window)
        return result["parameters"] if result["accepted"] else None

    def _workflow_apply_parameter_record(self, record: dict) -> None:
        kind = str(record.get("kind", "combined"))
        original = copy.deepcopy(record.get("parameters") or {})
        parameters = original if kind == "combined" else {kind: original}
        parameters = self._workflow_map_template_columns(parameters)
        if parameters is None:
            return
        prepare = parameters.get("prepare", {})
        current_fs = None
        try:
            current_fs = self._parse_float(self.fs_var, "采样率 fs")
        except Exception:
            pass
        saved_fs = prepare.get("fs") if isinstance(prepare, dict) else None
        if isinstance(saved_fs, (int, float)) and current_fs and not math.isclose(
            float(saved_fs), float(current_fs), rel_tol=1e-6, abs_tol=1e-9
        ):
            use_current = messagebox.askyesno(
                "采样率不同",
                f"模板采样率为 {saved_fs:g} Hz，当前设置为 {current_fs:g} Hz。是否按当前数据采样率适配？\n是：使用当前采样率；否：保留模板值。",
                parent=self.root,
            )
            if use_current:
                prepare["fs"] = float(current_fs)
        eda = parameters.get("eda", {})
        eda_config = eda.get("config", {}) if isinstance(eda, dict) else {}
        saved_unit = eda_config.get("unit") if isinstance(eda_config, dict) else None
        current_unit = self.eda_unit_var.get()
        if saved_unit and current_unit in {"uS", "mS", "nS", "a.u."} and saved_unit != current_unit:
            adapt_unit = messagebox.askyesno(
                "皮电单位不同",
                f"模板单位为 {saved_unit}，当前设置为 {current_unit}。是否按当前数据的单位设置适配？\n是：使用当前单位；否：保留模板单位。",
                parent=self.root,
            )
            if adapt_unit:
                eda_config["unit"] = current_unit
        check_parameters = copy.deepcopy(parameters)
        check_eda = check_parameters.get("eda")
        if isinstance(check_eda, dict):
            check_config = dict(check_eda.get("config", {}))
            check_config["column"] = check_eda.get("column", "")
            check_config["units"] = {"eda": check_config.get("unit", "")}
            check_parameters["eda"] = check_config
        template_for_check = {"kind": kind, "parameters": check_parameters}
        data_context = {
            "columns": [str(name) for name in self.raw_frame.columns] if self.raw_frame is not None else [],
            "time_col": prepare.get("time_col") if isinstance(prepare, dict) else None,
            "value_cols": prepare.get("value_cols", []) if isinstance(prepare, dict) else [],
            "sampling_rate": current_fs,
            "units": {"eda": current_unit},
        }
        try:
            compatibility = self._workflow_get_store().check_template_compatibility(
                template_for_check, data_context
            )
        except Exception as exc:
            messagebox.showerror("模板兼容性检查失败", str(exc), parent=self.root)
            return
        issues = compatibility.get("issues", [])
        if not compatibility.get("compatible", False):
            details = "\n".join(
                f"• {item.get('field', '设置')}：{item.get('message', '')}" for item in issues
            ) or "模板参数与当前数据不兼容。"
            messagebox.showerror(
                "模板尚不兼容",
                f"以下项目未适配，因此没有应用模板：\n{details}",
                parent=self.root,
            )
            return
        try:
            self._workflow_apply_parameters_to_controls(parameters)
        except Exception as exc:
            messagebox.showerror("应用模板失败", str(exc), parent=self.root)
            return
        self._set_status(f"已应用参数模板：{record.get('name', '')}")

    def _workflow_apply_parameters_to_controls(self, parameters: dict) -> None:
        self._apply_additional_parameters(parameters)
        prepare = parameters.get("prepare")
        if isinstance(prepare, dict):
            self.fs_var.set(str(prepare.get("fs", self.fs_var.get())))
            self.time_col_var.set(prepare.get("time_col") or "（无时间列）")
            self.time_kind_var.set({"auto": "自动识别", "numeric": "数值时间", "datetime": "日期时间"}.get(prepare.get("time_kind"), "自动识别"))
            self.missing_var.set({"reject": "拒绝缺失值", "ffill": "前向填充（仅探索）", "interpolate": "线性插值（仅探索）"}.get(prepare.get("missing"), self.missing_var.get()))
            self.duplicate_var.set({"reject": "重复时间：报错", "mean": "重复时间：取均值", "first": "重复时间：保留首条"}.get(prepare.get("duplicate"), self.duplicate_var.get()))
            self.resample_var.set("" if prepare.get("resample") is None else str(prepare.get("resample")))
            self.transform_var.set({"none": "不变换", "demean": "去均值", "detrend": "去线性趋势", "zscore": "z-score 标准化", "difference": "一阶差分"}.get(prepare.get("transform"), self.transform_var.get()))
            selected = set(prepare.get("value_cols") or [])
            self.value_list.selection_clear(0, tk.END)
            columns = [str(name) for name in self.raw_frame.columns] if self.raw_frame is not None else []
            for index, column in enumerate(columns):
                if column in selected:
                    self.value_list.selection_set(index)
        analysis = parameters.get("analysis")
        if isinstance(analysis, dict):
            for key, variable in (("max_lag", self.max_lag_var), ("period", self.period_var), ("lag", self.lag_var), ("band_low", self.band_low_var), ("band_high", self.band_high_var), ("nperseg", self.nperseg_var)):
                if key in analysis:
                    variable.set("" if analysis[key] is None else str(analysis[key]))
        forecast = parameters.get("forecast")
        if isinstance(forecast, dict):
            self.forecast_col_var.set(str(forecast.get("column", self.forecast_col_var.get())))
            self.horizon_var.set(str(forecast.get("horizon", self.horizon_var.get())))
            self.arima_order_var.set(",".join(str(value) for value in forecast.get("order", (1, 1, 1))))
            self.seasonal_order_var.set(",".join(str(value) for value in forecast.get("seasonal_order", (1, 0, 1, 12))))
            self.forecast_missing_var.set({"reject": "拒绝缺失值", "ffill": "因果前向填充"}.get(forecast.get("missing"), self.forecast_missing_var.get()))
            selected_models = set(forecast.get("models", []))
            for name, variable in self.model_vars.items():
                variable.set(name in selected_models)
        eda = parameters.get("eda")
        if isinstance(eda, dict):
            self.eda_column_var.set(str(eda.get("column", self.eda_column_var.get())))
            config = eda.get("config", eda)
            if isinstance(config, dict):
                for key, variable in (
                    ("unit", self.eda_unit_var), ("method", self.eda_method_var),
                    ("clean", self.eda_clean_var), ("cutoff", self.eda_cutoff_var),
                    ("relative_threshold", self.eda_relative_var), ("absolute_threshold", self.eda_absolute_var),
                    ("window_seconds", self.eda_window_var), ("start_seconds", self.eda_start_var),
                    ("end_seconds", self.eda_end_var), ("baseline_start", self.baseline_start_var),
                    ("baseline_end", self.baseline_end_var), ("response_start", self.response_start_var),
                    ("response_end", self.response_end_var),
                ):
                    if key in config:
                        variable.set("" if config[key] is None else str(config[key]))
        events = parameters.get("event_mapping")
        if isinstance(events, dict):
            self.event_source_var.set(events.get("source", self.event_source_var.get()))
            self.event_time_col_var.set(events.get("time_col", self.event_time_col_var.get()))
            self.event_label_col_var.set(events.get("label_col", self.event_label_col_var.get()))
            self.marker_col_var.set(events.get("marker_col", self.marker_col_var.get()))
        self._on_event_source_changed()
        self._on_eda_unit_changed()

    def workflow_copy_parameter_template(self) -> None:
        record = self._workflow_selected_parameter_template()
        if record is None:
            return
        name = simpledialog.askstring("复制参数模板", "新模板名称：", parent=self.root)
        if not name or not name.strip():
            return
        template_id = str(record["id"])
        def copy_template():
            return self._workflow_get_store().copy_parameter_template(template_id, name.strip())
        def copied(_record):
            self.workflow_refresh_parameter_templates()
            self._set_status(f"已复制参数模板：{name.strip()}")
        self._start_worker("复制参数模板", copy_template, copied)

    def workflow_import_parameter_template(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="导入参数模板", filetypes=(("JSON 模板", "*.json"), ("所有文件", "*.*"))
        )
        if not path:
            return
        def inspect():
            records = self._workflow_get_store().list_parameter_templates()
            return next((row for row in records if row.get("name") == Path(path).stem), None)
        def inspected(existing):
            overwrite = bool(existing) and messagebox.askyesno(
                "模板名称已存在", f"已存在同名模板“{existing.get('name')}”。是否覆盖？", parent=self.root
            )
            if existing and not overwrite:
                return
            def import_template():
                return self._workflow_get_store().import_parameter_template(path, overwrite=overwrite)
            def imported(_record):
                self.workflow_refresh_parameter_templates()
                self._set_status("参数模板已导入。")
            self._start_worker("导入参数模板", import_template, imported)
        self._start_worker("检查导入模板", inspect, inspected)

    def workflow_export_parameter_template(self) -> None:
        record = self._workflow_selected_parameter_template()
        if record is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出参数模板", defaultextension=".json",
            initialfile=f"{record.get('name', 'parameter_template')}.json",
            filetypes=(("JSON 模板", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        template_id = str(record["id"])
        def export_template():
            return str(self._workflow_get_store().export_parameter_template(template_id, path))
        def exported(saved_path):
            self._set_status(f"参数模板已导出：{saved_path}")
        self._start_worker("导出参数模板", export_template, exported)

    def workflow_delete_parameter_template(self) -> None:
        record = self._workflow_selected_parameter_template()
        if record is None:
            return
        if not messagebox.askyesno("删除参数模板", f"确定删除模板“{record.get('name')}”？", parent=self.root):
            return
        template_id = str(record["id"])
        def delete_template():
            self._workflow_get_store().delete_parameter_template(template_id)
        def deleted(_result):
            self.workflow_refresh_parameter_templates()
            self._set_status(f"参数模板已删除：{record.get('name')}")
        self._start_worker("删除参数模板", delete_template, deleted)

    def _workflow_preflight_signature_now(self) -> tuple:
        selected_time = self.time_col_var.get()
        time_col = None if selected_time == "（无时间列）" else selected_time
        values = tuple(self._value_columns()) if self.raw_frame is not None else ()
        try:
            fs = self._parse_float(self.fs_var, "采样率 fs")
        except Exception:
            fs = None
        return (self.workflow_source_info.get("identity", ""), time_col, values, fs,
                self.time_kind_var.get(), self.additional_vars["prepare"]["time_scale"].get(),
                self.additional_vars["prepare"]["timezone"].get())

    def _workflow_run_preflight(self, *, automatic: bool) -> None:
        if self.raw_frame is None:
            if not automatic:
                messagebox.showinfo("需要数据", "请先载入数据后再运行预检。", parent=self.root)
            return
        signature = self._workflow_preflight_signature_now()
        if automatic and signature == self.workflow_preflight_signature and self.workflow_preflight_source_identity:
            return
        frame = self.raw_frame
        time_col, value_cols, sampling_rate = signature[1], list(signature[2]), signature[3]
        time_kind = {"自动识别": "auto", "数值秒": "numeric", "数值时间": "numeric", "日期时间": "datetime"}.get(
            self.time_kind_var.get(), "auto"
        )
        def preflight(cancel_event, progress):
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "数据预检", "message": "检查缺失、时间间隔与异常范围；不会修改数据。"})
            return self._workflow_get_store().preflight_dataframe(
                frame,
                time_col=time_col,
                value_cols=value_cols or None,
                sampling_rate=sampling_rate,
                time_kind=time_kind,
                time_scale=signature[5],
                timezone=signature[6],
            )
        def complete(issues):
            self.workflow_preflight_issues = list(issues or [])
            self.workflow_preflight_signature = signature
            self.workflow_preflight_source_identity = signature[0]
            self._workflow_render_preflight()
            self.workflow_preflight_note_var.set(
                f"预检完成：发现 {len(self.workflow_preflight_issues)} 条提示；数据未被修改。"
            )
        self._workflow_start_task("数据预检", preflight, complete)

    def workflow_run_preflight(self) -> None:
        self._workflow_run_preflight(automatic=False)

    def _workflow_render_preflight(self) -> None:
        tree = getattr(self, "workflow_preflight_tree", None)
        if tree is None:
            return
        self._workflow_clear_tree(tree)
        if not self.workflow_preflight_issues:
            if self.workflow_preflight_signature is not None:
                tree.insert("", "end", values=("正常", "未发现问题", "", 0, "", "", "当前边界预检未发现提示。"))
            return
        for index, issue in enumerate(self.workflow_preflight_issues):
            rows = issue.get("row_indices") or []
            row_text = ", ".join(str(value) for value in rows)
            if issue.get("row_range"):
                row_text += f"（{issue['row_range'].get('start')}–{issue['row_range'].get('end')}）"
            times = issue.get("time_range")
            time_text = "" if times is None else str(times)
            tree.insert(
                "", "end", iid=f"preflight-{index}",
                values=(issue.get("severity", "warning"), issue.get("check", ""), issue.get("column", ""), issue.get("count", ""), row_text, time_text, issue.get("message", "")),
            )

    def _workflow_remember_data_in_worker(self, path: str | Path, label: str) -> None:
        try:
            self._workflow_get_store().add_recent_item("data", path, label=label)
        except Exception:
            pass

    def workflow_refresh_recent(self) -> None:
        kind_map = {"项目": "project", "数据": "data", "全部": None}
        kind = kind_map.get(self.workflow_recent_kind_var.get())
        def load():
            return self._workflow_get_store().list_recent_items(kind=kind, limit=20)
        def loaded(records):
            self.workflow_recent_records = list(records)
            self._workflow_clear_tree(self.workflow_recent_tree)
            for index, record in enumerate(records):
                self.workflow_recent_tree.insert(
                    "", "end", iid=f"recent-{index}",
                    values=(record.get("kind", ""), record.get("label", ""), record.get("path", ""), record.get("last_used", ""), "是" if record.get("exists") else "否"),
                )
        self._start_worker("读取最近使用", load, loaded)

    def _workflow_selected_recent(self) -> dict | None:
        selection = self.workflow_recent_tree.selection()
        if not selection:
            messagebox.showinfo("请选择记录", "请先选择一个最近使用记录。", parent=self.root)
            return None
        try:
            index = int(str(selection[0]).split("-", 1)[1])
            return self.workflow_recent_records[index]
        except (ValueError, IndexError):
            return None

    def workflow_open_recent(self) -> None:
        record = self._workflow_selected_recent()
        if record is None:
            return
        path = Path(record.get("path", ""))
        if not path.exists():
            messagebox.showerror("文件不可用", f"找不到最近使用的文件：\n{path}\n\n请重新选择源文件，或移除这条记录。", parent=self.root)
            return
        if record.get("kind") == "project":
            self._workflow_load_project(path)
        else:
            self.raw_path = path
            self.source_var.set(str(path))
            self.reload_selected_source()

    def workflow_remove_recent(self) -> None:
        record = self._workflow_selected_recent()
        if record is None:
            return
        def remove():
            self._workflow_get_store().remove_recent_item(record.get("kind"), record.get("path"))
        def removed(_result):
            self.workflow_refresh_recent()
            self._set_status("最近使用记录已移除；原文件未删除。")
        self._start_worker("移除最近记录", remove, removed)

    def _workflow_update_report_figure_choices(self) -> None:
        keys = ["time_series"]
        if self.analysis:
            keys.extend(f"matrix_{key}" for key in self.analysis.get("matrices", {}))
            psd = self.analysis.get("tables", {}).get("psd")
            if psd is not None and not getattr(psd, "empty", True):
                keys.append("welch_psd")
        if self.forecast_result:
            keys.append("forecast")
        if self.eda_result:
            keys.append("eda_components")
        keys = list(dict.fromkeys(keys))
        labels = {
            "time_series": "时间序列",
            "welch_psd": "Welch 功率谱",
            "forecast": "预测",
            "eda_components": "皮电分量",
        }
        self.workflow_report_figure_keys = keys
        if not hasattr(self, "workflow_report_figure_list"):
            return
        selected = set(self._workflow_selected_report_figures())
        self.workflow_report_figure_list.delete(0, tk.END)
        for key in keys:
            label = labels.get(key, key)
            self.workflow_report_figure_list.insert(tk.END, f"{label}  [{key}]")
        for index, key in enumerate(keys):
            if key in selected:
                self.workflow_report_figure_list.selection_set(index)
        self.workflow_report_figure_key_var.set(keys[0] if keys else "")
        self.workflow_report_figure_key_combo.configure(values=keys or [""])

    def _workflow_selected_report_figures(self) -> list[str]:
        if not hasattr(self, "workflow_report_figure_list"):
            return []
        keys = getattr(self, "workflow_report_figure_keys", [])
        return [keys[index] for index in self.workflow_report_figure_list.curselection() if index < len(keys)]

    def _workflow_report_all_figures_changed(self) -> None:
        if not hasattr(self, "workflow_report_figure_list"):
            return
        state = tk.DISABLED if self.workflow_report_all_figures_var.get() else tk.NORMAL
        self.workflow_report_figure_list.configure(state=state)

    def _workflow_collect_report_template(self) -> dict:
        module = self._workflow_get_report_module()
        template = copy.deepcopy(module.DEFAULT_REPORT_TEMPLATE)
        template["name"] = self.workflow_report_name_var.get().strip() or "默认报告"
        template["title"] = self.workflow_report_title_var.get().strip() or "PYTSA 分析报告"
        template["experiment"] = {
            key: variable.get().strip()
            for key, variable in self.workflow_report_experiment_vars.items()
            if variable.get().strip()
        }
        template["sections"] = [key for key, variable in self.workflow_report_section_vars.items() if variable.get()]
        template["formats"] = [key for key, variable in self.workflow_report_format_vars.items() if variable.get()]
        template["figures"] = "all" if self.workflow_report_all_figures_var.get() else self._workflow_selected_report_figures()
        template["figure_titles"] = dict(self.workflow_report_figure_titles)
        return module.normalize_report_template(template)

    def _workflow_apply_report_template_to_controls(self, template: dict) -> None:
        canonical = self._workflow_get_report_module().normalize_report_template(template)
        self.workflow_report_template = canonical
        self.workflow_report_name_var.set(canonical["name"])
        self.workflow_report_title_var.set(canonical["title"])
        for key, variable in self.workflow_report_experiment_vars.items():
            variable.set(str(canonical.get("experiment", {}).get(key, "")))
        sections = set(canonical["sections"])
        for key, variable in self.workflow_report_section_vars.items():
            variable.set(key in sections)
        formats = set(canonical["formats"])
        for key, variable in self.workflow_report_format_vars.items():
            variable.set(key in formats)
        self.workflow_report_figure_titles = dict(canonical.get("figure_titles", {}))
        figures = canonical.get("figures", "all")
        is_all = figures == "all"
        self.workflow_report_all_figures_var.set(is_all)
        self._workflow_update_report_figure_choices()
        if not is_all:
            wanted = set(figures)
            for index, key in enumerate(getattr(self, "workflow_report_figure_keys", [])):
                if key in wanted:
                    self.workflow_report_figure_list.selection_set(index)
        self._workflow_report_all_figures_changed()

    def workflow_set_report_figure_title(self) -> None:
        key = self.workflow_report_figure_key_var.get().strip()
        title = self.workflow_report_figure_title_var.get().strip()
        if not key or not title:
            messagebox.showerror("图表标题不完整", "请选择图表并填写标题。", parent=self.root)
            return
        self.workflow_report_figure_titles[key] = title
        self._set_status(f"已设置图表标题：{key} → {title}")

    def workflow_refresh_report_templates(self) -> None:
        def load():
            return self._workflow_get_store().list_report_templates()
        def loaded(records):
            self.workflow_report_templates_by_name = {
                str(record.get("name", "")): record for record in records
            }
            names = ["默认报告", *self.workflow_report_templates_by_name.keys()]
            names = list(dict.fromkeys(names))
            self.workflow_report_template_combo.configure(values=names)
        self._start_worker("读取报告模板", load, loaded)

    def workflow_apply_report_template(self) -> None:
        name = self.workflow_report_name_var.get().strip()
        if not name:
            messagebox.showerror("请选择报告模板", "请先选择或填写报告模板名称。", parent=self.root)
            return
        if name == "默认报告":
            self._workflow_apply_report_template_to_controls(None)
            return
        def load():
            return self._workflow_get_store().get_report_template(name)
        def loaded(template):
            self._workflow_apply_report_template_to_controls(template)
            self._set_status(f"已加载报告模板：{name}")
        self._start_worker("读取报告模板", load, loaded)

    def workflow_save_report_template(self) -> None:
        try:
            template = self._workflow_collect_report_template()
        except Exception as exc:
            messagebox.showerror("报告模板设置有误", str(exc), parent=self.root)
            return
        name = template["name"]
        def inspect():
            records = self._workflow_get_store().list_report_templates()
            return next((record for record in records if record.get("name") == name), None)
        def inspected(existing):
            overwrite = False
            if existing:
                overwrite = messagebox.askyesno(
                    "报告模板名称已存在", f"已存在同名模板“{name}”。是否覆盖？", parent=self.root
                )
                if not overwrite:
                    return
            def save():
                return self._workflow_get_store().save_report_template(template, overwrite=overwrite)
            def saved(record):
                self._workflow_apply_report_template_to_controls(record)
                self.workflow_refresh_report_templates()
                self._set_status(f"报告模板已保存：{name}")
            self._start_worker("保存报告模板", save, saved)
        self._start_worker("检查报告模板名称", inspect, inspected)

    def workflow_copy_report_template(self) -> None:
        name = self.workflow_report_name_var.get().strip()
        if not name or name == "默认报告":
            messagebox.showinfo("请选择模板", "请先加载一个已保存的报告模板再复制。", parent=self.root)
            return
        new_name = simpledialog.askstring("复制报告模板", "新模板名称：", parent=self.root)
        if not new_name or not new_name.strip():
            return
        def duplicate():
            return self._workflow_get_store().copy_report_template(name, new_name.strip())
        def copied(_template):
            self.workflow_refresh_report_templates()
            self.workflow_report_name_var.set(new_name.strip())
            self._set_status(f"报告模板已复制：{new_name.strip()}")
        self._start_worker("复制报告模板", duplicate, copied)

    def workflow_import_report_template(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="导入报告模板", filetypes=(("JSON 模板", "*.json"), ("所有文件", "*.*"))
        )
        if not path:
            return
        def inspect():
            records = self._workflow_get_store().list_report_templates()
            return next((row for row in records if row.get("name") == Path(path).stem), None)
        def inspected(existing):
            overwrite = bool(existing) and messagebox.askyesno(
                "报告模板名称已存在", f"已存在同名模板“{existing.get('name')}”。是否覆盖？", parent=self.root
            )
            if existing and not overwrite:
                return
            def import_template():
                return self._workflow_get_store().import_report_template(path, overwrite=overwrite)
            def imported(template):
                self._workflow_apply_report_template_to_controls(template)
                self.workflow_refresh_report_templates()
                self._set_status("报告模板已导入。")
            self._start_worker("导入报告模板", import_template, imported)
        self._start_worker("检查导入报告模板", inspect, inspected)

    def workflow_export_report_template(self) -> None:
        name = self.workflow_report_name_var.get().strip()
        if not name or name == "默认报告":
            messagebox.showinfo("请选择模板", "默认报告不是已保存模板，请先保存当前设置。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出报告模板", defaultextension=".json",
            initialfile=f"{name}.json", filetypes=(("JSON 模板", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        def export_template():
            return str(self._workflow_get_store().export_report_template(name, path))
        def exported(saved_path):
            self._set_status(f"报告模板已导出：{saved_path}")
        self._start_worker("导出报告模板", export_template, exported)

    def workflow_delete_report_template(self) -> None:
        name = self.workflow_report_name_var.get().strip()
        if not name or name == "默认报告":
            messagebox.showinfo("请选择模板", "默认报告不能删除。", parent=self.root)
            return
        if not messagebox.askyesno("删除报告模板", f"确定删除报告模板“{name}”？", parent=self.root):
            return
        def delete_template():
            self._workflow_get_store().delete_report_template(name)
        def deleted(_result):
            self.workflow_report_name_var.set("默认报告")
            self._workflow_apply_report_template_to_controls(None)
            self.workflow_refresh_report_templates()
            self._set_status(f"报告模板已删除：{name}")
        self._start_worker("删除报告模板", delete_template, deleted)

    def _workflow_on_span_select(self, key: str, first, second) -> None:
        metadata = self._workflow_chart_time_meta.get(key, {})
        try:
            if metadata.get("kind") == "relative_seconds":
                start, end = sorted((float(first), float(second)))
            elif metadata.get("kind") == "datetime":
                from matplotlib.dates import num2date
                origin = metadata.get("origin")
                origin = origin.to_pydatetime() if hasattr(origin, "to_pydatetime") else origin
                a = num2date(float(first), tz=timezone.utc)
                b = num2date(float(second), tz=timezone.utc)
                start, end = sorted(((a - origin).total_seconds(), (b - origin).total_seconds()))
            else:
                origin = float(metadata.get("origin", 0.0))
                start, end = sorted((float(first) - origin, float(second) - origin))
        except Exception as exc:
            self.workflow_range_var.set(f"无法转换所选区间：{exc}")
            return
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            self.workflow_range_var.set("请拖动选择一个有效的时间区间。")
            return
        interval = {
            "start_seconds": float(start),
            "end_seconds": float(end),
            "chart_key": key,
            "source_identity": self.workflow_source_info.get("identity", ""),
            "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
        }
        self.workflow_selected_interval = interval
        self.workflow_range_var.set(
            f"所选区间：{start:.3f}–{end:.3f} 秒（从记录首样本计）；标注不会自动排除样本。"
        )
        self.workflow_eda_range_note_var.set(
            f"选区：{start:.3f}–{end:.3f} 秒。仅点击按钮后用于皮电窗口/事件统计；清理与分解仍处理完整连续信号。"
        )

    def _workflow_attach_chart_interactions(self, key: str, canvas, axes) -> None:
        axis_list = list(axes) if isinstance(axes, (list, tuple)) else [axes]
        if hasattr(axes, "flat"):
            axis_list = list(axes.flat)
        canvas_key = key
        readout = self._workflow_chart_readouts.get(key)
        if readout is None:
            readout = tk.StringVar(value="移动指针查看坐标。")
            self._workflow_chart_readouts[key] = readout
        canvas.mpl_connect(
            "motion_notify_event",
            lambda event, key=canvas_key: self._workflow_motion_readout(key, event),
        )
        if key not in {"series", "eda"} or not axis_list:
            return
        try:
            from matplotlib.widgets import SpanSelector
            selector_axis = axis_list[-1] if key == "eda" else axis_list[0]
            selector = SpanSelector(
                selector_axis,
                lambda a, b, key=key: self._workflow_on_span_select(key, a, b),
                "horizontal",
                useblit=True,
                props={"alpha": 0.25, "facecolor": COLORS["green"]},
                interactive=True,
                drag_from_anywhere=True,
            )
            self._workflow_chart_selectors[key] = selector
        except (ImportError, TypeError, AttributeError):
            self._workflow_chart_selectors.pop(key, None)

    def _workflow_motion_readout(self, key: str, event) -> None:
        variable = self._workflow_chart_readouts.get(key)
        if variable is None:
            return
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            variable.set("移动指针查看坐标。")
            return
        variable.set(f"x={self._format_value(event.xdata)}；y={self._format_value(event.ydata)}")

    def _workflow_apply_interval_to_eda(self) -> None:
        interval = self.workflow_selected_interval
        if interval is None:
            messagebox.showinfo("没有选区", "请先在时域图上拖动选择一个区间。", parent=self.root)
            return
        if interval.get("source_identity") != self.workflow_source_info.get("identity", ""):
            messagebox.showerror("选区已过期", "数据来源已改变，请在当前数据图上重新选择区间。", parent=self.root)
            return
        start, end = float(interval["start_seconds"]), float(interval["end_seconds"])
        self.eda_start_var.set(f"{start:.6g}")
        self.eda_end_var.set(f"{end:.6g}")
        self.workflow_eda_range_note_var.set(
            f"皮电统计区间已设为 {start:.3f}–{end:.3f} 秒；清理与分解仍使用完整连续信号。"
        )
        self._set_status("已将选区用于皮电窗口与事件统计区间；信号清理和分解范围未改变。")

    def workflow_add_annotation(self) -> None:
        interval = self.workflow_selected_interval
        if interval is None:
            messagebox.showinfo("没有选区", "请先在时域图上拖动选择一个区间。", parent=self.root)
            return
        if interval.get("source_identity") != self.workflow_source_info.get("identity", ""):
            messagebox.showerror("选区已过期", "数据来源已改变，请重新选择区间。", parent=self.root)
            return
        label = self.workflow_annotation_label_var.get().strip()
        if not label:
            messagebox.showerror("缺少标注名称", "请填写标注名称。", parent=self.root)
            return
        annotation = {
            "id": uuid.uuid4().hex,
            "label": label,
            "note": self.workflow_annotation_note_var.get().strip(),
            "start_seconds": float(interval["start_seconds"]),
            "end_seconds": float(interval["end_seconds"]),
            "chart_key": interval.get("chart_key", "series"),
            "source_identity": interval.get("source_identity", ""),
            "time_basis": copy.deepcopy(interval.get("time_basis")),
            "created_at": self._workflow_now(),
            "automatic_exclusion": False,
        }
        self.workflow_annotations.append(annotation)
        self.workflow_annotation_label_var.set("")
        self.workflow_annotation_note_var.set("")
        self._workflow_render_annotations()
        self._set_status(f"已添加人工标注：{label}；未修改分析样本。")

    def _workflow_selected_annotation(self) -> dict | None:
        selection = self.workflow_annotation_tree.selection()
        if not selection:
            messagebox.showinfo("请选择标注", "请先在列表中选择一个标注。", parent=self.root)
            return None
        annotation_id = str(selection[0])
        return next((item for item in self.workflow_annotations if item.get("id") == annotation_id), None)

    def workflow_edit_annotation(self) -> None:
        annotation = self._workflow_selected_annotation()
        if annotation is None:
            return
        label = simpledialog.askstring("编辑标注名称", "标注名称：", initialvalue=annotation.get("label", ""), parent=self.root)
        if label is None:
            return
        note = simpledialog.askstring("编辑标注备注", "备注（可留空）：", initialvalue=annotation.get("note", ""), parent=self.root)
        if note is None:
            return
        annotation["label"] = label.strip() or "未命名标注"
        annotation["note"] = note.strip()
        annotation["updated_at"] = self._workflow_now()
        self._workflow_render_annotations()
        self._set_status("人工标注已更新；分析结果未改变。")

    def workflow_delete_annotation(self) -> None:
        annotation = self._workflow_selected_annotation()
        if annotation is None:
            return
        if not messagebox.askyesno("删除人工标注", f"确定删除标注“{annotation.get('label')}”？", parent=self.root):
            return
        self.workflow_annotations = [item for item in self.workflow_annotations if item.get("id") != annotation.get("id")]
        self._workflow_render_annotations()
        self._set_status("人工标注已删除；分析结果未改变。")

    def _workflow_render_annotations(self) -> None:
        tree = getattr(self, "workflow_annotation_tree", None)
        if tree is None:
            return
        self._workflow_clear_tree(tree)
        for annotation in self.workflow_annotations:
            tree.insert(
                "", "end", iid=str(annotation.get("id", uuid.uuid4().hex)),
                values=(
                    annotation.get("label", ""),
                    f"{float(annotation.get('start_seconds', 0)):.3f}",
                    f"{float(annotation.get('end_seconds', 0)):.3f}",
                    annotation.get("chart_key", ""),
                    annotation.get("note", ""),
                ),
            )

    def workflow_refresh_history(self) -> None:
        def load():
            return self._workflow_get_store().list_run_history(limit=100)
        def loaded(records):
            self.workflow_history_records = {str(row.get("run_id")): row for row in records}
            self._workflow_clear_tree(self.workflow_history_tree)
            for row in records:
                metrics = row.get("metrics", {})
                if isinstance(metrics, dict):
                    overview = "; ".join(
                        f"{name}={value.get('value', '') if isinstance(value, dict) else value}"
                        for name, value in list(metrics.items())[:3]
                    )
                else:
                    overview = str(metrics)
                source = row.get("source", {})
                source_text = source.get("path", "") if isinstance(source, dict) else str(source)
                run_id = str(row.get("run_id", ""))
                if run_id:
                    self.workflow_history_tree.insert(
                        "", "end", iid=run_id,
                        values=(run_id, row.get("kind", ""), row.get("saved_at", ""), source_text, overview),
                    )
        self._start_worker("读取运行历史", load, loaded)

    def workflow_compare_selected_runs(self) -> None:
        selections = self.workflow_history_tree.selection()
        if len(selections) != 2:
            messagebox.showinfo("选择两次运行", "请按住 Ctrl 选择两条历史记录，再点击比较。", parent=self.root)
            return
        left_id, right_id = map(str, selections)
        def compare(cancel_event, progress):
            self._workflow_check_cancel(cancel_event)
            progress({"stage": "历史比较", "message": "正在载入两次运行并校验曲线单位。"})
            return self._workflow_get_store().compare_runs(left_id, right_id)
        def compared(result):
            self.workflow_comparison_result = result
            self._workflow_render_history_comparison(result)
            self._set_status(f"历史比较完成：{left_id[:10]} ↔ {right_id[:10]}")
        self._workflow_start_task("比较运行历史", compare, compared)

    def _workflow_render_history_comparison(self, result: dict) -> None:
        tree = self.workflow_history_details
        self._workflow_clear_tree(tree)
        for item in result.get("parameters", []):
            tree.insert(
                "", "end", values=(
                    "参数", item.get("path", ""), self._format_value(item.get("left")),
                    self._format_value(item.get("right")), "", "", "已变化" if item.get("changed") else "未变化",
                ),
            )
        for item in result.get("metrics", []):
            tree.insert(
                "", "end", values=(
                    "指标", item.get("name", ""), self._format_value(item.get("left_value")),
                    self._format_value(item.get("right_value")), self._format_value(item.get("delta")),
                    item.get("unit") or "", "可比" if item.get("comparable") else item.get("reason") or "不可比",
                ),
            )
        for item in result.get("curves", []):
            tree.insert(
                "", "end", values=(
                    "曲线", item.get("key", ""), "", "", "", "",
                    "可比" if item.get("compatible") else item.get("reason") or "不可比",
                ),
            )
        try:
            from matplotlib.figure import Figure
        except Exception as exc:
            self._set_chart_message(self.workflow_comparison_chart_host, f"无法载入图形组件：{exc}")
            return
        fig = Figure(figsize=(8.5, 3.0), dpi=100, facecolor=COLORS["white"])
        ax = fig.add_subplot(111)
        plotted = 0
        colors = (COLORS["ink"], COLORS["coral"], COLORS["green"], "#6C5CE7")
        for item in result.get("curves", []):
            if not item.get("compatible"):
                continue
            for side, color in (("left", colors[0]), ("right", colors[1])):
                curve = item.get(side)
                if not isinstance(curve, dict):
                    continue
                x, y = curve.get("x", []), curve.get("y", [])
                if len(x) != len(y) or not x:
                    continue
                label = f"{curve.get('label', item.get('key', '曲线'))} · {side}"
                ax.plot(x, y, label=label, color=color, linewidth=1.0)
                plotted += 1
        if plotted:
            ax.set_xlabel("记录起点后的秒")
            ax.set_ylabel("同一记录单位")
            ax.set_title("兼容曲线比较（原始采样点，不插值）")
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
            fig.tight_layout()
            self._render_figure(
                self.workflow_comparison_chart_host,
                fig,
                "workflow-comparison",
                axes=ax,
            )
        else:
            reasons = result.get("incompatibilities", [])
            text = "没有可叠加的兼容曲线。"
            if reasons:
                text += "\n" + "；".join(str(reason) for reason in reasons[:4])
            self._set_chart_message(self.workflow_comparison_chart_host, text)

    def workflow_refresh_logs(self) -> None:
        def load():
            return self._workflow_get_store().read_run_logs(None, limit=500)
        def loaded(records):
            self.workflow_log_records = list(records)
            self._workflow_clear_tree(self.workflow_log_tree)
            for index, record in enumerate(records):
                self.workflow_log_tree.insert(
                    "", "end", iid=f"log-{index}",
                    values=(record.get("timestamp", ""), record.get("run_id", ""), record.get("level", ""), record.get("message", "")),
                )
            self._workflow_set_text(
                self.workflow_log_detail,
                f"已载入 {len(records)} 条日志。选择一条记录查看详情。",
            )
        self._start_worker("读取持久日志", load, loaded)

    def _workflow_log_selected(self, _event=None) -> None:
        selection = self.workflow_log_tree.selection()
        if not selection:
            return
        try:
            index = int(str(selection[0]).split("-", 1)[1])
            record = self.workflow_log_records[index]
        except (ValueError, IndexError):
            return
        self._workflow_set_text(self.workflow_log_detail, json.dumps(record, ensure_ascii=False, indent=2, default=str))

    @staticmethod
    def _workflow_set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def workflow_copy_logs(self) -> None:
        def load():
            return self._workflow_get_store().format_run_logs(None, limit=500)
        def copied(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("持久日志已复制到剪贴板。")
        self._start_worker("读取日志文本", load, copied)

    def _workflow_batch_policy_changed(self, _event=None) -> None:
        policy = self.workflow_batch_event_policy_var.get()
        shared_enabled = policy == "所有文件共用事件表"
        mapping_enabled = policy == "逐文件映射事件表"
        raw_marker = bool(self.workflow_batch_marker_col_var.get().strip())
        for name, enabled in (
            ("workflow_batch_event_time_widget", shared_enabled or mapping_enabled),
            ("workflow_batch_event_label_widget", shared_enabled or mapping_enabled),
            ("workflow_batch_shared_event_widget", shared_enabled),
            ("workflow_batch_shared_event_button", shared_enabled),
            ("workflow_batch_map_event_button", mapping_enabled),
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                try:
                    widget.configure(state=tk.NORMAL if enabled else tk.DISABLED)
                except tk.TclError:
                    pass

    def workflow_batch_add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="选择批量分析文件",
            filetypes=(("支持的文件", "*.csv *.tsv *.txt *.xlsx"), ("所有文件", "*.*")),
        )
        if not paths:
            return
        seen = {self._workflow_normalize_path(item) for item in self.workflow_batch_files}
        for path in paths:
            normalized = self._workflow_normalize_path(path)
            if normalized not in seen:
                self.workflow_batch_files.append(str(Path(path).resolve()))
                seen.add(normalized)
        self._workflow_render_batch_files()

    def workflow_batch_remove_selected(self) -> None:
        selected = {str(self.workflow_batch_tree.item(item, "values")[0]) for item in self.workflow_batch_tree.selection()}
        if not selected:
            messagebox.showinfo("请选择文件", "请先选择要移除的批量文件。", parent=self.root)
            return
        self.workflow_batch_files = [path for path in self.workflow_batch_files if path not in selected]
        for path in selected:
            self.workflow_batch_event_map.pop(self._workflow_normalize_path(path), None)
        self._workflow_render_batch_files()

    def workflow_batch_map_event(self) -> None:
        selected = self.workflow_batch_tree.selection()
        if len(selected) != 1:
            messagebox.showinfo("选择一个文件", "请先选择一个输入文件，再指定对应事件表。", parent=self.root)
            return
        input_path = str(self.workflow_batch_tree.item(selected[0], "values")[0])
        event_path = filedialog.askopenfilename(
            parent=self.root,
            title=f"为 {Path(input_path).name} 指定事件表",
            filetypes=(("支持的文件", "*.csv *.tsv *.txt *.xlsx"), ("所有文件", "*.*")),
        )
        if not event_path:
            return
        self.workflow_batch_event_map[self._workflow_normalize_path(input_path)] = str(Path(event_path).resolve())
        self._workflow_render_batch_files()

    def workflow_batch_choose_shared_event(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择批次共用事件表",
            filetypes=(("支持的文件", "*.csv *.tsv *.txt *.xlsx"), ("所有文件", "*.*")),
        )
        if path:
            self.workflow_batch_shared_event_path_var.set(str(Path(path).resolve()))

    def workflow_batch_choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择批量结果文件夹")
        if path:
            self.workflow_batch_output_var.set(path)

    def _workflow_render_batch_files(self) -> None:
        tree = getattr(self, "workflow_batch_tree", None)
        if tree is None:
            return
        self._workflow_clear_tree(tree)
        policy = self.workflow_batch_event_policy_var.get()
        for index, path in enumerate(self.workflow_batch_files):
            if policy == "所有文件共用事件表":
                event_path = self.workflow_batch_shared_event_path_var.get()
            elif policy == "逐文件映射事件表":
                event_path = self.workflow_batch_event_map.get(self._workflow_normalize_path(path), "")
            else:
                event_path = ""
            tree.insert("", "end", iid=f"batch-{index}", values=(path, event_path))

    def workflow_batch_start(self) -> None:
        if not self.workflow_batch_files:
            messagebox.showerror("没有批量文件", "请先添加一个或多个数据文件。", parent=self.root)
            return
        try:
            prep, analysis = self._collect_prepare_config()
            report_template = self._workflow_collect_report_template()
        except Exception as exc:
            messagebox.showerror("批量设置不完整", str(exc), parent=self.root)
            return
        parameters = {"prepare": prep, "analysis": analysis, "forecast": {}, "eda": {}}
        if self.workflow_batch_forecast_var.get():
            try:
                parameters["forecast"] = self._workflow_collect_forecast_config()
            except Exception as exc:
                messagebox.showerror("批量预测设置有误", str(exc), parent=self.root)
                return
        if self.workflow_batch_eda_var.get():
            try:
                parameters["eda"] = self._workflow_collect_eda_parameters()
            except Exception as exc:
                messagebox.showerror("批量皮电设置有误", str(exc), parent=self.root)
                return
        try:
            event_policy = self._workflow_batch_event_policy()
        except Exception as exc:
            messagebox.showerror("批量事件映射有误", str(exc), parent=self.root)
            return
        parameters["event_mapping"] = {
            "source": "event_file" if event_policy["mode"] != "none" else ("raw_marker" if self.workflow_batch_marker_col_var.get().strip() else "none"),
            "time_col": self.workflow_batch_event_time_col_var.get().strip(),
            "label_col": self.workflow_batch_event_label_col_var.get().strip(),
            "marker_col": self.workflow_batch_marker_col_var.get().strip(),
            "time_kind": parameters.get("prepare", {}).get("time_kind", "auto"),
        }
        parameters["batch_options"] = {
            "forecast_enabled": bool(self.workflow_batch_forecast_var.get()),
            "eda_enabled": bool(self.workflow_batch_eda_var.get()),
            "report_template": report_template,
        }
        output_text = self.workflow_batch_output_var.get().strip()
        output_dir = Path(output_text).expanduser() if output_text else None
        if bool(self.workflow_batch_eda_var.get()):
            eda = parameters.get("eda", {})
            if not eda.get("column") or eda.get("config", {}).get("unit") not in {"uS", "mS", "nS", "a.u."}:
                messagebox.showerror("皮电设置不完整", "批量皮电分析需要明确选择皮电通道和输入单位。", parent=self.root)
                return
        files = list(self.workflow_batch_files)

        def run(cancel_event, progress):
            return self._workflow_get_store().run_batch(
                files,
                self._workflow_batch_worker,
                parameters=parameters,
                output_dir=output_dir,
                event_policy=event_policy,
                cancel_event=cancel_event,
                progress_callback=progress,
            )
        def complete(result):
            self.workflow_last_batch_id = str(result.get("batch_id", ""))
            self.workflow_last_batch_result = result
            self._workflow_render_batch_result(result)
            summary = result.get("summary_csv")
            counts = {key: 0 for key in ("success", "failed", "cancelled", "not_run")}
            for item in result.get("results", []):
                counts[item.get("status", "not_run")] = counts.get(item.get("status", "not_run"), 0) + 1
            self._set_status(
                f"批次 {self.workflow_last_batch_id[:10]} 完成：成功 {counts['success']}、失败 {counts['failed']}、取消 {counts['cancelled']}、未运行 {counts['not_run']}。",
                warning=bool(counts["failed"] or result.get("cancelled")),
            )
            self.workflow_batch_result_summary_path = str(summary or "")
        self._workflow_start_task("批量分析", run, complete)

    def _workflow_batch_event_policy(self) -> dict:
        selection = self.workflow_batch_event_policy_var.get()
        if selection == "所有文件共用事件表":
            path = self.workflow_batch_shared_event_path_var.get().strip()
            if not path or not Path(path).is_file():
                raise ValueError("请选择存在的共用事件表。")
            return {"mode": "shared", "path": str(Path(path).resolve())}
        if selection == "逐文件映射事件表":
            mapping = {}
            missing = []
            for path in self.workflow_batch_files:
                key = self._workflow_normalize_path(path)
                event_path = self.workflow_batch_event_map.get(key)
                if not event_path or not Path(event_path).is_file():
                    missing.append(Path(path).name)
                else:
                    mapping[key] = str(Path(event_path).resolve())
            if missing:
                raise ValueError("以下输入文件尚未明确映射事件表：" + "、".join(missing))
            return {"mode": "per_file", "mapping": mapping}
        return {"mode": "none"}

    def _workflow_batch_worker(self, item, raw_frame, event_frame, parameters, cancel_event, stage_callback) -> dict:
        import core
        import experimental

        def stage(name, message):
            stage_callback({"stage": name, "message": message})
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}
        prepare_config = copy.deepcopy(parameters["prepare"])
        analysis_config = copy.deepcopy(parameters["analysis"])
        stage("prepare", "准备时间轴与探索性信号。")
        prepared = core.prepare_data(raw_frame.copy(deep=True), prepare_config)
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}
        analysis_config["max_lag"] = min(int(analysis_config["max_lag"]), max(1, len(prepared["data"]) - 1))
        if len(prepared["data"]) >= 4:
            analysis_config["nperseg"] = min(int(analysis_config["nperseg"]), len(prepared["data"]) // 2)
        stage("analysis", "计算统计检验、矩阵与时间结构。")
        analysis = core.analyze(prepared, analysis_config)
        if "outliers" not in analysis.setdefault("tables", {}):
            analysis["tables"]["outliers"] = self._compute_outlier_flags(
                prepared["data"], prepare_config["value_cols"]
            )
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}

        options = parameters.get("batch_options", {})
        forecast = None
        if options.get("forecast_enabled"):
            forecast_config = copy.deepcopy(parameters.get("forecast", {}))
            column = str(forecast_config.pop("column", "") or prepare_config["value_cols"][0])
            if column not in prepared.get("meta", {}).get("value_cols", []):
                raise ValueError(f"预测列“{column}”不在当前文件选择的数值列中。")
            stage("forecast", "运行留后比较与未来预测。")
            forecast = core.forecast(prepared, column, forecast_config)
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}

        eda_result = None
        if options.get("eda_enabled"):
            eda = parameters.get("eda", {})
            column = str(eda.get("column", ""))
            if column not in prepared.get("meta", {}).get("value_cols", []):
                raise ValueError(f"皮电列“{column}”不在当前文件选择的数值列中。")
            mapping = parameters.get("event_mapping", {})
            events = None
            if event_frame is not None:
                time_col, label_col = mapping.get("time_col"), mapping.get("label_col")
                if not time_col or not label_col or time_col == label_col:
                    raise ValueError("批量事件表需要选择不同的时间列和标签列。")
                if time_col not in event_frame or label_col not in event_frame:
                    raise ValueError("批量事件表列名与所选映射不匹配。")
                import pandas as pd
                events = event_frame[[time_col, label_col]].copy()
                events.columns = ["time", "label"]
                events["time"] = pd.to_numeric(events["time"], errors="coerce")
                if events["time"].isna().any():
                    raise ValueError("事件时间必须是记录起点后的秒数，且不能有空值。")
            elif mapping.get("source") == "raw_marker":
                marker = mapping.get("marker_col")
                if not marker or marker not in raw_frame:
                    raise ValueError("请选择存在的原始连续标签列。")
                events = experimental.markers_to_events(
                    raw_frame,
                    marker_col=marker,
                    time_col=prepare_config.get("time_col"),
                    fs=float(prepared.get("fs", prepare_config.get("fs", 1))),
                    time_kind=prepare_config.get("time_kind", "auto"),
                    time_scale=prepare_config.get("time_scale", "s"),
                    timezone=prepare_config.get("timezone", "UTC"),
                )
            if cancel_event.is_set():
                return {"kind": "analysis", "cancelled": True, "results": {}}
            stage("eda", "清理、分解皮电信号并计算事件统计。")
            eda_result = experimental.analyze_eda(
                prepared,
                column,
                copy.deepcopy(eda.get("config", {})),
                event_df=events,
            )
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}

        analysis_copy = dict(analysis)
        analysis_copy["tables"] = dict(analysis.get("tables", {}))
        analysis_copy["matrices"] = dict(analysis.get("matrices", {}))
        analysis_copy["meta"] = dict(analysis.get("meta", {}))
        if forecast:
            analysis_copy["meta"]["forecast_parameters"] = parameters.get("forecast", {})
        if eda_result:
            for name, frame in eda_result.get("tables", {}).items():
                analysis_copy["tables"][f"eda_{name}"] = frame
            analysis_copy["meta"]["eda"] = dict(eda_result.get("meta", {}))
            analysis_copy["warnings"] = list(analysis_copy.get("warnings", [])) + list(eda_result.get("warnings", []))
        report = self._workflow_get_report_module()
        report_template = options.get("report_template") or report.DEFAULT_REPORT_TEMPLATE
        figures = self._build_export_figures(prepared, analysis_copy, forecast, eda_result)
        stage("export", "保存该文件的报告与所选图表。")
        exported = report.export_report(
            item["output_dir"],
            prepared,
            analysis_copy,
            forecast_result=forecast,
            figures=figures,
            research_path=self._workflow_resource_path("research_notes.json"),
            template=report_template,
            annotations=[],
            progress=lambda message: stage_callback({"stage": "report", "message": str(message)}),
            cancel=cancel_event.is_set,
        )
        if cancel_event.is_set():
            return {"kind": "analysis", "cancelled": True, "results": {}}
        source = {
            "identity": item.get("source_identity", self._workflow_normalize_path(item["path"])),
            "path": item["path"],
        }
        metrics, curves = self._workflow_history_payload(
            "analysis", prepared, analysis, forecast, eda_result, parameters, source
        )
        results = {
            "source": source,
            "sample_count": int(len(prepared["data"])),
            "analysis_summary": self._workflow_dataframe_records(analysis.get("tables", {}).get("summary")),
            "forecast_metrics": self._workflow_dataframe_records(forecast.get("metrics") if forecast else None),
            "eda_peak_count": int(len(eda_result.get("tables", {}).get("eda_peaks", []))) if eda_result else None,
            "warnings": list(prepared.get("warnings", [])) + list(analysis.get("warnings", [])),
            "report_files": exported.get("files", []),
        }
        return {"kind": "analysis", "metrics": metrics, "curves": curves, "results": results}

    def _workflow_render_batch_result(self, result: dict) -> None:
        self.workflow_batch_result_rows = list(result.get("results", []))
        self._workflow_clear_tree(self.workflow_batch_result_tree)
        for index, item in enumerate(self.workflow_batch_result_rows):
            display = next(
                (path for path in self.workflow_batch_files if self._workflow_normalize_path(path) == self._workflow_normalize_path(item.get("path", ""))),
                Path(item.get("path", "")).name,
            )
            self.workflow_batch_result_tree.insert(
                "", "end", iid=f"batch-result-{index}",
                values=(display, item.get("status", ""), item.get("elapsed_seconds", ""), item.get("output_dir", ""), item.get("history_run_id", ""), item.get("error", "")),
            )

    def workflow_batch_retry(self) -> None:
        if not self.workflow_last_batch_id:
            messagebox.showinfo("没有可重试批次", "请先在当前会话完成一个批次。", parent=self.root)
            return
        batch_id = self.workflow_last_batch_id
        def retry(cancel_event, progress):
            return self._workflow_get_store().retry_batch(
                batch_id,
                self._workflow_batch_worker,
                cancel_event=cancel_event,
                progress_callback=progress,
            )
        def retried(result):
            self.workflow_last_batch_id = str(result.get("batch_id", ""))
            self.workflow_last_batch_result = result
            self._workflow_render_batch_result(result)
            self.workflow_batch_result_summary_path = str(result.get("summary_csv") or "")
            self._set_status(f"失败项重试完成：新批次 {self.workflow_last_batch_id[:10]}。", warning=bool(result.get("cancelled")))
        self._workflow_start_task("重试失败批次", retry, retried)

    def workflow_batch_open_summary(self) -> None:
        path = getattr(self, "workflow_batch_result_summary_path", "")
        if not path or not Path(path).is_file():
            messagebox.showinfo("没有汇总文件", "请先完成批量分析。", parent=self.root)
            return
        try:
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror("无法打开汇总文件", str(exc), parent=self.root)

    @staticmethod
    def _workflow_scalar(value):
        if value is None:
            return None
        try:
            if hasattr(value, "item"):
                value = value.item()
        except Exception:
            pass
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if hasattr(value, "isoformat") and not isinstance(value, str):
            try:
                return value.isoformat()
            except Exception:
                pass
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [WorkflowMixin._workflow_scalar(item) for item in value]
        if isinstance(value, dict):
            return {str(key): WorkflowMixin._workflow_scalar(item) for key, item in value.items()}
        return str(value)

    def _workflow_dataframe_records(self, frame, *, limit: int = 200) -> list[dict]:
        if frame is None or not hasattr(frame, "to_dict"):
            return []
        try:
            rows = frame.head(limit).to_dict(orient="records")
        except Exception:
            return []
        return [
            {str(key): self._workflow_scalar(value) for key, value in row.items()}
            for row in rows
        ]

    @staticmethod
    def _workflow_add_frame_metrics(metrics: dict, frame, prefix: str, *, max_entries: int = 150,
                                    metric_units: dict | None = None) -> None:
        if frame is None or not hasattr(frame, "to_dict"):
            return
        try:
            rows = frame.to_dict(orient="records")
        except Exception:
            return
        added = 0
        for row_index, row in enumerate(rows):
            label = str(row.get("variable") or row.get("model") or row.get("event_label") or row_index)
            for discriminator in ("band", "fold", "start_seconds", "scope", "label"):
                if discriminator in row:
                    label += f".{row[discriminator]}"
            unit = str(row.get("unit") or row.get("units") or "未标注")
            for key, value in row.items():
                if key in {"variable", "model", "event_label", "unit", "units", "status", "method", "message"}:
                    continue
                if isinstance(value, bool):
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number):
                    metrics[f"{prefix}.{label}.{key}"] = {"value": number, "unit": (metric_units or {}).get(key, unit)}
                    added += 1
                    if added >= max_entries:
                        return

    def _workflow_relative_axis(self, prepared, frame):
        import numpy as np
        import pandas as pd

        meta = prepared.get("meta", {})
        time_col = meta.get("time_col")
        if time_col and time_col in frame:
            axis = frame[time_col]
            original = prepared.get("original")
            origin_series = original[time_col] if original is not None and time_col in original else axis
            origin = origin_series.iloc[0]
            if pd.api.types.is_datetime64_any_dtype(axis):
                parsed = pd.to_datetime(axis, errors="coerce", utc=True)
                origin = pd.to_datetime(origin, errors="coerce", utc=True)
                return (parsed - origin).dt.total_seconds().to_numpy(dtype=float)
            values = pd.to_numeric(axis, errors="coerce").to_numpy(dtype=float)
            return values - float(pd.to_numeric(pd.Series([origin]), errors="coerce").iloc[0])
        fs = float(prepared.get("fs", 1.0))
        return np.arange(len(frame), dtype=float) / fs

    @staticmethod
    def _workflow_sample_indices(length: int, maximum: int = 1600):
        import numpy as np

        if length <= maximum:
            return np.arange(length, dtype=int)
        return np.linspace(0, length - 1, maximum, dtype=int)

    def _workflow_history_payload(
        self,
        kind: str,
        prepared,
        analysis,
        forecast,
        eda_result,
        parameters: dict,
        source: dict,
    ) -> tuple[dict, dict]:
        import numpy as np
        import pandas as pd

        metrics: dict[str, dict] = {}
        curves: dict[str, dict] = {}
        data = prepared.get("data") if isinstance(prepared, dict) else None
        if data is not None and hasattr(data, "__len__"):
            metrics["sample_count"] = {"value": int(len(data)), "unit": "samples"}
            metrics["sampling_rate"] = {"value": float(prepared.get("fs", 0.0)), "unit": "Hz"}
            meta = prepared.get("meta", {})
            self._workflow_add_frame_metrics(metrics, analysis.get("tables", {}).get("summary"), "summary")
            self._workflow_add_frame_metrics(metrics, analysis.get("tables", {}).get("tests"), "tests")
            self._workflow_add_frame_metrics(metrics, analysis.get("tables", {}).get("band_power"), "bands")
            self._workflow_add_frame_metrics(metrics, analysis.get("tables", {}).get("windows"), "windows")
            axis = self._workflow_relative_axis(prepared, data)
            indexes = self._workflow_sample_indices(len(data))
            identity = str(source.get("identity", "unknown-source"))
            transform = str(meta.get("transform", "none"))
            for column in list(meta.get("value_cols", []))[:8]:
                try:
                    y = pd.to_numeric(data[column], errors="coerce").to_numpy(dtype=float)
                    good = np.isfinite(axis[indexes]) & np.isfinite(y[indexes])
                    chosen = indexes[good]
                    curves[f"series.{column}"] = {
                        "label": str(column),
                        "x": axis[chosen].astype(float).tolist(),
                        "y": y[chosen].astype(float).tolist(),
                        "x_unit": "s",
                        "y_unit": f"未标注:{column}:{transform}:{identity}",
                        "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
                    }
                except Exception:
                    continue

        if forecast:
            metrics["forecast_count"] = {"value": float(len(forecast.get("metrics", []))), "unit": "models"}
            self._workflow_add_frame_metrics(metrics, forecast.get("metrics"), "forecast")
            self._workflow_add_frame_metrics(metrics, forecast.get("rolling_metrics"), "forecast.rolling")
            backtest = forecast.get("backtest")
            future = forecast.get("future")
            if backtest is not None and not getattr(backtest, "empty", True):
                actual = backtest.drop_duplicates("time").sort_values("time")
                x = self._workflow_relative_time_values(prepared, actual["time"])
                idx = self._workflow_sample_indices(len(actual))
                identity = str(source.get("identity", "unknown-source"))
                column = str(forecast.get("column") or getattr(self, "forecast_column", "series"))
                curves["forecast.actual"] = {
                    "label": f"{column} 实际留后值",
                    "x": x[idx].astype(float).tolist(),
                    "y": pd.to_numeric(actual["actual"], errors="coerce").to_numpy(dtype=float)[idx].tolist(),
                    "x_unit": "s",
                    "y_unit": f"未标注:{column}:raw:{identity}",
                    "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
                }
            model_names = []
            if backtest is not None and not getattr(backtest, "empty", True) and "model" in backtest:
                model_names.extend(backtest["model"].astype(str).unique().tolist())
            if future is not None and not getattr(future, "empty", True) and "model" in future:
                model_names.extend(future["model"].astype(str).unique().tolist())
            column = str(forecast.get("column") or getattr(self, "forecast_column", "series"))
            identity = str(source.get("identity", "unknown-source"))
            for model in list(dict.fromkeys(model_names))[:8]:
                groups = []
                if backtest is not None and not getattr(backtest, "empty", True):
                    group = backtest[backtest["model"].astype(str) == model]
                    if not group.empty:
                        groups.append((group["time"], group["predicted"]))
                if future is not None and not getattr(future, "empty", True):
                    group = future[future["model"].astype(str) == model]
                    if not group.empty:
                        groups.append((group["time"], group["predicted"]))
                if groups:
                    times = pd.concat([item[0] for item in groups], ignore_index=True)
                    values = pd.concat([item[1] for item in groups], ignore_index=True)
                    x = self._workflow_relative_time_values(prepared, times)
                    y = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
                    idx = self._workflow_sample_indices(len(x))
                    curves[f"forecast.{model}"] = {
                        "label": f"{column} · {model}",
                        "x": x[idx].astype(float).tolist(),
                        "y": y[idx].tolist(),
                        "x_unit": "s",
                        "y_unit": f"未标注:{column}:raw:{identity}",
                        "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
                    }

        if eda_result:
            tables = eda_result.get("tables", {})
            peaks = tables.get("eda_peaks")
            metrics["eda.peak_count"] = {"value": int(len(peaks)) if peaks is not None else 0, "unit": "peaks"}
            physical_unit = eda_result.get("meta", {}).get("output_unit", "未标注")
            corrected_unit = eda_result.get("meta", {}).get("event_corrected_unit", physical_unit)
            window_units = {key: physical_unit for key in ("scl_mean", "scl_std", "scr_amplitude_mean", "scr_amplitude_median", "scr_amplitude_max")}
            window_units.update({key: "s" for key in ("start_seconds", "end_seconds", "valid_seconds", "scr_rise_seconds_mean", "scr_rise_seconds_median", "scr_recovery50_seconds_mean", "scr_recovery50_seconds_median")})
            window_units.update(scl_slope_per_second=f"{physical_unit}/s", scr_count="peaks", scr_per_minute="peaks/min", scr_recovery50_n="peaks")
            self._workflow_add_frame_metrics(metrics, tables.get("eda_windows"), "eda.windows", metric_units=window_units)
            self._workflow_add_frame_metrics(metrics, tables.get("eda_event_summary"), "eda.events", metric_units={
                "delta_mean": physical_unit, "max_scr_amplitude_mean": physical_unit,
                "corrected_response_mean": corrected_unit, "response_auc_mean": f"{corrected_unit}*s",
                "latency_seconds_mean": "s", "nonresponse_fraction": "fraction",
                "nonresponse_count": "events", "n_events": "events", "n_valid": "events", "scr_count_mean": "peaks"})
            signals = tables.get("eda_signals")
            if signals is not None and not getattr(signals, "empty", True):
                x = pd.to_numeric(signals["time"], errors="coerce").to_numpy(dtype=float)
                idx = self._workflow_sample_indices(len(signals))
                unit = str(eda_result.get("meta", {}).get("output_unit", "未标注"))
                for key in ("raw", "clean", "tonic", "phasic"):
                    if key not in signals:
                        continue
                    y = pd.to_numeric(signals[key], errors="coerce").to_numpy(dtype=float)
                    good = np.isfinite(x[idx]) & np.isfinite(y[idx])
                    chosen = idx[good]
                    curves[f"eda.{key}"] = {
                        "label": f"皮电 {key}",
                        "x": x[chosen].astype(float).tolist(),
                        "y": y[chosen].astype(float).tolist(),
                        "x_unit": "s",
                        "y_unit": unit,
                        "time_basis": {"kind": "elapsed_from_start", "origin": "recording_start", "unit": "s"},
                    }
        return metrics, curves

    def _workflow_relative_time_values(self, prepared, values):
        import numpy as np
        import pandas as pd

        meta = prepared.get("meta", {})
        original = prepared.get("original")
        time_col = meta.get("time_col")
        if time_col and original is not None and time_col in original:
            origin = original[time_col].iloc[0]
        else:
            origin = 0.0
        series = pd.Series(values)
        if pd.api.types.is_datetime64_any_dtype(series) or (
            len(series) and isinstance(series.iloc[0], (pd.Timestamp,))
        ):
            parsed = pd.to_datetime(series, errors="coerce", utc=True)
            origin = pd.to_datetime(origin, errors="coerce", utc=True)
            return (parsed - origin).dt.total_seconds().to_numpy(dtype=float)
        numeric = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
        try:
            numeric_origin = float(pd.to_numeric(pd.Series([origin]), errors="coerce").iloc[0])
        except (TypeError, ValueError):
            numeric_origin = 0.0
        return numeric - numeric_origin

    def _workflow_record_history(self, kind: str, prepared, analysis, forecast, eda_result, parameters: dict, source: dict) -> tuple[str, str | None]:
        try:
            metrics, curves = self._workflow_history_payload(
                kind, prepared, analysis, forecast, eda_result, parameters, source
            )
            snapshot = {
                "kind": kind,
                "source": {
                    "identity": str(source.get("identity", "unknown-source")),
                    "path": str(source.get("path") or ""),
                },
                "parameters": copy.deepcopy(parameters),
                "metrics": metrics,
                "curves": curves,
                "results": {
                    "analysis_summary": self._workflow_dataframe_records(analysis.get("tables", {}).get("summary")),
                    "analysis_tests": self._workflow_dataframe_records(analysis.get("tables", {}).get("tests")),
                    "analysis_band_power": self._workflow_dataframe_records(analysis.get("tables", {}).get("band_power")),
                    "analysis_windows": self._workflow_dataframe_records(analysis.get("tables", {}).get("windows")),
                    "forecast_metrics": self._workflow_dataframe_records(forecast.get("metrics") if forecast else None),
                    "forecast_rolling_metrics": self._workflow_dataframe_records(forecast.get("rolling_metrics") if forecast else None),
                    "forecast_rolling_predictions": self._workflow_dataframe_records(forecast.get("rolling_predictions") if forecast else None),
                    "eda_events": self._workflow_dataframe_records(eda_result.get("tables", {}).get("eda_events") if eda_result else None),
                    "eda_event_summary": self._workflow_dataframe_records(eda_result.get("tables", {}).get("eda_event_summary") if eda_result else None),
                    "eda_windows": self._workflow_dataframe_records(eda_result.get("tables", {}).get("eda_windows") if eda_result else None),
                    "eda_peaks": self._workflow_dataframe_records(eda_result.get("tables", {}).get("eda_peaks") if eda_result else None),
                },
            }
            return self._workflow_get_store().record_run(snapshot), None
        except Exception as exc:
            return "", str(exc)


