from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

try:
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError as exc:  # pragma: no cover - exercised on minimal Linux installs
    tk = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    ScrolledText = None  # type: ignore[assignment]
    _TK_IMPORT_ERROR: Exception | None = exc
else:
    _TK_IMPORT_ERROR = None

from .config import load_config, save_config
from .gui_backend import (
    INVENTORY_SOURCE,
    LIVE_SOURCE,
    GuiInputError,
    GuiReportError,
    GuiRunRequest,
    LoadedReport,
    ProcessController,
    ProcessResult,
    build_check_command,
    build_validate_command,
    default_columns,
    find_project_root,
    format_command,
    load_report,
    load_validation_rows,
)
from .model import FIELD_NAMES, ModuleRule, RsCheckError, ToolConfig


@dataclass(frozen=True)
class WorkerOutcome:
    action: str
    process: ProcessResult | None = None
    report: LoadedReport | None = None
    validation_rows: tuple[Mapping[str, Any], ...] = ()
    error: str = ""


def _evidence_payload(
    record: Mapping[str, Any], finding: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "spec": record.get("spec", {}),
        "module_rule": record.get("module_rule"),
        "step_check": record.get("step_check"),
        "matched_instances": record.get("matched_instances", []),
    }
    if finding is not None:
        evidence["finding"] = {
            "severity": finding.get("severity"),
            "code": finding.get("code"),
            "message": finding.get("message"),
            "instance": finding.get("instance"),
            "expected": finding.get("expected"),
            "actual": finding.get("actual"),
        }
    return evidence


def _parse_step_parameters(value: str) -> tuple[str, ...]:
    parameters = [item.strip() for item in value.replace("，", ",").split(",")]
    parameters = [item for item in parameters if item]
    if len(set(parameters)) != len(parameters):
        raise GuiInputError("决定 step 的 parameter 名不能重复")
    if "RS_CFG_EN" in parameters:
        raise GuiInputError("RS_CFG_EN 不能同时作为决定 step 的 parameter")
    return tuple(parameters)


def _module_rule_from_form(
    module_name: str, has_rs_cfg_en: bool, step_parameters: str
) -> ModuleRule:
    name = module_name.strip()
    if not name:
        raise GuiInputError("RS_module 名不能为空")
    if any(character.isspace() for character in name):
        raise GuiInputError("RS_module 名不能包含空白字符")
    parameters = _parse_step_parameters(step_parameters)
    if any(any(character.isspace() for character in item) for item in parameters):
        raise GuiInputError("parameter 名不能包含空白字符")
    return ModuleRule(
        name=name,
        has_rs_cfg_en=bool(has_rs_cfg_en),
        step_parameters=parameters,
    )


def _position_mapping_from_form(short_name: str, rtl_path: str) -> tuple[str, str]:
    alias = short_name.strip()
    full_path = rtl_path.strip()
    if not alias:
        raise GuiInputError("position 简写不能为空")
    if alias.startswith(".") or alias.endswith("."):
        raise GuiInputError("position 简写不能以 '.' 开头或结尾")
    if not full_path:
        raise GuiInputError("RTL 层次全路径不能为空")
    if not full_path.strip("."):
        raise GuiInputError("RTL 层次全路径不能仅由 '.' 组成")
    return alias, full_path


def _position_display(spec: Mapping[str, Any]) -> str:
    position = str(spec.get("position", ""))
    alias = spec.get("position_alias", "")
    if isinstance(alias, str) and alias:
        return f"{alias} -> {position}"
    return position


class RsCheckApp:
    def __init__(self, root: Any) -> None:
        self.root = root
        self.project_root = find_project_root()
        self.controller = ProcessController()
        self.events: queue.Queue[WorkerOutcome] = queue.Queue()
        self._closing = False
        self._running = False
        self._cancel_requested = threading.Event()
        self._cancel_guard = threading.Lock()
        self._result_records: dict[str, Mapping[str, Any]] = {}
        self._finding_records: dict[str, Mapping[str, Any]] = {}
        self._last_json_path = ""
        self._last_csv_path = ""
        self._saved_control_states: dict[Any, str] = {}
        self._loaded_config: ToolConfig | None = None
        self._loaded_config_path = ""
        self._module_rules: dict[str, ModuleRule] = {}
        self._module_rules_dirty = False
        self._editing_module_name = ""
        self._rule_tree_names: dict[str, str] = {}
        self._position_mappings: dict[str, str] = {}
        self._position_mappings_dirty = False
        self._editing_position_alias = ""
        self._position_tree_aliases: dict[str, str] = {}

        self._configure_root()
        self._create_variables()
        self._build_ui()
        self._load_initial_config()
        self._update_source_mode()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_events)

    @property
    def running(self) -> bool:
        """Whether an operation is awaiting its GUI-thread outcome handler."""
        return self._running

    def _configure_root(self) -> None:
        self.root.title("RTL 打拍例化检查工具")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)
        self.root.option_add("*tearOff", False)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f4f6f8")
        style.configure("TLabel", background="#f4f6f8", foreground="#20262d")
        style.configure("TLabelframe", background="#f4f6f8", bordercolor="#c8d0d8")
        style.configure(
            "TLabelframe.Label",
            background="#f4f6f8",
            foreground="#20262d",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure("Primary.TButton", padding=(14, 7))
        style.configure("Danger.TButton", padding=(14, 7), foreground="#9f1d20")
        style.configure("Summary.TLabel", font=("TkDefaultFont", 10, "bold"))
        style.configure("Treeview", rowheight=26, background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("TkDefaultFont", 9, "bold"))

        try:
            families = set(tkfont.families(self.root))
            for candidate in (
                "Microsoft YaHei UI",
                "Noto Sans CJK SC",
                "WenQuanYi Micro Hei",
                "DejaVu Sans",
            ):
                if candidate in families:
                    tkfont.nametofont("TkDefaultFont").configure(family=candidate, size=10)
                    tkfont.nametofont("TkTextFont").configure(family=candidate, size=10)
                    break
        except Exception:
            pass

    def _create_variables(self) -> None:
        example_excel = self.project_root / "examples" / "specs.csv"
        example_config = self.project_root / "config" / "rscheck.example.json"
        collector = self.project_root / "npi" / "build" / "rs_npi_collector"
        output = self.project_root / "output"

        self.excel_var = tk.StringVar(value=str(example_excel) if example_excel.is_file() else "")
        self.config_var = tk.StringVar(
            value=str(example_config) if example_config.is_file() else ""
        )
        self.sheet_var = tk.StringVar(value="1")
        self.header_row_var = tk.StringVar(value="1")
        self.data_start_row_var = tk.StringVar(value="2")
        self.header_check_var = tk.BooleanVar(value=False)
        self.column_vars = {
            name: tk.StringVar(value=value) for name, value in default_columns().items()
        }
        self.module_search_var = tk.StringVar()
        self.module_name_var = tk.StringVar()
        self.module_has_rs_cfg_en_var = tk.BooleanVar(value=False)
        self.module_step_parameters_var = tk.StringVar()
        self.module_rule_status_var = tk.StringVar(value="规则 0")
        self.position_search_var = tk.StringVar()
        self.position_alias_var = tk.StringVar()
        self.position_path_var = tk.StringVar()
        self.position_mapping_status_var = tk.StringVar(value="映射 0")

        self.source_mode_var = tk.StringVar(value=LIVE_SOURCE)
        self.collector_var = tk.StringVar(value=str(collector))
        self.elab_db_var = tk.StringVar()
        self.inventory_var = tk.StringVar()
        self.npi_lib_var = tk.StringVar()
        self.npi_timeout_var = tk.StringVar(value="180")
        self.keep_inventory_var = tk.StringVar(value=str(output / "inventory.current.json"))
        self.json_report_var = tk.StringVar(value=str(output / "rs_report.json"))
        self.csv_report_var = tk.StringVar(value=str(output / "rs_report.csv"))

        self.status_var = tk.StringVar(value="就绪")
        self.summary_state_var = tk.StringVar(value="未运行")
        self.summary_rows_var = tk.StringVar(value="行数 0")
        self.summary_pass_var = tk.StringVar(value="通过 0")
        self.summary_fail_var = tk.StringVar(value="失败 0")
        self.summary_errors_var = tk.StringVar(value="错误 0")
        self.summary_warnings_var = tk.StringVar(value="警告 0")

    def _build_ui(self) -> None:
        self.root.configure(background="#f4f6f8")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))

        self.setup_tab = ttk.Frame(self.notebook, padding=12)
        self.rules_tab = ttk.Frame(self.notebook, padding=10)
        self.positions_tab = ttk.Frame(self.notebook, padding=10)
        self.results_tab = ttk.Frame(self.notebook, padding=10)
        self.log_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.setup_tab, text="检查配置")
        self.notebook.add(self.rules_tab, text="模块规则库")
        self.notebook.add(self.positions_tab, text="Position 映射库")
        self.notebook.add(self.results_tab, text="检查结果")
        self.notebook.add(self.log_tab, text="运行日志")

        self._build_setup_tab()
        self._build_rules_tab()
        self._build_positions_tab()
        self._build_results_tab()
        self._build_log_tab()
        self._build_status_bar()

    def _build_setup_tab(self) -> None:
        tab = self.setup_tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)

        input_group = ttk.LabelFrame(tab, text="规格输入", padding=10)
        input_group.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        input_group.columnconfigure(1, weight=1)
        self._path_row(
            input_group,
            0,
            "Excel / CSV",
            self.excel_var,
            lambda: self._choose_file(
                self.excel_var,
                (("规格文件", "*.xlsx *.xlsm *.csv *.tsv"), ("所有文件", "*.*")),
            ),
        )
        self._path_row(
            input_group,
            1,
            "配置 JSON",
            self.config_var,
            self._choose_config,
            extra_button=("加载", self._load_config_from_form),
        )

        excel_options = ttk.Frame(input_group)
        excel_options.grid(
            row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0)
        )
        excel_options.columnconfigure(6, weight=1)
        ttk.Label(excel_options, text="工作表").grid(row=0, column=0, sticky="w")
        ttk.Entry(excel_options, textvariable=self.sheet_var, width=16).grid(
            row=0, column=1, sticky="w", padx=(8, 18)
        )
        ttk.Label(excel_options, text="表头行").grid(row=0, column=2, sticky="w")
        tk.Spinbox(
            excel_options,
            from_=1,
            to=100000,
            textvariable=self.header_row_var,
            width=7,
        ).grid(row=0, column=3, sticky="w", padx=(8, 18))
        ttk.Label(excel_options, text="数据起始行").grid(row=0, column=4, sticky="w")
        tk.Spinbox(
            excel_options,
            from_=1,
            to=100000,
            textvariable=self.data_start_row_var,
            width=7,
        ).grid(row=0, column=5, sticky="w", padx=(8, 18))
        ttk.Checkbutton(
            excel_options,
            text="严格校验表头（可选）",
            variable=self.header_check_var,
        ).grid(row=0, column=6, sticky="e")

        columns_group = ttk.LabelFrame(
            tab,
            text="内部属性 -> Excel 列号（从 1 开始）",
            padding=10,
        )
        columns_group.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        columns_group.columnconfigure(1, weight=1)
        columns_group.columnconfigure(3, weight=1)
        rows_per_block = (len(FIELD_NAMES) + 1) // 2
        for index, name in enumerate(FIELD_NAMES):
            row = index % rows_per_block
            block = index // rows_per_block
            label_column = block * 2
            ttk.Label(columns_group, text=name).grid(
                row=row,
                column=label_column,
                sticky="w",
                padx=(0 if block == 0 else 18, 8),
                pady=8,
            )
            tk.Spinbox(
                columns_group,
                from_=1,
                to=16384,
                textvariable=self.column_vars[name],
                width=8,
            ).grid(row=row, column=label_column + 1, sticky="w", pady=8)

        self.source_group = ttk.LabelFrame(tab, text="RTL 数据来源", padding=10)
        self.source_group.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        self.source_group.columnconfigure(0, weight=1)
        mode_bar = ttk.Frame(self.source_group)
        mode_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Radiobutton(
            mode_bar,
            text="在线 NPI（elaborated KDB）",
            variable=self.source_mode_var,
            value=LIVE_SOURCE,
            command=self._update_source_mode,
        ).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(
            mode_bar,
            text="离线 Inventory",
            variable=self.source_mode_var,
            value=INVENTORY_SOURCE,
            command=self._update_source_mode,
        ).pack(side="left")

        self.live_frame = ttk.Frame(self.source_group)
        self.live_frame.grid(row=1, column=0, sticky="nsew")
        self.live_frame.columnconfigure(1, weight=1)
        self._compact_path_row(
            self.live_frame,
            0,
            "Collector",
            self.collector_var,
            lambda: self._choose_file(self.collector_var, (("可执行文件", "*"),)),
        )
        self._compact_path_row(
            self.live_frame,
            1,
            "Elab KDB",
            self.elab_db_var,
            lambda: self._choose_directory(self.elab_db_var),
        )
        self._compact_path_row(
            self.live_frame,
            2,
            "NPI 库目录",
            self.npi_lib_var,
            lambda: self._choose_directory(self.npi_lib_var),
        )
        self._compact_path_row(
            self.live_frame,
            3,
            "保存 Inventory",
            self.keep_inventory_var,
            lambda: self._choose_save(
                self.keep_inventory_var, ".json", (("JSON", "*.json"),)
            ),
        )
        ttk.Label(self.live_frame, text="超时（秒）").grid(row=4, column=0, sticky="w", pady=5)
        tk.Spinbox(
            self.live_frame,
            from_=1,
            to=86400,
            textvariable=self.npi_timeout_var,
            width=10,
        ).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=5)

        self.inventory_frame = ttk.Frame(self.source_group)
        self.inventory_frame.columnconfigure(1, weight=1)
        self._compact_path_row(
            self.inventory_frame,
            0,
            "Inventory JSON",
            self.inventory_var,
            lambda: self._choose_file(
                self.inventory_var, (("Inventory JSON", "*.json"), ("所有文件", "*.*"))
            ),
        )

        output_group = ttk.LabelFrame(tab, text="报告输出", padding=10)
        output_group.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        output_group.columnconfigure(1, weight=1)
        output_group.columnconfigure(4, weight=1)
        ttk.Label(output_group, text="JSON").grid(row=0, column=0, sticky="w")
        ttk.Entry(output_group, textvariable=self.json_report_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 4)
        )
        ttk.Button(
            output_group,
            text="浏览",
            command=lambda: self._choose_save(
                self.json_report_var, ".json", (("JSON", "*.json"),)
            ),
        ).grid(row=0, column=2, padx=(0, 18))
        ttk.Label(output_group, text="CSV").grid(row=0, column=3, sticky="w")
        ttk.Entry(output_group, textvariable=self.csv_report_var).grid(
            row=0, column=4, sticky="ew", padx=(8, 4)
        )
        ttk.Button(
            output_group,
            text="浏览",
            command=lambda: self._choose_save(
                self.csv_report_var, ".csv", (("CSV", "*.csv"),)
            ),
        ).grid(row=0, column=5)

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.validate_button = ttk.Button(
            actions,
            text="验证 Excel",
            command=lambda: self._start_operation("validate"),
        )
        self.validate_button.pack(side="left")
        self.run_button = ttk.Button(
            actions,
            text="运行 RTL 检查",
            style="Primary.TButton",
            command=lambda: self._start_operation("check"),
        )
        self.run_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(
            actions,
            text="取消",
            style="Danger.TButton",
            state="disabled",
            command=self._cancel_operation,
        )
        self.cancel_button.pack(side="left")
        ttk.Button(actions, text="打开报告目录", command=self._open_report_directory).pack(
            side="right"
        )

    def _build_rules_tab(self) -> None:
        tab = self.rules_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索模块").grid(row=0, column=0, sticky="w")
        ttk.Entry(toolbar, textvariable=self.module_search_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(toolbar, textvariable=self.module_rule_status_var).grid(
            row=0, column=2, sticky="e"
        )

        tree_frame = ttk.Frame(tab)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.rule_tree = ttk.Treeview(
            tree_frame,
            columns=("module", "rs_cfg_en", "step_parameters"),
            show="headings",
            selectmode="browse",
        )
        for name, title, width in (
            ("module", "RS_module", 260),
            ("rs_cfg_en", "RS_CFG_EN parameter", 170),
            ("step_parameters", "决定 step 的 parameters", 420),
        ):
            self.rule_tree.heading(name, text=title)
            self.rule_tree.column(
                name,
                width=width,
                minwidth=100,
                stretch=name == "step_parameters",
            )
        rule_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.rule_tree.yview
        )
        self.rule_tree.configure(yscrollcommand=rule_scroll.set)
        self.rule_tree.grid(row=0, column=0, sticky="nsew")
        rule_scroll.grid(row=0, column=1, sticky="ns")
        self.rule_tree.bind("<<TreeviewSelect>>", self._on_rule_selected)

        editor = ttk.LabelFrame(tab, text="规则编辑", padding=10)
        editor.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(4, weight=2)
        ttk.Label(editor, text="RS_module").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.module_name_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Checkbutton(
            editor,
            text="有 RS_CFG_EN",
            variable=self.module_has_rs_cfg_en_var,
        ).grid(row=0, column=2, sticky="w", padx=(0, 18))
        ttk.Label(editor, text="step parameters").grid(row=0, column=3, sticky="w")
        ttk.Entry(editor, textvariable=self.module_step_parameters_var).grid(
            row=0, column=4, sticky="ew", padx=(8, 0)
        )

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="新建", command=self._new_rule).pack(side="left")
        ttk.Button(actions, text="应用修改", command=self._apply_rule).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="删除", command=self._delete_rule).pack(side="left")
        ttk.Button(actions, text="保存规则库", command=self._save_module_rules).pack(
            side="right"
        )
        self.module_search_var.trace_add("write", lambda *_: self._render_rule_tree())

    def _build_positions_tab(self) -> None:
        tab = self.positions_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索 position").grid(row=0, column=0, sticky="w")
        ttk.Entry(toolbar, textvariable=self.position_search_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(toolbar, textvariable=self.position_mapping_status_var).grid(
            row=0, column=2, sticky="e"
        )

        tree_frame = ttk.Frame(tab)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.position_tree = ttk.Treeview(
            tree_frame,
            columns=("alias", "rtl_path"),
            show="headings",
            selectmode="browse",
        )
        for name, title, width, stretch in (
            ("alias", "position 简写", 280, False),
            ("rtl_path", "RTL 层次全路径", 1200, False),
        ):
            self.position_tree.heading(name, text=title)
            self.position_tree.column(
                name, width=width, minwidth=140, stretch=stretch
            )
        position_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.position_tree.yview
        )
        position_xscroll = ttk.Scrollbar(
            tree_frame, orient="horizontal", command=self.position_tree.xview
        )
        self.position_tree.configure(
            yscrollcommand=position_scroll.set,
            xscrollcommand=position_xscroll.set,
        )
        self.position_tree.grid(row=0, column=0, sticky="nsew")
        position_scroll.grid(row=0, column=1, sticky="ns")
        position_xscroll.grid(row=1, column=0, sticky="ew")
        self.position_tree.bind("<<TreeviewSelect>>", self._on_position_selected)

        editor = ttk.LabelFrame(tab, text="Position 映射编辑", padding=10)
        editor.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=3)
        ttk.Label(editor, text="position 简写").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.position_alias_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(editor, text="RTL 层次全路径").grid(row=0, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.position_path_var).grid(
            row=0, column=3, sticky="ew", padx=(8, 0)
        )

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="新建", command=self._new_position_mapping).pack(
            side="left"
        )
        ttk.Button(
            actions, text="应用修改", command=self._apply_position_mapping
        ).pack(side="left", padx=8)
        ttk.Button(
            actions, text="删除", command=self._delete_position_mapping
        ).pack(side="left")
        ttk.Button(
            actions, text="保存映射库", command=self._save_position_mappings
        ).pack(side="right")
        self.position_search_var.trace_add(
            "write", lambda *_: self._render_position_tree()
        )

    def _build_results_tab(self) -> None:
        tab = self.results_tab
        tab.rowconfigure(1, weight=3)
        tab.rowconfigure(2, weight=2)
        tab.columnconfigure(0, weight=1)

        summary = ttk.Frame(tab)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        summary.columnconfigure(6, weight=1)
        for index, variable in enumerate(
            (
                self.summary_state_var,
                self.summary_rows_var,
                self.summary_pass_var,
                self.summary_fail_var,
                self.summary_errors_var,
                self.summary_warnings_var,
            )
        ):
            ttk.Label(summary, textvariable=variable, style="Summary.TLabel").grid(
                row=0, column=index, sticky="w", padx=(0, 22)
            )

        result_frame = ttk.Frame(tab)
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        columns = (
            "status",
            "row",
            "interface",
            "position",
            "rs_inst",
            "rs_cfg_en",
            "physical_instances",
            "effective_step",
            "findings",
        )
        self.result_tree = ttk.Treeview(
            result_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "status": "状态",
            "row": "Excel 行",
            "interface": "Interface",
            "position": "Position",
            "rs_inst": "RS_inst",
            "rs_cfg_en": "RS_CFG_EN",
            "physical_instances": "匹配实例",
            "effective_step": "实际/期望拍",
            "findings": "Finding",
        }
        widths = {
            "status": 65,
            "row": 60,
            "interface": 85,
            "position": 520,
            "rs_inst": 120,
            "rs_cfg_en": 90,
            "physical_instances": 75,
            "effective_step": 95,
            "findings": 80,
        }
        for name in columns:
            self.result_tree.heading(name, text=headings[name])
            self.result_tree.column(
                name, width=widths[name], minwidth=55, stretch=False
            )
        result_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_tree.yview)
        result_xscroll = ttk.Scrollbar(
            result_frame, orient="horizontal", command=self.result_tree.xview
        )
        self.result_tree.configure(
            yscrollcommand=result_scroll.set,
            xscrollcommand=result_xscroll.set,
        )
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        result_scroll.grid(row=0, column=1, sticky="ns")
        result_xscroll.grid(row=1, column=0, sticky="ew")
        self.result_tree.tag_configure("pass", foreground="#176b3a")
        self.result_tree.tag_configure("fail", foreground="#a02124")
        self.result_tree.tag_configure("warning", foreground="#8a5a00")
        self.result_tree.bind("<<TreeviewSelect>>", self._on_result_selected)

        detail_pane = ttk.Panedwindow(tab, orient="horizontal")
        detail_pane.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

        findings_frame = ttk.Frame(detail_pane)
        findings_frame.rowconfigure(0, weight=1)
        findings_frame.columnconfigure(0, weight=1)
        finding_columns = ("severity", "code", "instance", "message")
        self.finding_tree = ttk.Treeview(
            findings_frame,
            columns=finding_columns,
            show="headings",
            selectmode="browse",
        )
        for name, title, width in (
            ("severity", "级别", 70),
            ("code", "代码", 190),
            ("instance", "实例", 230),
            ("message", "说明", 420),
        ):
            self.finding_tree.heading(name, text=title)
            self.finding_tree.column(name, width=width, minwidth=60, stretch=name == "message")
        finding_scroll = ttk.Scrollbar(
            findings_frame, orient="vertical", command=self.finding_tree.yview
        )
        self.finding_tree.configure(yscrollcommand=finding_scroll.set)
        self.finding_tree.grid(row=0, column=0, sticky="nsew")
        finding_scroll.grid(row=0, column=1, sticky="ns")
        self.finding_tree.bind("<<TreeviewSelect>>", self._on_finding_selected)

        evidence_frame = ttk.Frame(detail_pane)
        evidence_frame.rowconfigure(0, weight=1)
        evidence_frame.columnconfigure(0, weight=1)
        self.evidence_text = ScrolledText(
            evidence_frame,
            wrap="word",
            height=9,
            font=("TkFixedFont", 9),
            background="#ffffff",
            foreground="#20262d",
        )
        self.evidence_text.grid(row=0, column=0, sticky="nsew")
        self.evidence_text.configure(state="disabled")
        detail_pane.add(findings_frame, weight=3)
        detail_pane.add(evidence_frame, weight=2)

    def _build_log_tab(self) -> None:
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        self.log_text = ScrolledText(
            self.log_tab,
            wrap="word",
            font=("TkFixedFont", 9),
            background="#111820",
            foreground="#d8e0e8",
            insertbackground="#d8e0e8",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

    def _build_status_bar(self) -> None:
        status = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        status.grid(row=1, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=1, sticky="e")

    def _path_row(
        self,
        parent: Any,
        row: int,
        label: str,
        variable: Any,
        command: Any,
        *,
        extra_button: tuple[str, Any] | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(10, 4), pady=4
        )
        ttk.Button(parent, text="浏览", command=command).grid(row=row, column=2, pady=4)
        if extra_button:
            ttk.Button(parent, text=extra_button[0], command=extra_button[1]).grid(
                row=row, column=3, padx=(4, 0), pady=4
            )

    def _compact_path_row(
        self, parent: Any, row: int, label: str, variable: Any, command: Any
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(8, 4), pady=4
        )
        ttk.Button(parent, text="浏览", width=6, command=command).grid(
            row=row, column=2, pady=4
        )

    def _choose_file(self, variable: Any, filetypes: Any) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            initialdir=self._initial_directory(variable.get()),
            filetypes=filetypes,
        )
        if selected:
            variable.set(selected)

    def _choose_config(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            initialdir=self._initial_directory(self.config_var.get()),
            filetypes=(("JSON", "*.json"), ("所有文件", "*.*")),
        )
        if selected:
            if self._module_rules_dirty and not self._confirm_discard_rule_changes():
                return
            if (
                self._position_mappings_dirty
                and not self._confirm_discard_position_changes()
            ):
                return
            self.config_var.set(selected)
            self._load_config_from_form(confirm_discard=False)

    def _choose_directory(self, variable: Any) -> None:
        selected = filedialog.askdirectory(
            parent=self.root, initialdir=self._initial_directory(variable.get())
        )
        if selected:
            variable.set(selected)

    def _choose_save(self, variable: Any, extension: str, filetypes: Any) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            initialdir=self._initial_directory(variable.get()),
            initialfile=Path(variable.get()).name if variable.get().strip() else "",
            defaultextension=extension,
            filetypes=filetypes,
        )
        if selected:
            variable.set(selected)

    def _initial_directory(self, value: str) -> str:
        path = Path(value).expanduser() if value.strip() else self.project_root
        if path.is_dir():
            return str(path)
        if path.parent.is_dir():
            return str(path.parent)
        return str(self.project_root)

    def _load_initial_config(self) -> None:
        if self.config_var.get().strip():
            self._load_config_from_form(show_error=False)

    def _load_config_from_form(
        self, show_error: bool = True, confirm_discard: bool = True
    ) -> None:
        if confirm_discard:
            if (
                self._module_rules_dirty
                and not self._confirm_discard_rule_changes()
            ):
                return
            if (
                self._position_mappings_dirty
                and not self._confirm_discard_position_changes()
            ):
                return
        try:
            config = load_config(self.config_var.get().strip())
        except RsCheckError as exc:
            self.status_var.set("配置加载失败")
            if show_error:
                messagebox.showerror("配置错误", str(exc), parent=self.root)
            return
        self.sheet_var.set(str(config.excel.sheet))
        self.header_row_var.set(str(config.excel.header_row))
        self.data_start_row_var.set(str(config.excel.data_start_row))
        self.header_check_var.set(config.excel.validate_headers)
        for name in FIELD_NAMES:
            self.column_vars[name].set(str(config.excel.columns[name]))
        self._loaded_config = config
        self._loaded_config_path = str(Path(self.config_var.get().strip()).resolve())
        self._module_rules = dict(config.module_rules)
        self._module_rules_dirty = False
        self._position_mappings = dict(config.position_mappings)
        self._position_mappings_dirty = False
        self._new_rule()
        self._new_position_mapping()
        self._render_rule_tree()
        self._render_position_tree()
        self.status_var.set("配置已加载")

    def _confirm_discard_rule_changes(self) -> bool:
        if not self._module_rules_dirty:
            return True
        return bool(
            messagebox.askyesno(
                "未保存规则",
                "模块规则库有未保存修改，确定放弃吗？",
                parent=self.root,
            )
        )

    def _confirm_discard_position_changes(self) -> bool:
        if not self._position_mappings_dirty:
            return True
        return bool(
            messagebox.askyesno(
                "未保存映射",
                "Position 映射库有未保存修改，确定放弃吗？",
                parent=self.root,
            )
        )

    def _render_rule_tree(self) -> None:
        if not hasattr(self, "rule_tree"):
            return
        selected_name = self._editing_module_name
        for item in self.rule_tree.get_children():
            self.rule_tree.delete(item)
        self._rule_tree_names.clear()
        search = self.module_search_var.get().strip().casefold()
        selected_iid = ""
        for index, name in enumerate(sorted(self._module_rules)):
            if search and search not in name.casefold():
                continue
            rule = self._module_rules[name]
            iid = f"module-rule-{index}"
            self._rule_tree_names[iid] = name
            self.rule_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    name,
                    "有" if rule.has_rs_cfg_en else "无",
                    ", ".join(rule.step_parameters) or "-",
                ),
            )
            if name == selected_name:
                selected_iid = iid
        if selected_iid:
            self.rule_tree.selection_set(selected_iid)
            self.rule_tree.see(selected_iid)
        suffix = " · 未保存" if self._module_rules_dirty else ""
        self.module_rule_status_var.set(f"规则 {len(self._module_rules)}{suffix}")

    def _on_rule_selected(self, _event: Any = None) -> None:
        selection = self.rule_tree.selection()
        if not selection:
            return
        name = self._rule_tree_names.get(selection[0], "")
        rule = self._module_rules.get(name)
        if rule is None:
            return
        self._editing_module_name = name
        self.module_name_var.set(rule.name)
        self.module_has_rs_cfg_en_var.set(rule.has_rs_cfg_en)
        self.module_step_parameters_var.set(", ".join(rule.step_parameters))

    def _new_rule(self) -> None:
        self._editing_module_name = ""
        self.module_name_var.set("")
        self.module_has_rs_cfg_en_var.set(False)
        self.module_step_parameters_var.set("")
        if hasattr(self, "rule_tree"):
            self.rule_tree.selection_remove(*self.rule_tree.selection())

    def _apply_rule(self) -> None:
        try:
            rule = _module_rule_from_form(
                self.module_name_var.get(),
                self.module_has_rs_cfg_en_var.get(),
                self.module_step_parameters_var.get(),
            )
        except GuiInputError as exc:
            messagebox.showerror("规则错误", str(exc), parent=self.root)
            return
        original = self._editing_module_name
        if rule.name in self._module_rules and rule.name != original:
            messagebox.showerror(
                "规则错误",
                f"RS_module {rule.name!r} 已存在",
                parent=self.root,
            )
            return
        if original and original != rule.name:
            self._module_rules.pop(original, None)
        self._module_rules[rule.name] = rule
        self._editing_module_name = rule.name
        self._module_rules_dirty = True
        self._render_rule_tree()
        self.status_var.set("模块规则已修改")

    def _delete_rule(self) -> None:
        name = self._editing_module_name
        if not name or name not in self._module_rules:
            return
        if not messagebox.askyesno(
            "删除规则",
            f"确定删除 RS_module {name!r} 的规则吗？",
            parent=self.root,
        ):
            return
        del self._module_rules[name]
        self._module_rules_dirty = True
        self._new_rule()
        self._render_rule_tree()
        self.status_var.set("模块规则已删除")

    def _save_module_rules(self) -> None:
        config_path = self.config_var.get().strip()
        if self._loaded_config is None or not config_path:
            messagebox.showerror("保存失败", "请先加载配置 JSON", parent=self.root)
            return
        if str(Path(config_path).resolve()) != self._loaded_config_path:
            messagebox.showerror(
                "保存失败",
                "配置路径已变化，请先加载当前配置",
                parent=self.root,
            )
            return
        baseline = self._loaded_config
        try:
            current = load_config(config_path)
            if current.module_rules != baseline.module_rules:
                messagebox.showerror(
                    "保存冲突",
                    "模块规则库已被外部修改，请先重新加载配置后再保存",
                    parent=self.root,
                )
                return
            updated = replace(current, module_rules=dict(self._module_rules))
            output = save_config(updated, config_path)
            reloaded = load_config(output)
        except RsCheckError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        if self._position_mappings_dirty:
            self._loaded_config = replace(
                reloaded,
                position_mappings=dict(baseline.position_mappings),
            )
        else:
            self._loaded_config = reloaded
            self._position_mappings = dict(reloaded.position_mappings)
            self._new_position_mapping()
            self._render_position_tree()
        self._module_rules = dict(reloaded.module_rules)
        self._module_rules_dirty = False
        self._render_rule_tree()
        self.status_var.set("模块规则库已保存")

    def _render_position_tree(self) -> None:
        if not hasattr(self, "position_tree"):
            return
        selected_alias = self._editing_position_alias
        for item in self.position_tree.get_children():
            self.position_tree.delete(item)
        self._position_tree_aliases.clear()
        search = self.position_search_var.get().strip().casefold()
        selected_iid = ""
        for index, alias in enumerate(sorted(self._position_mappings)):
            rtl_path = self._position_mappings[alias]
            if (
                search
                and search not in alias.casefold()
                and search not in rtl_path.casefold()
            ):
                continue
            iid = f"position-mapping-{index}"
            self._position_tree_aliases[iid] = alias
            self.position_tree.insert(
                "", "end", iid=iid, values=(alias, rtl_path)
            )
            if alias == selected_alias:
                selected_iid = iid
        if selected_iid:
            self.position_tree.selection_set(selected_iid)
            self.position_tree.see(selected_iid)
        suffix = " · 未保存" if self._position_mappings_dirty else ""
        self.position_mapping_status_var.set(
            f"映射 {len(self._position_mappings)}{suffix}"
        )

    def _on_position_selected(self, _event: Any = None) -> None:
        selection = self.position_tree.selection()
        if not selection:
            return
        alias = self._position_tree_aliases.get(selection[0], "")
        rtl_path = self._position_mappings.get(alias)
        if rtl_path is None:
            return
        self._editing_position_alias = alias
        self.position_alias_var.set(alias)
        self.position_path_var.set(rtl_path)

    def _new_position_mapping(self) -> None:
        self._editing_position_alias = ""
        self.position_alias_var.set("")
        self.position_path_var.set("")
        if hasattr(self, "position_tree"):
            self.position_tree.selection_remove(*self.position_tree.selection())

    def _apply_position_mapping(self) -> None:
        try:
            alias, rtl_path = _position_mapping_from_form(
                self.position_alias_var.get(), self.position_path_var.get()
            )
        except GuiInputError as exc:
            messagebox.showerror("映射错误", str(exc), parent=self.root)
            return
        original = self._editing_position_alias
        if alias in self._position_mappings and alias != original:
            messagebox.showerror(
                "映射错误",
                f"position 简写 {alias!r} 已存在",
                parent=self.root,
            )
            return
        if original and original != alias:
            self._position_mappings.pop(original, None)
        self._position_mappings[alias] = rtl_path
        self._editing_position_alias = alias
        self._position_mappings_dirty = True
        self._render_position_tree()
        self.status_var.set("Position 映射已修改")

    def _delete_position_mapping(self) -> None:
        alias = self._editing_position_alias
        if not alias or alias not in self._position_mappings:
            return
        if not messagebox.askyesno(
            "删除映射",
            f"确定删除 position 简写 {alias!r} 的映射吗？",
            parent=self.root,
        ):
            return
        del self._position_mappings[alias]
        self._position_mappings_dirty = True
        self._new_position_mapping()
        self._render_position_tree()
        self.status_var.set("Position 映射已删除")

    def _save_position_mappings(self) -> None:
        config_path = self.config_var.get().strip()
        if self._loaded_config is None or not config_path:
            messagebox.showerror("保存失败", "请先加载配置 JSON", parent=self.root)
            return
        if str(Path(config_path).resolve()) != self._loaded_config_path:
            messagebox.showerror(
                "保存失败",
                "配置路径已变化，请先加载当前配置",
                parent=self.root,
            )
            return
        baseline = self._loaded_config
        try:
            current = load_config(config_path)
            if current.position_mappings != baseline.position_mappings:
                messagebox.showerror(
                    "保存冲突",
                    "Position 映射库已被外部修改，请先重新加载配置后再保存",
                    parent=self.root,
                )
                return
            updated = replace(
                current,
                position_mappings=dict(self._position_mappings),
            )
            output = save_config(updated, config_path)
            reloaded = load_config(output)
        except RsCheckError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        if self._module_rules_dirty:
            self._loaded_config = replace(
                reloaded,
                module_rules=dict(baseline.module_rules),
            )
        else:
            self._loaded_config = reloaded
            self._module_rules = dict(reloaded.module_rules)
            self._new_rule()
            self._render_rule_tree()
        self._position_mappings = dict(reloaded.position_mappings)
        self._position_mappings_dirty = False
        self._render_position_tree()
        self.status_var.set("Position 映射库已保存")

    def _update_source_mode(self) -> None:
        if self.source_mode_var.get() == LIVE_SOURCE:
            self.inventory_frame.grid_remove()
            self.live_frame.grid(row=1, column=0, sticky="nsew")
        else:
            self.live_frame.grid_remove()
            self.inventory_frame.grid(row=1, column=0, sticky="nsew")

    def _request(self) -> GuiRunRequest:
        return GuiRunRequest(
            excel_path=self.excel_var.get(),
            config_path=self.config_var.get(),
            columns={name: variable.get() for name, variable in self.column_vars.items()},
            sheet=self.sheet_var.get(),
            header_row=self.header_row_var.get(),
            data_start_row=self.data_start_row_var.get(),
            validate_headers=self.header_check_var.get(),
            source_mode=self.source_mode_var.get(),
            collector_path=self.collector_var.get(),
            elab_db_path=self.elab_db_var.get(),
            inventory_path=self.inventory_var.get(),
            npi_lib_dir=self.npi_lib_var.get(),
            npi_timeout=self.npi_timeout_var.get(),
            keep_inventory_path=self.keep_inventory_var.get(),
            json_report_path=self.json_report_var.get(),
            csv_report_path=self.csv_report_var.get(),
        )

    def _start_operation(self, action: str) -> None:
        if self.running or self.controller.is_running:
            return
        if self._module_rules_dirty:
            messagebox.showerror(
                "规则未保存",
                "请先在模块规则库页保存修改",
                parent=self.root,
            )
            return
        if self._position_mappings_dirty:
            messagebox.showerror(
                "映射未保存",
                "请先在 Position 映射库页保存修改",
                parent=self.root,
            )
            return
        config_path = self.config_var.get().strip()
        if (
            self._loaded_config is None
            or not config_path
            or str(Path(config_path).resolve()) != self._loaded_config_path
        ):
            messagebox.showerror(
                "配置未加载",
                "配置路径已变化，请先点击“加载”同步模块规则库和 Position 映射库",
                parent=self.root,
            )
            return
        request = self._request()
        try:
            if action == "validate":
                build_validate_command(request)
            else:
                command = build_check_command(
                    request, internal_json_report=request.json_report_path or "report.json"
                )
                self.json_report_var.set(
                    command[command.index("--json-report") + 1]
                )
                if "--csv-report" in command:
                    self.csv_report_var.set(
                        command[command.index("--csv-report") + 1]
                    )
                if "--keep-inventory" in command:
                    self.keep_inventory_var.set(
                        command[command.index("--keep-inventory") + 1]
                    )
                request = self._request()
        except GuiInputError as exc:
            messagebox.showerror("输入错误", str(exc), parent=self.root)
            return

        with self._cancel_guard:
            self._cancel_requested.clear()
        self._clear_results()
        self._reset_summary("RUNNING")
        self._set_running(True)
        self.status_var.set("正在验证 Excel" if action == "validate" else "正在检查 RTL")
        self._append_log("\n" + "=" * 72 + "\n")
        worker = threading.Thread(
            target=self._run_worker,
            args=(action, request),
            name=f"rscheck-gui-{action}",
            daemon=True,
        )
        worker.start()

    def _run_worker(self, action: str, request: GuiRunRequest) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="rscheck-gui-") as temp_name:
                if self._cancel_requested.is_set():
                    self.events.put(WorkerOutcome(action=action))
                    return
                if action == "validate":
                    command = build_validate_command(request)
                    report_path: Path | None = None
                else:
                    report_path = (
                        Path(request.json_report_path).expanduser()
                        if request.json_report_path.strip()
                        else Path(temp_name) / "report.json"
                    )
                    command = build_check_command(
                        request, internal_json_report=report_path
                    )
                    report_path = Path(command[command.index("--json-report") + 1])
                if self._cancel_requested.is_set():
                    self.events.put(WorkerOutcome(action=action))
                    return
                process = self.controller.run(command, cwd=self.project_root)
                if process.cancelled or self._cancel_requested.is_set():
                    self.events.put(WorkerOutcome(action=action, process=process))
                    return
                if action == "validate" and process.returncode == 0:
                    rows = load_validation_rows(process.stdout)
                    self.events.put(
                        WorkerOutcome(action=action, process=process, validation_rows=rows)
                    )
                    return
                if action == "check" and process.returncode in (0, 1):
                    assert report_path is not None
                    report = load_report(report_path)
                    self.events.put(
                        WorkerOutcome(action=action, process=process, report=report)
                    )
                    return
                self.events.put(WorkerOutcome(action=action, process=process))
        except (GuiInputError, GuiReportError, OSError, RuntimeError) as exc:
            self.events.put(
                WorkerOutcome(
                    action=action,
                    error="" if self._cancel_requested.is_set() else str(exc),
                )
            )
        except Exception as exc:  # keep unexpected worker errors inside the GUI
            self.events.put(
                WorkerOutcome(
                    action=action,
                    error=(
                        ""
                        if self._cancel_requested.is_set()
                        else f"未预期内部错误: {type(exc).__name__}: {exc}"
                    ),
                )
            )

    def _cancel_operation(self) -> None:
        with self._cancel_guard:
            self._cancel_requested.set()
        self.status_var.set("正在取消")
        self.cancel_button.configure(state="disabled")
        threading.Thread(target=self._cancel_when_started, daemon=True).start()

    def _cancel_when_started(self) -> None:
        while True:
            with self._cancel_guard:
                if not self.running or not self._cancel_requested.is_set():
                    return
                if self.controller.cancel():
                    return
            time.sleep(0.01)

    def _poll_events(self) -> None:
        try:
            while True:
                outcome = self.events.get_nowait()
                if self._closing:
                    self._set_running(False)
                else:
                    self._handle_outcome(outcome)
        except queue.Empty:
            pass
        if self._closing and not self.running and not self.controller.is_running:
            self.root.destroy()
            return
        self.root.after(100, self._poll_events)

    def _handle_outcome(self, outcome: WorkerOutcome) -> None:
        self._set_running(False)
        process = outcome.process
        if process is not None:
            self._append_log("$ " + format_command(process.command) + "\n")
            if process.stdout:
                self._append_log(process.stdout.rstrip() + "\n")
            if process.stderr:
                self._append_log(process.stderr.rstrip() + "\n")
            self._append_log(f"[exit {process.returncode}]\n")
            if process.cancelled:
                self._reset_summary("CANCELLED")
                self.status_var.set("已取消")
                return
        if self._cancel_requested.is_set():
            self._reset_summary("CANCELLED")
            self.status_var.set("已取消")
            return
        if outcome.error:
            self._reset_summary("ERROR")
            self.status_var.set("执行失败")
            self._append_log(outcome.error + "\n")
            messagebox.showerror("执行失败", outcome.error, parent=self.root)
            return
        if process is None:
            self._reset_summary("ERROR")
            self.status_var.set("执行失败")
            return
        if outcome.action == "validate" and process.returncode == 0:
            self._render_validation(outcome.validation_rows)
            self.status_var.set(f"Excel 验证通过，共 {len(outcome.validation_rows)} 行")
            self.notebook.select(self.results_tab)
            return
        if outcome.action == "check" and process.returncode in (0, 1) and outcome.report:
            self._render_report(outcome.report)
            self.status_var.set("检查通过" if process.returncode == 0 else "检查完成，发现差异")
            self._last_json_path = self.json_report_var.get().strip()
            self._last_csv_path = self.csv_report_var.get().strip()
            self.notebook.select(self.results_tab)
            return

        self.status_var.set("执行失败")
        self._reset_summary("ERROR")
        detail = process.stderr.strip() or process.stdout.strip() or f"退出码 {process.returncode}"
        messagebox.showerror("执行失败", detail[-4000:], parent=self.root)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._set_setup_controls_disabled(running)
        state = "disabled" if running else "normal"
        self.validate_button.configure(state=state)
        self.run_button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _set_setup_controls_disabled(self, disabled: bool) -> None:
        interactive = {
            "Button",
            "Checkbutton",
            "Entry",
            "Radiobutton",
            "Spinbox",
            "TButton",
            "TCheckbutton",
            "TEntry",
            "TRadiobutton",
            "TSpinbox",
        }
        if disabled:
            self._saved_control_states.clear()
            stack = (
                list(self.setup_tab.winfo_children())
                + list(self.rules_tab.winfo_children())
                + list(self.positions_tab.winfo_children())
            )
            while stack:
                widget = stack.pop()
                stack.extend(widget.winfo_children())
                if widget.winfo_class() not in interactive:
                    continue
                try:
                    self._saved_control_states[widget] = str(widget.cget("state"))
                    widget.configure(state="disabled")
                except tk.TclError:
                    pass
            return
        for widget, state in tuple(self._saved_control_states.items()):
            try:
                if widget.winfo_exists():
                    widget.configure(state=state)
            except tk.TclError:
                pass
        self._saved_control_states.clear()

    def _clear_results(self) -> None:
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        for item in self.finding_tree.get_children():
            self.finding_tree.delete(item)
        self._result_records.clear()
        self._finding_records.clear()
        self.result_tree.column("position", width=520, stretch=False)
        self.result_tree.xview_moveto(0)
        self._set_evidence("")

    def _fit_result_position_column(self, value: str) -> None:
        try:
            required = tkfont.nametofont("TkDefaultFont").measure(value) + 24
        except Exception:
            required = len(value) * 9 + 24
        current = int(self.result_tree.column("position", "width"))
        if required > current:
            self.result_tree.column(
                "position",
                width=min(required, 2400),
                stretch=False,
            )

    def _reset_summary(self, state: str) -> None:
        self.summary_state_var.set(state)
        self.summary_rows_var.set("行数 0")
        self.summary_pass_var.set("通过 0")
        self.summary_fail_var.set("失败 0")
        self.summary_errors_var.set("错误 0")
        self.summary_warnings_var.set("警告 0")

    def _render_validation(self, rows: tuple[Mapping[str, Any], ...]) -> None:
        self._clear_results()
        self.summary_state_var.set("VALID")
        self.summary_rows_var.set(f"行数 {len(rows)}")
        self.summary_pass_var.set(f"通过 {len(rows)}")
        self.summary_fail_var.set("失败 0")
        self.summary_errors_var.set("错误 0")
        self.summary_warnings_var.set("警告 0")
        for index, spec in enumerate(rows):
            iid = f"validation-{index}"
            position_text = _position_display(spec)
            self._fit_result_position_column(position_text)
            self._result_records[iid] = {"spec": spec, "findings": [], "matched_instances": []}
            self.result_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "VALID",
                    spec.get("row", ""),
                    spec.get("Intf_type", ""),
                    position_text,
                    spec.get("RS_inst", ""),
                    spec.get("RS_CFG_EN", ""),
                    "-",
                    f"-/{spec.get('step', '')}",
                    "0E/0W",
                ),
                tags=("pass",),
            )
        children = self.result_tree.get_children()
        if children:
            self.result_tree.selection_set(children[0])

    def _render_report(self, report: LoadedReport) -> None:
        self._clear_results()
        summary = report.summary
        passed = bool(summary["passed"])
        self.summary_state_var.set("PASS" if passed else "FAIL")
        self.summary_rows_var.set(f"行数 {summary['rows']}")
        self.summary_pass_var.set(f"通过 {summary['passed_rows']}")
        self.summary_fail_var.set(f"失败 {summary['failed_rows']}")
        self.summary_errors_var.set(f"错误 {summary['errors']}")
        self.summary_warnings_var.set(f"警告 {summary['warnings']}")

        if report.global_findings:
            global_record: Mapping[str, Any] = {
                "spec": {
                    "row": "-",
                    "Intf_type": "GLOBAL",
                    "position": "",
                    "RS_inst": "",
                    "RS_CFG_EN": "",
                    "step": "",
                },
                "passed": False,
                "matched_instances": [],
                "findings": list(report.global_findings),
            }
            self._insert_result_record("global", global_record)
        for index, row in enumerate(report.rows):
            self._insert_result_record(f"row-{index}", row)
        children = self.result_tree.get_children()
        if children:
            self.result_tree.selection_set(children[0])

    def _insert_result_record(self, iid: str, record: Mapping[str, Any]) -> None:
        spec = record.get("spec", {})
        if not isinstance(spec, Mapping):
            spec = {}
        findings = record.get("findings", [])
        instances = record.get("matched_instances", [])
        findings = findings if isinstance(findings, list) or isinstance(findings, tuple) else []
        instances = instances if isinstance(instances, list) or isinstance(instances, tuple) else []
        errors = sum(
            isinstance(item, Mapping) and item.get("severity") == "error" for item in findings
        )
        warnings = sum(
            isinstance(item, Mapping) and item.get("severity") == "warning" for item in findings
        )
        passed = bool(record.get("passed", not errors))
        status = "PASS" if passed else "FAIL"
        tag = "warning" if passed and warnings else ("pass" if passed else "fail")
        step_check = record.get("step_check", {})
        if not isinstance(step_check, Mapping):
            step_check = {}
        physical_instances = step_check.get("physical_instances", len(instances))
        effective_step = step_check.get("effective_step", len(instances))
        expected_step = step_check.get("expected", spec.get("step", ""))
        effective_text = "?" if effective_step is None else str(effective_step)
        position_text = _position_display(spec)
        self._fit_result_position_column(position_text)
        self._result_records[iid] = record
        self.result_tree.insert(
            "",
            "end",
            iid=iid,
            values=(
                status,
                spec.get("row", ""),
                spec.get("Intf_type", ""),
                position_text,
                spec.get("RS_inst", ""),
                spec.get("RS_CFG_EN", ""),
                physical_instances,
                f"{effective_text}/{expected_step}",
                f"{errors}E/{warnings}W",
            ),
            tags=(tag,),
        )

    def _on_result_selected(self, _event: Any = None) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        record = self._result_records.get(selection[0])
        if record is None:
            return
        for item in self.finding_tree.get_children():
            self.finding_tree.delete(item)
        self._finding_records.clear()
        findings = record.get("findings", [])
        if not isinstance(findings, (list, tuple)):
            findings = []
        for index, finding in enumerate(findings):
            if not isinstance(finding, Mapping):
                continue
            iid = f"finding-{index}"
            self._finding_records[iid] = finding
            self.finding_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    str(finding.get("severity", "")).upper(),
                    finding.get("code", ""),
                    finding.get("instance", ""),
                    finding.get("message", ""),
                ),
            )
        self._show_record_evidence(record)
        children = self.finding_tree.get_children()
        if children:
            self.finding_tree.selection_set(children[0])

    def _on_finding_selected(self, _event: Any = None) -> None:
        selection = self.finding_tree.selection()
        if not selection:
            return
        finding = self._finding_records.get(selection[0])
        if finding is None:
            return
        result_selection = self.result_tree.selection()
        record = (
            self._result_records.get(result_selection[0], {})
            if result_selection
            else {}
        )
        self._set_evidence(
            json.dumps(
                _evidence_payload(record, finding),
                ensure_ascii=False,
                indent=2,
            )
        )

    def _show_record_evidence(self, record: Mapping[str, Any]) -> None:
        self._set_evidence(
            json.dumps(_evidence_payload(record), ensure_ascii=False, indent=2)
        )

    def _set_evidence(self, value: str) -> None:
        self.evidence_text.configure(state="normal")
        self.evidence_text.delete("1.0", "end")
        self.evidence_text.insert("1.0", value)
        self.evidence_text.configure(state="disabled")

    def _append_log(self, value: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", value)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_report_directory(self) -> None:
        candidate = self._last_json_path or self._last_csv_path or self.json_report_var.get()
        path = Path(candidate).expanduser() if candidate.strip() else self.project_root / "output"
        directory = path if path.is_dir() else path.parent
        if not directory.is_dir():
            messagebox.showerror("目录不存在", str(directory), parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(str(directory))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(directory)])
            else:
                subprocess.Popen(["xdg-open", str(directory)])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def _on_close(self) -> None:
        if self.running or self.controller.is_running:
            if not messagebox.askyesno(
                "任务仍在运行", "取消当前检查并关闭窗口？", parent=self.root
            ):
                return
            self._closing = True
            with self._cancel_guard:
                self._cancel_requested.set()
            self.status_var.set("正在取消并关闭")
            threading.Thread(target=self._cancel_when_started, daemon=True).start()
            return
        if self._module_rules_dirty and not self._confirm_discard_rule_changes():
            return
        if (
            self._position_mappings_dirty
            and not self._confirm_discard_position_changes()
        ):
            return
        self.root.destroy()


def main() -> int:
    if _TK_IMPORT_ERROR is not None or tk is None:
        print(
            "ERROR: tkinter is not available. Install python3-tk (Debian/Ubuntu) "
            "or the matching python3-tkinter package (RHEL/CentOS).",
            file=sys.stderr,
        )
        return 2
    try:
        root = tk.Tk()
    except Exception as exc:
        print(
            "ERROR: cannot open the rscheck GUI: "
            f"{exc}. Use a graphical terminal, VNC/XRDP, or ssh -Y with Xwayland/X11.",
            file=sys.stderr,
        )
        return 2
    RsCheckApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
