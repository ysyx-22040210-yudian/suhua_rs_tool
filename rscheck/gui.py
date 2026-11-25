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
    from . import gui_style as design
except ImportError as exc:  # pragma: no cover - exercised on minimal Linux installs
    tk = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_IMPORT_ERROR: Exception | None = exc
else:
    _TK_IMPORT_ERROR = None

from . import __version__
from .config import (
    config_from_dict,
    config_to_dict,
    load_complete_config,
    load_config,
    save_config,
)
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
    build_position_hierarchy_search_command,
    build_validate_command,
    default_columns,
    find_project_root,
    format_command,
    load_report,
    load_position_hierarchy_search,
    load_validation_result,
)
from .model import FIELD_NAMES, RS_INST_FUZZY_RULE, RS_INST_LIST_RULE, ModuleRule, RsCheckError, ToolConfig, blank_field_message
from .match_review import format_match_details
from .hierarchy_tree import ScopeTree
from .npi_runner import HierarchySearchResult, load_hierarchy_index


@dataclass(frozen=True)
class WorkerOutcome:
    action: str
    process: ProcessResult | None = None
    report: LoadedReport | None = None
    hierarchy_search: HierarchySearchResult | None = None
    validation_rows: tuple[Mapping[str, Any], ...] = ()
    error: str = ""
    skipped_rows: tuple[Mapping[str, Any], ...] = ()
    hierarchy_tree: ScopeTree | None = None


def _uses_ranked_names(match: Mapping[str, Any]) -> bool:
    return match.get("mode") == "RS_route" or match.get("rule") == RS_INST_FUZZY_RULE


def _item_description(item: Mapping[str, Any]) -> str:
    paths = item["matched_paths"]
    if item["status"] == "ambiguous":
        distance, ties = item["best_distance"], item["tied_best_count"]
        closest = [c["full_name"] for c in item["top_candidates"] if c["distance"] == distance]
        detail = (f"模糊匹配：{item['candidate_count']} 个同模块候选中，{ties} 个在距离 {distance} 并列，未选中。"
                  f"并列候选（显示 {len(closest)}/{ties}）：{'、'.join(closest)}")
    elif item["status"] == "no_candidates":
        detail = "无同模块候选"
    elif item["status"] == "position_not_found":
        detail = "层次不存在，未执行匹配"
    else:
        how = (f"模糊匹配（{item['candidate_count']} 个同模块候选，唯一最近，距离 {item['best_distance']}）"
               if item["rule"] == RS_INST_FUZZY_RULE else "完整名或前缀组")
        names = "、".join(paths[:3]) + (f" 等 {len(paths)} 个" if len(paths) > 3 else "")
        detail = f"{how} → {names}"
        if len(paths) > item["added_count"]:
            detail += f"（与前项重叠 {len(paths) - item['added_count']} 个，仅计一次）"
    return f"{item['query']}：{detail}"


def _evidence_payload(
    record: Mapping[str, Any], finding: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    spec = record.get("spec", {})
    evidence: dict[str, Any] = {
        "spec": spec,
        "instance_match": record.get("instance_match"),
        "module_rule": record.get("module_rule"),
        "step_check": record.get("step_check"),
        "crg_source_check": record.get("crg_source_check"),
        "source_signal_checks": record.get("source_signal_checks", []),
        "matched_instances": record.get("matched_instances", []),
    }
    match = record.get("instance_match")
    if isinstance(match, Mapping):
        if match.get("mode") == "none":
            explanation = "RS_inst 和 RS_route 均无有效值，未选择例化；拍数、实例参数和端口检查未执行。"
        elif match.get("rule") == RS_INST_LIST_RULE:
            explanation = (f"RS_inst 拆为 {len(match['items'])} 项；逐项查完整名或前缀组，未命中的项独立做模糊匹配。"
                           f"按完整 RTL 路径去重后选中 "
                           f"{match['matched_count']} 个物理实例。\n" +
                           "\n".join(f"[{idx}] {_item_description(item)}" for idx, item in enumerate(match["items"], 1)))
        elif _uses_ranked_names(match):
            reason = ("RS_inst 的完整名和组前缀均未命中，使用 RS_inst 本身模糊匹配。"
                      if match.get("mode") == "RS_inst" else "RS_inst 没有有效项，使用 RS_route。")
            explanation = (
                f"{reason}在 {match.get('position')} 中找到 {match.get('candidate_count')} 个 "
                f"{match.get('RS_module')} 实例，仅用本地例化名与 {match.get('query')!r} 比较。"
                "区分大小写，增、删、改一个字符各计 1，距离越小越接近。"
            )
            if match.get("status") == "position_not_found":
                explanation = f"未找到层次 {match.get('position')}，未执行 RS_route 匹配。"
            elif match.get("status") == "ambiguous":
                explanation += f"最小距离 {match.get('best_distance')}，有 {match.get('tied_best_count')} 个并列，未选中。"
            elif isinstance(match.get("selected"), Mapping):
                explanation += f"唯一最近实例：{match['selected'].get('full_name')}（距离 {match.get('best_distance')}）。"
            else:
                explanation += "没有同模块候选，未选中。"
            explanation += " 常规排名最多列前 5 项；更多并列候选见「匹配明细」。"
        elif match.get("status") == "position_not_found":
            explanation = f"未找到层次 {match.get('position')}，未执行 RS_inst 匹配。"
        else:
            explanation = "已填写 RS_inst，按完整本地名称或前缀加配置的后缀规则匹配。"
        if match.get("route_ignored"):
            explanation += " 本行填写的 RS_route 不参与选择。"
        evidence = {"例化匹配说明": explanation, **evidence}
    if isinstance(spec, Mapping):
        evidence["crg_source_resolution"] = _crg_source_display(spec)
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


def _instance_display(spec: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    match = record.get("instance_match")
    if isinstance(match, Mapping) and match.get("mode") == "none":
        return "未指定例化（未计拍）"
    items = spec.get("RS_inst_items")
    if items and len(items) > 1:
        names = "；".join(items)
        if not isinstance(match, Mapping):
            return f"{len(items)} 项：{names}（待 RTL 匹配）"
        results = match["items"]
        fuzzy_hits = sum(item["rule"] == RS_INST_FUZZY_RULE and item["status"] == "matched" for item in results)
        missing = sum(item["status"] != "matched" for item in results)
        labels = []
        if fuzzy_hits:
            labels.append(f"{fuzzy_hits} 项模糊命中")
        if missing:
            labels.append(f"{missing} 项未选中")
        return " · ".join([*labels, f"{names} → {match['matched_count']} 个实例"])
    explicit_name = items[0] if items else (spec.get("RS_inst") if items is None else "")
    if explicit_name:
        if not isinstance(match, Mapping) or match.get("rule") != RS_INST_FUZZY_RULE:
            return str(explicit_name)
        label = f"RS_inst 模糊: {match['query']}"
    elif spec.get("RS_route") and "RS_route" not in spec.get("ignored_fields", {}):
        label = f"RS_route: {spec['RS_route']}"
    else:
        return ""
    if not isinstance(match, Mapping):
        return f"{label}（待 RTL 匹配）"
    selected = match.get("selected")
    if isinstance(selected, Mapping):
        return f"{label} → {selected.get('name')}（距离 {selected.get('distance')}）"
    reason = {"ambiguous": "最近候选并列", "no_candidates": "无同模块实例",
              "position_not_found": "层次不存在"}.get(match.get("status"), "未选中")
    return f"{label}（{reason}）"


def _review_sections(
    record: Mapping[str, Any], payload: Mapping[str, Any],
    finding: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """Concise human-readable inspector; the original evidence remains available."""
    spec = payload["spec"]
    findings = record.get("findings", [])
    is_global = spec.get("row") == "-" and spec.get("Intf_type") == "GLOBAL"
    sections = []
    if finding:
        lines = [str(finding.get("message", ""))]
        for key, label in (("expected", "期望"), ("actual", "实际")):
            value = finding.get(key)
            if isinstance(value, (str, int, float, bool)):
                lines.append(f"{label}：{value}")
        sections.append((f"{str(finding.get('severity', '')).upper()}   /   {finding.get('code', '')}", "\n".join(lines)))
        if "passed" not in record:
            sections.append(("Excel 已验证", "此步骤仅验证规格表，尚未检查 RTL。"))
    elif is_global:
        sections.append(("全局诊断", "从左侧选择一项查看原因，原始 JSON 保留完整证据。"))
    elif "passed" not in record:
        sections.append(("Excel 已验证", "此步骤仅验证规格表。运行 RTL 检查后可查看拍数和实例证据。"))
    else:
        step = record.get("step_check") or {}
        # Report v2 has only a physical count; mirror the legacy table column.
        actual = step.get("effective_step", len(record.get("matched_instances", [])))
        title = ("需处理" if not record["passed"] else "有警告" if findings else "仅检查层次和模块"
                 if (record.get("instance_match") or {}).get("mode") == "none" else "检查通过")
        sections.append((title, f"实际 {'未确定' if actual is None else actual} / 期望 {spec.get('step', '—')} 拍"
                         f"     ·     匹配 {len(record.get('matched_instances', []))} 个物理实例"))
    if not is_global:
        ignored = spec.get("ignored_fields", {})
        if ignored:
            lines = [f"{field}：{'空值（警告）' if reason == 'blank' else 'NA（不报警）'}" for field, reason in ignored.items()]
            if "CRG_source" in ignored and any(role not in ignored for role in ("clk", "rst")):
                lines.append("没有目标 CRG 层次：仍检查已填写的 RS 端口，不比对源端信号名。")
            sections.append(("本行跳过的字段", "\n".join(lines)))
        scope = _position_display(spec)
        if scope:
            sections.append(("检查对象", f"{spec.get('RS_module', '')}   /   {scope}"))
        match = record.get("instance_match") or {}
        if match.get("rule") == RS_INST_LIST_RULE:
            sections.append(("例化匹配 · RS_inst 多项", payload["例化匹配说明"]))
        elif _uses_ranked_names(match):
            selected = match.get("selected") or {}
            result = selected.get("full_name") or {
                "ambiguous": "最小距离并列，未选择实例", "position_not_found": "层次不存在",
                "no_candidates": "无同模块候选",
            }.get(match.get("status"), "未选中")
            detail = f"{match.get('candidate_count', 0)} 个候选"
            if match.get("best_distance") is not None:
                detail += f"   ·   最小编辑距离 {match['best_distance']}   ·   最近候选 {match['tied_best_count']} 个"
            is_fallback = match.get("mode") == "RS_inst"
            title = "例化匹配 · RS_inst 模糊回退" if is_fallback else "例化匹配 · RS_route"
            if is_fallback:
                detail = "完整名和组前缀均未命中。 " + detail
                if match.get("route_ignored"):
                    detail += "；RS_route 未参与。"
            sections.append((title, f"{match['query']} → {result}\n{detail}"))
        elif payload.get("例化匹配说明"):
            sections.append(("例化匹配 · RS_inst", payload["例化匹配说明"]))
        if match.get("top_candidates"):
            sections.append(("最近候选 · 字符编辑距离", "\n".join(
                f"{c['distance']}    {c['full_name']}" for c in match["top_candidates"])))
        elif record.get("matched_instances"):
            names = [i.get("full_name", "") for i in record["matched_instances"][:3]]
            if len(record["matched_instances"]) > len(names):
                names.append("更多实例见原始 JSON")
            sections.append(("选中的实例", "\n".join(names)))
    signals = record.get("source_signal_checks") or []
    if signals:
        labels = {"matched": "匹配", "mismatch": "不匹配", "unavailable": "证据不足",
                  "invalid_start": "起点端口无效", "depth_limited": "达到深度上限", "not_found": "未到达目标"}
        lines = []
        for check in signals:
            lines.append(f"{check['instance']}.{check['start_port']} · {check['role']} 期望 {check['expected']} · "
                         f"{labels[check['status']]}")
            if not check["endpoints"]:
                lines.append(f"  {check['target']}：无已确认的源端连线")
            for e in check["endpoints"]:
                lines.append(f"  {' → '.join(e['path'])} · {e['port'] or '?'} 接 {e['connection'] or '?'}")
        sections.append(("CRG 源端连线 · clk / rst", "\n".join(lines)))
    return sections


def _parse_step_parameters(value: str) -> tuple[str, ...]:
    parameters = [item.strip() for item in value.replace("，", ",").split(",")]
    parameters = [item for item in parameters if item]
    if len(set(parameters)) != len(parameters):
        raise GuiInputError("决定 step 的 parameter 名不能重复")
    if "RS_CRG_EN" in parameters:
        raise GuiInputError("RS_CRG_EN 不能同时作为决定 step 的 parameter")
    return tuple(parameters)


def _module_rule_from_form(
    module_name: str,
    has_rs_cfg_en: bool,
    step_parameters: str,
    clk_port: str = "",
    rst_port: str = "",
) -> ModuleRule:
    name = module_name.strip()
    if not name:
        raise GuiInputError("RS_module 名不能为空")
    if any(character.isspace() for character in name):
        raise GuiInputError("RS_module 名不能包含空白字符")
    parameters = _parse_step_parameters(step_parameters)
    if any(any(character.isspace() for character in item) for item in parameters):
        raise GuiInputError("parameter 名不能包含空白字符")
    normalized_clk_port = clk_port.strip() or "clk"
    normalized_rst_port = rst_port.strip() or "rst_n"
    for label, port_name in (
        ("clk", normalized_clk_port),
        ("rst", normalized_rst_port),
    ):
        if any(character.isspace() for character in port_name):
            raise GuiInputError(f"{label} 端口名不能包含空白字符")
    return ModuleRule(
        name=name,
        has_rs_cfg_en=bool(has_rs_cfg_en),
        step_parameters=parameters,
        clk_port=normalized_clk_port,
        rst_port=normalized_rst_port,
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


def _crg_source_mapping_from_form(
    short_name: str, rtl_path: str
) -> tuple[str, str]:
    alias = short_name.strip()
    full_path = rtl_path.strip()
    if not alias:
        raise GuiInputError("CRG_source 简写不能为空")
    if alias.startswith(".") or alias.endswith("."):
        raise GuiInputError("CRG_source 简写不能以 '.' 开头或结尾")
    if not full_path:
        raise GuiInputError("CRG Source RTL 层次全路径不能为空")
    if not full_path.strip("."):
        raise GuiInputError("CRG Source RTL 层次全路径不能仅由 '.' 组成")
    return alias, full_path


def _crg_source_display(spec: Mapping[str, Any]) -> str:
    crg_source = str(spec.get("CRG_source", ""))
    alias = spec.get("crg_source_alias", "")
    if isinstance(alias, str) and alias:
        return f"{alias} -> {crg_source}"
    return crg_source


def _crg_trace_display(record: Mapping[str, Any]) -> str:
    check = record.get("crg_source_check")
    signals = record.get("source_signal_checks", ())
    if signals and record.get("source_signal_status"):
        return record["source_signal_status"].upper()
    if not isinstance(check, Mapping):
        return "N/A"
    status = check.get("status")
    if not isinstance(status, str) or not status.strip():
        return "N/A"
    return status.strip().upper()


def _resolved_config_path(value: str | Path) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except (OSError, RuntimeError) as exc:
        raise GuiInputError(f"无法解析配置路径 {value!s}: {exc}") from exc


def _config_path_identity(value: str | Path) -> str:
    return os.path.normcase(_resolved_config_path(value))


def _same_config_path(
    left: str | Path,
    right: str | Path,
    *,
    include_hardlinks: bool = False,
) -> bool:
    if _config_path_identity(left) == _config_path_identity(right):
        return True
    if not include_hardlinks:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


class RsCheckApp:
    def __init__(self, root: Any) -> None:
        self.root = root
        self.project_root = find_project_root()
        self.controller = ProcessController()
        self.events: queue.Queue[WorkerOutcome] = queue.Queue()
        self._closing = False
        self._running = False
        self._active_action = ""
        self._cancel_requested = threading.Event()
        self._cancel_guard = threading.Lock()
        self._result_records: dict[str, Mapping[str, Any]] = {}
        self._finding_records: dict[str, Mapping[str, Any]] = {}
        self._issue_rows: set[str] = set()
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
        self._position_hierarchy_dialog: Any | None = None
        self._position_hierarchy_tree: Any | None = None
        self._position_hierarchy_paths: dict[str, str] = {}
        self._position_hierarchy_close_pending = False
        self._hierarchy_target = "position"
        self._hierarchy_temp: tempfile.TemporaryDirectory | None = None
        self._hierarchy_model: ScopeTree | None = None
        self._hierarchy_children: dict[str, list[str]] = {}
        self._hierarchy_offsets: dict[str, int] = {}
        self._hierarchy_more: dict[str, str] = {}
        self._hierarchy_hits: set[str] = set()
        self._crg_source_mappings: dict[str, str] = {}
        self._crg_source_mappings_dirty = False
        self._editing_crg_source_alias = ""
        self._crg_source_tree_aliases: dict[str, str] = {}

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
        self.root.title("RS Studio · RTL 打拍例化检查工具")
        # Leave room for the desktop panels on common 1366x768 VM desktops.
        w = max(980, min(1240, self.root.winfo_screenwidth() - 44))
        h = max(680, min(800, self.root.winfo_screenheight() - 84))
        self.root.geometry(f"{w}x{h}")
        self.root.minsize(980, 680)
        self.root.option_add("*tearOff", False)

        self.ui = design.StudioTheme(self.root)

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
        self.crg_trace_max_depth_var = tk.StringVar(value="16")
        self.column_vars = {
            name: tk.StringVar(value=value) for name, value in default_columns().items()
        }
        self.module_search_var = tk.StringVar()
        self.module_name_var = tk.StringVar()
        self.module_has_rs_cfg_en_var = tk.BooleanVar(value=False)
        self.module_step_parameters_var = tk.StringVar()
        self.module_clk_port_var = tk.StringVar(value="clk")
        self.module_rst_port_var = tk.StringVar(value="rst_n")
        self.module_rule_status_var = tk.StringVar(value="规则 0")
        self.position_search_var = tk.StringVar()
        self.position_alias_var = tk.StringVar()
        self.position_path_var = tk.StringVar()
        self.position_mapping_status_var = tk.StringVar(value="映射 0")
        self.position_hierarchy_regex_var = tk.StringVar()
        self.position_hierarchy_status_var = tk.StringVar(value="尚未查询")
        self.crg_source_search_var = tk.StringVar()
        self.crg_source_alias_var = tk.StringVar()
        self.crg_source_path_var = tk.StringVar()
        self.crg_source_mapping_status_var = tk.StringVar(value="映射 0")

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
        self.only_issues_var = tk.BooleanVar(value=False)
        self.advanced_options_var = tk.BooleanVar(value=False)
        self.result_count_var = tk.StringVar()
        self.summary_state_var = tk.StringVar(value="未运行")
        self.summary_rows_var = tk.StringVar(value="行数 0")
        self.summary_pass_var = tk.StringVar(value="通过 0")
        self.summary_fail_var = tk.StringVar(value="失败 0")
        self.summary_errors_var = tk.StringVar(value="错误 0")
        self.summary_warnings_var = tk.StringVar(value="警告 0")

    def _build_ui(self) -> None:
        self.root.configure(background=design.PAGE)
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(1, weight=1)

        self.notebook = ttk.Notebook(self.root, style="Workspace.TNotebook", takefocus=False)
        self.notebook.grid(row=1, column=1, sticky="nsew", padx=(16, 14), pady=(12, 10))
        (self.setup_tab, self.rules_tab, self.positions_tab, self.crg_sources_tab,
         self.results_tab, self.log_tab) = [ttk.Frame(self.notebook, style="Page.TFrame") for _ in range(6)]
        self._pages = (
            (self.setup_tab, "setup", "检查配置", "检查配置", "工作区", "配置规格表、列映射和 RTL 数据来源。"),
            (self.results_tab, "results", "检查结果", "检查结果", "工作区", "查看有效拍数，定位异常并核对例化匹配证据。"),
            (self.log_tab, "log", "运行日志", "运行日志", "工作区", "追溯本次会话的实际命令、输出和退出码。"),
            (self.rules_tab, "rules", "模块规则库", "模块规则库", "数据库", "维护各类 RS 模块的端口、门控与拍数计算规则。"),
            (self.positions_tab, "positions", "Position 映射库", "Position 映射", "数据库", "把 Excel 简写映射到 RTL 全路径，支持从 Verdi 查询。"),
            (self.crg_sources_tab, "crg", "CRG Source映射库", "CRG 映射", "数据库", "维护时钟来源简称与 RTL 实例全路径的对应关系。"),
        )
        for page, _icon, title, *_ in self._pages:
            self.notebook.add(page, text=title)
        self._build_navigation()
        self._build_page_header()

        self._build_setup_tab()
        self._build_rules_tab()
        self._build_positions_tab()
        self._build_crg_sources_tab()
        self._build_results_tab()
        self._build_log_tab()
        self._build_status_bar()
        self.notebook.bind("<<NotebookTabChanged>>", self._sync_navigation)
        self._sync_navigation()

    def _build_navigation(self) -> None:
        rail = tk.Frame(self.root, width=166, background=design.NAV)
        rail.grid(row=0, column=0, rowspan=3, sticky="nsew")
        rail.pack_propagate(False)
        brand = tk.Frame(rail, bg=design.NAV)
        brand.pack(fill="x", padx=13, pady=(20, 0))
        tk.Label(brand, image=self.ui.mark, bg=design.NAV, borderwidth=0).pack(side="left", padx=(0, 7))
        tk.Label(brand, text="RS Studio", bg=design.NAV, fg=design.INK,
                 font=(self.ui.wordmark, 16, "normal")).pack(side="left")
        tk.Label(rail, text="RTL 验证工作台", bg=design.NAV, fg=design.MUTED,
                 font=self.ui.f(9)).pack(anchor="w", padx=16, pady=(5, 16))
        tk.Frame(rail, height=1, bg=design.LINE).pack(fill="x", padx=14)
        self.nav_buttons = {}
        last_group = None
        for page, icon, _title, label, group, _hint in self._pages:
            if group != last_group:
                tk.Label(rail, text=group, bg=design.NAV, fg=design.MUTED, font=self.ui.f(9)).pack(
                    anchor="w", padx=16, pady=(17, 5))
                last_group = group
            button = ttk.Button(rail, text=label, image=self.ui.icon(icon), compound="left",
                                style="Nav.TButton", command=lambda p=page: self.notebook.select(p))
            button.pack(fill="x", padx=7, pady=1)
            self.nav_buttons[str(page)] = button
        foot = tk.Frame(rail, bg=design.NAV)
        foot.pack(side="bottom", fill="x", padx=15, pady=14)
        scope = tk.Canvas(foot, bg=design.NAV, height=30, width=125, highlightthickness=0)
        scope.pack(fill="x", pady=(0, 5))
        for y in (6, 17, 27):
            scope.create_line(0,y,136,y, fill=design.LINE, dash=(2,4))
        scope.create_line(0,23,18,23,18,11,41,11,41,22,65,22,65,6,86,6,86,17,112,17,112,10,135,10,
                          width=2, fill=design.CLAY)
        tk.Label(foot, text="配置文件", bg=design.NAV, fg=design.MUTED, font=self.ui.f(9)).pack(anchor="w")
        current = tk.Label(foot, bg=design.NAV, fg=design.INK, font=self.ui.code(9), anchor="w")
        current.pack(fill="x", pady=(2, 5))
        filename_font = tkfont.Font(root=self.root, font=self.ui.code(9))
        def sync_file(*_):
            name = Path(self.config_var.get()).name
            tail = name
            while tail and filename_font.measure(tail if tail == name else "…" + tail) > 126:
                tail = tail[1:]
            current.configure(text=(tail if tail == name else "…" + tail) or "未选择")
        self.config_var.trace_add("write", sync_file)
        sync_file()
        tk.Frame(foot, height=1, bg=design.LINE).pack(fill="x", pady=(4, 7))
        tk.Label(foot, text=f"RS CHECKER  /  v{__version__}", anchor="w",
                 bg=design.NAV, fg=design.MUTED, font=self.ui.code(8)).pack(fill="x")

    def _build_page_header(self) -> None:
        head = ttk.Frame(self.root, style="Page.TFrame", padding=(20, 11, 18, 3))
        head.grid(row=0, column=1, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.page_group = ttk.Label(head, style="Page.Muted.TLabel")
        self.page_group.grid(row=0, column=0, sticky="w")
        self.page_title = ttk.Label(head, style="Page.Title.TLabel")
        self.page_title.grid(row=1, column=0, sticky="w")
        self.page_hint = ttk.Label(head, style="Page.Muted.TLabel")
        self.page_hint.grid(row=2, column=0, sticky="w")
        self.mode_badge = ttk.Label(head, style="Badge.TLabel")
        self.mode_badge.grid(row=0, column=1, rowspan=3, sticky="e")

    def _sync_navigation(self, _event=None) -> None:
        selected = self.notebook.select()
        for page, _icon, title, _label, group, hint in self._pages:
            active = str(page) == selected
            self.nav_buttons[str(page)].configure(style="Selected.Nav.TButton" if active else "Nav.TButton")
            if active:
                self.page_group.configure(text=f"RTL 检查   /   {group}")
                self.page_title.configure(text=title.replace("Source映", "Source 映"))
                self.page_hint.configure(text=hint)

    def _build_setup_tab(self) -> None:
        self.setup_form = design.ScrollForm(self.setup_tab)
        tab = self.setup_form.body
        tab.columnconfigure(0, weight=1)
        sheet_card, input_group = self.ui.card(tab, "01", "规格与配置", "Excel 公式请先重算并保存")
        sheet_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
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
        (
            self.config_import_button,
            self.config_reload_button,
            self.config_export_button,
        ) = self._path_row(
            input_group,
            1,
            "配置 JSON",
            self.config_var,
            self._import_config,
            button_text="导入",
            extra_buttons=(
                ("加载", self._load_config_from_form),
                ("导出", self._export_config),
            ),
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
        ttk.Spinbox(
            excel_options,
            from_=1,
            to=100000,
            textvariable=self.header_row_var,
            width=5,
        ).grid(row=0, column=3, sticky="w", padx=(8, 18))
        ttk.Label(excel_options, text="数据起始行").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(
            excel_options,
            from_=1,
            to=100000,
            textvariable=self.data_start_row_var,
            width=5,
        ).grid(row=0, column=5, sticky="w", padx=(8, 18))
        ttk.Checkbutton(
            excel_options,
            text="严格校验表头（可选）",
            variable=self.header_check_var,
        ).grid(row=0, column=6, sticky="e")

        middle = ttk.Frame(tab, style="Page.TFrame")
        middle.grid(row=1, column=0, sticky="ew")
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2)
        columns_card, columns_group = self.ui.card(middle, "03", "列映射", "从 1 开始")
        columns_group.columnconfigure(1, weight=1)
        columns_group.columnconfigure(3, weight=1)
        rows_per_block = (len(FIELD_NAMES) + 1) // 2
        for index, name in enumerate(FIELD_NAMES):
            row = index % rows_per_block
            block = index // rows_per_block
            label_column = block * 2
            ttk.Label(columns_group, text="RS_route（可选）" if name == "RS_route" else name).grid(
                row=row,
                column=label_column,
                sticky="w",
                padx=(0 if block == 0 else 18, 8),
                pady=4,
            )
            ttk.Spinbox(
                columns_group,
                from_=1,
                to=16384,
                textvariable=self.column_vars[name],
                width=4,
            ).grid(row=row, column=label_column + 1, sticky="w", pady=4)
        ttk.Label(columns_group, text="RS_inst 分隔：空格、Tab、换行、分号、逗号、顿号、冒号。", style="Muted.TLabel").grid(
            row=rows_per_block, column=0, columnspan=4, sticky="w", pady=(5, 0))

        source_card, self.source_group = self.ui.card(middle, "02", "RTL 数据来源")
        def place_cards(_event=None):
            # Test the viewport, not the card size: hiding the scrollbar must
            # not cause a stack/unstack loop near the breakpoint.
            minimum = max(780, source_card.winfo_reqwidth() + columns_card.winfo_reqwidth() + 28)
            stacked = self.setup_tab.winfo_width() < minimum
            source_card.grid(row=0, column=0, columnspan=2 if stacked else 1, sticky="nsew",
                             padx=(0, 0 if stacked else 5))
            columns_card.grid(row=1 if stacked else 0, column=0 if stacked else 1,
                              columnspan=2 if stacked else 1, sticky="nsew",
                              padx=(0 if stacked else 5, 0), pady=(10 if stacked else 0, 0))
        self.setup_tab.bind("<Configure>", place_cards, add="+")
        self._arrange_setup = place_cards
        place_cards()
        self.source_group.columnconfigure(0, weight=1)
        self.source_group.rowconfigure(1, weight=1)
        mode_bar = ttk.Frame(self.source_group)
        mode_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Radiobutton(
            mode_bar,
            text="在线 NPI",
            style="Segment.TRadiobutton",
            variable=self.source_mode_var,
            value=LIVE_SOURCE,
            command=self._update_source_mode,
        ).pack(side="left", padx=(0, 4))
        ttk.Radiobutton(
            mode_bar,
            text="离线 Inventory",
            style="Segment.TRadiobutton",
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
        ttk.Label(self.live_frame, text="使用已展开的 *.elab++ 库", style="Muted.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(8,0))
        advanced_toggle = ttk.Checkbutton(self.live_frame, text="高级采集设置", variable=self.advanced_options_var)
        advanced_toggle.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8,2))
        self.live_advanced = ttk.Frame(self.live_frame)
        self.live_advanced.columnconfigure(1, weight=1)
        self._compact_path_row(
            self.live_advanced,
            0,
            "NPI 库目录",
            self.npi_lib_var,
            lambda: self._choose_directory(self.npi_lib_var),
        )
        self._compact_path_row(
            self.live_advanced,
            1,
            "保存 Inventory",
            self.keep_inventory_var,
            lambda: self._choose_save(
                self.keep_inventory_var, ".json", (("JSON", "*.json"),)
            ),
        )
        ttk.Label(self.live_advanced, text="超时（秒）").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Spinbox(
            self.live_advanced,
            from_=1,
            to=86400,
            textvariable=self.npi_timeout_var,
            width=10,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=5)
        def show_advanced(*_):
            if self.advanced_options_var.get():
                self.live_advanced.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(2,0))
            else:
                self.live_advanced.grid_remove()
            self.root.after_idle(place_cards)
        self.advanced_options_var.trace_add("write", show_advanced)

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
        ttk.Button(self.inventory_frame, text="使用示例 Inventory", style="Quiet.TButton",
                   command=lambda: self.inventory_var.set(str(self.project_root / "examples" / "inventory.json"))
                   ).grid(row=1, column=1, sticky="w", pady=(0, 4))

        trace_options = ttk.Frame(self.source_group)
        trace_options.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(trace_options, text="CRG Trace 最大层数").pack(side="left")
        ttk.Spinbox(
            trace_options,
            from_=1,
            to=256,
            textvariable=self.crg_trace_max_depth_var,
            width=8,
        ).pack(side="left", padx=(8, 0))

        output_card, output_group = self.ui.card(tab, "04", "报告输出")
        output_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
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


    def _database_list(self, tab, search_label, query, status):
        edge = ttk.Frame(tab, style="Surface.TFrame", padding=(10, 8))
        edge.grid(row=0, column=0, rowspan=2, sticky="nsew")
        toolbar = ttk.Frame(edge, padding=(12, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=search_label, style="CardTitle.TLabel").pack(side="left")
        ttk.Entry(toolbar, textvariable=query, width=26).pack(side="left", padx=9)
        count = ttk.Label(toolbar, textvariable=status, style="Badge.TLabel")
        count.pack(side="right")
        status.trace_add("write", lambda *_: count.configure(
            style="Dirty.Badge.TLabel" if "未保存" in status.get() else "Badge.TLabel"))
        frame = ttk.Frame(edge)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame

    def _build_rules_tab(self) -> None:
        tab = self.rules_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        tree_frame = self._database_list(
            tab, "搜索模块", self.module_search_var, self.module_rule_status_var
        )
        self.rule_tree = ttk.Treeview(
            tree_frame,
            columns=(
                "module",
                "rs_cfg_en",
                "clk_port",
                "rst_port",
                "step_parameters",
            ),
            show="headings",
            selectmode="browse",
        )
        # Start below the available width at 980px. Older ttk builds only
        # expand a stretch column on first map; they won't shrink an oversized one.
        for name, title, width, minimum in (
            ("module", "RS_module", 180, 105),
            ("rs_cfg_en", "RS_CRG_EN", 110, 85),
            ("clk_port", "clk 端口", 95, 70),
            ("rst_port", "rst 端口", 95, 70),
            ("step_parameters", "决定 step 的 parameters", 225, 135),
        ):
            self.rule_tree.heading(name, text=title, anchor="w")
            self.rule_tree.column(
                name,
                width=width,
                minwidth=minimum,
                stretch=name == "step_parameters",
            )
        rule_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.rule_tree.yview
        )
        self.rule_tree.configure(yscrollcommand=rule_scroll.set)
        self.rule_tree.grid(row=0, column=0, sticky="nsew")
        rule_scroll.grid(row=0, column=1, sticky="ns")
        self.rule_tree.bind("<<TreeviewSelect>>", self._on_rule_selected)

        edge, editor = self.ui.card(tab, "DB", "规则编辑", "应用修改后，再保存到配置")
        edge.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        ttk.Label(editor, text="RS_module").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.module_name_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Checkbutton(
            editor,
            text="有 RS_CRG_EN",
            variable=self.module_has_rs_cfg_en_var,
        ).grid(row=0, column=2, columnspan=2, sticky="w")
        ttk.Label(editor, text="clk 端口").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(editor, textvariable=self.module_clk_port_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 18), pady=(8, 0)
        )
        ttk.Label(editor, text="rst 端口").grid(
            row=1, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Entry(editor, textvariable=self.module_rst_port_var).grid(
            row=1, column=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Label(editor, text="step parameters").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(editor, textvariable=self.module_step_parameters_var).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0)
        )

        actions = ttk.Frame(tab, style="Page.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="新建", style="Page.TButton", command=self._new_rule).pack(side="left")
        ttk.Button(actions, text="应用修改", style="Page.TButton", command=self._apply_rule).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="删除", style="Page.Danger.TButton", command=self._delete_rule).pack(side="left")
        ttk.Button(actions, text="保存规则库", style="Page.Primary.TButton", command=self._save_module_rules).pack(
            side="right"
        )
        self.module_search_var.trace_add("write", lambda *_: self._render_rule_tree())

    def _build_positions_tab(self) -> None:
        tab = self.positions_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        tree_frame = self._database_list(
            tab, "搜索 Position", self.position_search_var, self.position_mapping_status_var
        )
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
            self.position_tree.heading(name, text=title, anchor="w")
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

        edge, editor = self.ui.card(tab, "DB", "Position 映射编辑", "选中表格行即可编辑")
        edge.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=3)
        ttk.Label(editor, text="position 简写").grid(row=0, column=0, sticky="w")
        ttk.Entry(editor, textvariable=self.position_alias_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(editor, text="RTL 层次全路径").grid(row=0, column=2, sticky="w")
        ttk.Entry(editor, textvariable=self.position_path_var).grid(
            row=0, column=3, sticky="ew", padx=(8, 8)
        )
        self.position_hierarchy_pick_button = ttk.Button(
            editor,
            text="选择 DUT 层次…",
            style="Quiet.TButton",
            command=self._open_position_hierarchy_search,
        )
        self.position_hierarchy_pick_button.grid(
            row=0, column=4, sticky="e"
        )

        actions = ttk.Frame(tab, style="Page.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="新建", style="Page.TButton", command=self._new_position_mapping).pack(
            side="left"
        )
        ttk.Button(
            actions, text="应用修改", style="Page.TButton", command=self._apply_position_mapping
        ).pack(side="left", padx=8)
        ttk.Button(
            actions, text="删除", style="Page.Danger.TButton", command=self._delete_position_mapping
        ).pack(side="left")
        ttk.Button(
            actions, text="保存映射库", style="Page.Primary.TButton", command=self._save_position_mappings
        ).pack(side="right")
        self.position_search_var.trace_add(
            "write", lambda *_: self._render_position_tree()
        )

    def _open_position_hierarchy_search(self, target: str = "position") -> None:
        if self.running or self.controller.is_running:
            return
        existing = self._position_hierarchy_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass

        request = self._request()
        try:
            build_position_hierarchy_search_command(request, ".*")
            session_dir = tempfile.TemporaryDirectory(prefix="rscheck-dut-tree-")
        except (GuiInputError, OSError) as exc:
            messagebox.showerror("无法浏览 DUT 层次",
                                 f"请先在「检查配置」选择在线 NPI、collector 和 Verdi *.elab++。\n{exc}",
                                 parent=self.root)
            return

        self._hierarchy_target = target
        self._hierarchy_temp = session_dir
        self._hierarchy_model = None
        self.position_hierarchy_regex_var.set("")
        dialog = tk.Toplevel(self.root)
        self._position_hierarchy_dialog = dialog
        self._position_hierarchy_close_pending = False
        self._position_hierarchy_paths.clear()
        self.position_hierarchy_status_var.set("尚未查询")
        dialog.title(f"{target} · DUT 层次树")
        dialog.geometry("980x560")
        dialog.minsize(760, 420)
        dialog.configure(background=design.PAGE)
        dialog.transient(self.root)
        dialog.rowconfigure(2, weight=1)
        dialog.columnconfigure(0, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", self._close_position_hierarchy_search)

        header = ttk.Frame(dialog, style="Page.TFrame", padding=(18, 12))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=f"选择 {target} 的 DUT 全路径", style="Page.Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="展开分支浏览，或按完整路径筛选。选中节点后点击「使用所选路径」回填。",
                  style="Page.Muted.TLabel").pack(anchor="w")
        toolbar = ttk.Frame(dialog, style="Surface.TFrame", padding=(15, 11))
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="全路径正则").grid(row=0, column=0, sticky="w")
        self.position_hierarchy_query_entry = ttk.Entry(
            toolbar, textvariable=self.position_hierarchy_regex_var
        )
        self.position_hierarchy_query_entry.grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        self.position_hierarchy_query_entry.bind(
            "<Return>", lambda _event: self._start_position_hierarchy_search()
        )
        self.position_hierarchy_search_button = ttk.Button(
            toolbar, text="筛选", command=self._start_position_hierarchy_search
        )
        self.position_hierarchy_search_button.grid(row=0, column=2)
        self.hierarchy_clear_button = ttk.Button(toolbar, text="清除筛选", command=self._clear_hierarchy_filter)
        self.hierarchy_clear_button.grid(row=0, column=3, padx=(6, 0))
        ttk.Label(toolbar, textvariable=self.position_hierarchy_status_var, style="Badge.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0)
        )

        tree_frame = ttk.Frame(dialog, style="Surface.TFrame", padding=10)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            tree_frame,
            columns=("path", "definition", "kind"),
            show="tree headings",
            selectmode="browse",
        )
        self._position_hierarchy_tree = tree
        for name, title, width in (
            ("#0", "DUT 分支", 255),
            ("path", "RTL 层次全路径", 420),
            ("definition", "模块定义", 145),
            ("kind", "类型", 95),
        ):
            tree.heading(name, text=title, anchor="w")
            tree.column(name, width=width, minwidth=90, stretch=name == "path")
        vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(
            tree_frame, orient="horizontal", command=tree.xview
        )
        tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.bind(
            "<<TreeviewSelect>>", self._on_position_hierarchy_candidate_selected
        )
        tree.tag_configure("hit", foreground=design.CLAY_INK)
        tree.bind("<<TreeviewOpen>>", self._on_hierarchy_open)
        tree.bind("<Double-1>", self._on_hierarchy_double_click)
        tree.bind("<Return>", self._on_hierarchy_return)

        actions = ttk.Frame(dialog, padding=10)
        actions.grid(row=3, column=0, sticky="ew")
        self.position_hierarchy_cancel_button = ttk.Button(
            actions,
            text="取消查询",
            state="disabled",
            command=self._cancel_operation,
        )
        self.position_hierarchy_cancel_button.pack(side="left")
        self.position_hierarchy_close_button = ttk.Button(
            actions, text="关闭", command=self._close_position_hierarchy_search
        )
        self.position_hierarchy_close_button.pack(side="right")
        self.position_hierarchy_use_button = ttk.Button(
            actions,
            text="使用所选路径",
            state="disabled",
            style="Primary.TButton",
            command=self._use_selected_position_hierarchy,
        )
        self.position_hierarchy_use_button.pack(side="right", padx=(0, 8))
        dialog.grab_set()
        self.position_hierarchy_query_entry.focus_set()
        self._start_position_hierarchy_search()

    def _clear_hierarchy_filter(self) -> None:
        self.position_hierarchy_regex_var.set("")
        self._start_position_hierarchy_search()

    def _destroy_position_hierarchy_search(self) -> None:
        dialog = self._position_hierarchy_dialog
        self._position_hierarchy_dialog = None
        self._position_hierarchy_tree = None
        self._position_hierarchy_paths.clear()
        self._hierarchy_model = None
        self._hierarchy_children = {}
        self._hierarchy_hits.clear()
        self._hierarchy_offsets.clear()
        self._hierarchy_more.clear()
        if self._hierarchy_temp is not None:
            self._hierarchy_temp.cleanup()
            self._hierarchy_temp = None
        self._position_hierarchy_close_pending = False
        if dialog is None:
            return
        try:
            if dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()
        except tk.TclError:
            pass

    def _close_position_hierarchy_search(self) -> None:
        if self.running or self.controller.is_running:
            self._position_hierarchy_close_pending = True
            dialog = self._position_hierarchy_dialog
            if dialog is not None:
                try:
                    dialog.grab_release()
                    dialog.withdraw()
                except tk.TclError:
                    pass
            self._cancel_operation()
            return
        self._destroy_position_hierarchy_search()

    def _set_position_hierarchy_search_running(self, running: bool) -> None:
        dialog = self._position_hierarchy_dialog
        if dialog is None:
            return
        try:
            if not dialog.winfo_exists():
                return
            self.position_hierarchy_query_entry.configure(
                state="disabled" if running else "normal"
            )
            self.position_hierarchy_search_button.configure(
                state="disabled" if running else "normal"
            )
            self.hierarchy_clear_button.configure(state="disabled" if running else "normal")
            self.position_hierarchy_cancel_button.configure(
                state="normal" if running else "disabled"
            )
            if running:
                self.position_hierarchy_use_button.configure(state="disabled")
            else:
                self._on_position_hierarchy_candidate_selected()
        except tk.TclError:
            pass

    def _clear_position_hierarchy_candidates(self) -> None:
        tree = self._position_hierarchy_tree
        self._position_hierarchy_paths.clear()
        self._hierarchy_children = {}
        self._hierarchy_hits.clear()
        self._hierarchy_offsets.clear()
        self._hierarchy_more.clear()
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        self.position_hierarchy_use_button.configure(state="disabled")

    def _render_position_hierarchy_candidates(self, result: HierarchySearchResult | None) -> None:
        model = self._hierarchy_model
        tree = self._position_hierarchy_tree
        if tree is None or model is None:
            return
        self._clear_position_hierarchy_candidates()
        if result is None:
            self._hierarchy_children = model.children
            status = f"DUT 层次 · {len(model.by_path)} 个节点 · 展开箭头浏览" if model.by_path else "DUT 层次为空"
        else:
            self._hierarchy_hits.update(match.path for match in result.matches)
            self._hierarchy_children = model.filtered_children(self._hierarchy_hits)
            status = f"匹配 {result.total_matches}，显示 {len(result.matches)}"
            if result.total_matches > len(result.matches):
                status += " · 结果已截断，请收窄正则"
            if result.total_matches == 0:
                status += " · 未找到匹配路径，可清除筛选"
        self._append_hierarchy_page("")
        if result is not None and result.matches:
            # Expand all of a small filtered tree so same-named leaves in e.g.
            # two tiles are visible together. Keep very broad/deep views lazy.
            first = result.matches[0].path
            if sum(map(len, self._hierarchy_children.values())) <= 200:
                ancestors = sorted(path for path in self._hierarchy_children if path)
            else:
                ancestors = []
                parent = model.parents[first]
                while parent:
                    ancestors.append(parent)
                    parent = model.parents[parent]
                ancestors.reverse()
            for parent in ancestors:
                self._expand_hierarchy(parent)
                tree.item(self._scope_iid(parent), open=True)
            tree.selection_set(self._scope_iid(first))
            tree.see(self._scope_iid(first))
        if model.index.truncated:
            status += " · 层次索引不完整"
        if model.index.notices:
            status += " · KDB 部分加载"
        self.position_hierarchy_status_var.set(status)
        self._on_position_hierarchy_candidate_selected()

    @staticmethod
    def _scope_iid(path: str) -> str:
        return "scope:" + path if path else ""

    def _append_hierarchy_page(self, parent: str) -> None:
        # Broad scopes must not create tens of thousands of Tk widgets at once.
        tree = self._position_hierarchy_tree
        children = self._hierarchy_children.get(parent, ())
        start = self._hierarchy_offsets.get(parent, 0)
        end = min(start + 200, len(children))
        self._hierarchy_offsets[parent] = end
        parent_id = self._scope_iid(parent)
        more = "more:" + parent
        if more in self._hierarchy_more:
            tree.delete(more)
            del self._hierarchy_more[more]
        for path in children[start:end]:
            scope = self._hierarchy_model.by_path[path]
            iid = self._scope_iid(path)
            self._position_hierarchy_paths[iid] = path
            tree.insert(parent_id, "end", iid=iid, text=scope.name or path,
                        values=(path, scope.definition, scope.kind),
                        tags=("hit",) if path in self._hierarchy_hits else ())
            if path in self._hierarchy_children:
                tree.insert(iid, "end", iid="pending:" + path)
        if end < len(children):
            self._hierarchy_more[more] = parent
            tree.insert(parent_id, "end", iid=more, text=f"显示更多…（还有 {len(children)-end} 项）")

    def _expand_hierarchy(self, path: str) -> None:
        if path in self._hierarchy_children and path not in self._hierarchy_offsets:
            self._position_hierarchy_tree.delete("pending:" + path)
            self._append_hierarchy_page(path)

    def _on_hierarchy_open(self, _event: Any) -> None:
        tree = self._position_hierarchy_tree
        path = self._position_hierarchy_paths.get(tree.focus())
        if path is not None:
            self._expand_hierarchy(path)

    def _on_hierarchy_return(self, _event: Any) -> str:
        tree = self._position_hierarchy_tree
        selected = tree.selection()
        if selected and selected[0] in self._hierarchy_more:
            self._append_hierarchy_page(self._hierarchy_more[selected[0]])
        else:
            self._use_selected_position_hierarchy()
        return "break"

    def _on_hierarchy_double_click(self, event: Any) -> str | None:
        tree = self._position_hierarchy_tree
        iid = tree.identify_row(event.y)
        if not iid or self.running:
            return "break"
        if iid in self._hierarchy_more:
            self._append_hierarchy_page(self._hierarchy_more[iid])
            return "break"
        if self._position_hierarchy_paths.get(iid) in self._hierarchy_children:
            return None  # Native Tk toggles branches, including a matched branch.
        tree.selection_set(iid)
        self._use_selected_position_hierarchy()
        return "break"

    def _on_position_hierarchy_candidate_selected(self, _event: Any = None) -> None:
        tree = self._position_hierarchy_tree
        if tree is None:
            return
        selected = tree.selection()
        enabled = bool(selected and selected[0] in self._position_hierarchy_paths)
        self.position_hierarchy_use_button.configure(
            state="normal" if enabled and not self.running else "disabled"
        )

    def _use_selected_position_hierarchy(self) -> None:
        tree = self._position_hierarchy_tree
        if tree is None or self.running:
            return
        selected = tree.selection()
        if not selected:
            return
        path = self._position_hierarchy_paths.get(selected[0])
        if not path:
            return
        target = self.position_path_var if self._hierarchy_target == "position" else self.crg_source_path_var
        target.set(path)
        self.status_var.set(f"已选择 Verdi {self._hierarchy_target} 全路径")
        self._destroy_position_hierarchy_search()

    def _build_crg_sources_tab(self) -> None:
        tab = self.crg_sources_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        tree_frame = self._database_list(
            tab, "搜索 CRG Source", self.crg_source_search_var, self.crg_source_mapping_status_var
        )
        self.crg_source_tree = ttk.Treeview(
            tree_frame,
            columns=("alias", "rtl_path"),
            show="headings",
            selectmode="browse",
        )
        for name, title, width, stretch in (
            ("alias", "CRG Source简称", 280, False),
            ("rtl_path", "RTL完整路径", 1200, False),
        ):
            self.crg_source_tree.heading(name, text=title, anchor="w")
            self.crg_source_tree.column(
                name, width=width, minwidth=140, stretch=stretch
            )
        crg_source_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.crg_source_tree.yview
        )
        crg_source_xscroll = ttk.Scrollbar(
            tree_frame, orient="horizontal", command=self.crg_source_tree.xview
        )
        self.crg_source_tree.configure(
            yscrollcommand=crg_source_scroll.set,
            xscrollcommand=crg_source_xscroll.set,
        )
        self.crg_source_tree.grid(row=0, column=0, sticky="nsew")
        crg_source_scroll.grid(row=0, column=1, sticky="ns")
        crg_source_xscroll.grid(row=1, column=0, sticky="ew")
        self.crg_source_tree.bind(
            "<<TreeviewSelect>>", self._on_crg_source_selected
        )

        edge, editor = self.ui.card(tab, "DB", "CRG Source 映射编辑", "选中表格行即可编辑")
        edge.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=3)
        ttk.Label(editor, text="CRG Source简称").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(editor, textvariable=self.crg_source_alias_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 18)
        )
        ttk.Label(editor, text="RTL完整路径").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Entry(editor, textvariable=self.crg_source_path_var).grid(
            row=0, column=3, sticky="ew", padx=(8, 8)
        )
        self.crg_source_hierarchy_pick_button = ttk.Button(
            editor, text="选择 DUT 层次…", style="Quiet.TButton",
            command=lambda: self._open_position_hierarchy_search("CRG_source"))
        self.crg_source_hierarchy_pick_button.grid(row=0, column=4, sticky="e")

        actions = ttk.Frame(tab, style="Page.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="新建", style="Page.TButton", command=self._new_crg_source_mapping).pack(
            side="left"
        )
        ttk.Button(
            actions, text="应用修改", style="Page.TButton", command=self._apply_crg_source_mapping
        ).pack(side="left", padx=8)
        ttk.Button(
            actions, text="删除", style="Page.Danger.TButton", command=self._delete_crg_source_mapping
        ).pack(side="left")
        ttk.Button(
            actions,
            text="保存映射库",
            style="Page.Primary.TButton",
            command=self._save_crg_source_mappings,
        ).pack(side="right")
        self.crg_source_search_var.trace_add(
            "write", lambda *_: self._render_crg_source_tree()
        )

    def _build_results_tab(self) -> None:
        tab = self.results_tab
        tab.rowconfigure(1, weight=3)
        tab.rowconfigure(2, weight=2)
        tab.columnconfigure(0, weight=1)

        summary = ttk.Frame(tab, style="Page.TFrame")
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        metrics = ((self.summary_state_var, "检查状态", design.INK),
                   (self.summary_rows_var, "规格行", design.INK),
                   (self.summary_pass_var, "通过行", design.SUCCESS),
                   (self.summary_fail_var, "失败行", design.RED),
                   (self.summary_errors_var, "错误", design.RED),
                   (self.summary_warnings_var, "警告", design.AMBER))
        self.metric_values = []
        for i, (var, caption, color) in enumerate(metrics):
            summary.columnconfigure(i, weight=1, uniform="metrics")
            tile = ttk.Frame(summary, style="Metric.TFrame", padding=(8, 4))
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if not i else 3, 0 if i == 5 else 3))
            ttk.Label(tile, text=caption, style="Muted.TLabel").pack(anchor="w", padx=3, pady=(2,0))
            val = tk.Label(tile, bg=design.PAPER, fg=color, font=self.ui.f(15, "bold"), anchor="w", pady=0)
            val.pack(anchor="w", padx=3, pady=(0,2))
            self.metric_values.append(val)
        def refresh(*_):
            state = self.summary_state_var.get()
            ready = state in {"PASS", "FAIL", "VALID"}
            for i, (var, _, color) in enumerate(metrics):
                display = var.get().rsplit(" ", 1)[-1] if ready else "—"
                if i == 0:
                    display = {"未运行": "待运行", "RUNNING": "运行中", "CANCELLED": "已取消",
                               "ERROR": "执行错误"}.get(state, state)
                    color = {"PASS": design.SUCCESS, "FAIL": design.RED, "ERROR": design.RED}.get(state, design.INK)
                elif not ready or display == "0":
                    color = design.MUTED
                self.metric_values[i].configure(text=display, fg=color)
        for var, _, _ in metrics:
            var.trace_add("write", refresh)
        refresh()

        result_frame = ttk.Frame(tab, style="Surface.TFrame", padding=(9, 6, 9, 7))
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.rowconfigure(1, weight=1)
        result_frame.columnconfigure(0, weight=1)
        bar = ttk.Frame(result_frame, padding=(10, 5))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="检查明细", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(bar, textvariable=self.result_count_var, style="Muted.TLabel").pack(side="left", padx=9)
        ttk.Checkbutton(bar, text="仅看问题", variable=self.only_issues_var,
                        command=self._filter_results).pack(side="right")
        columns = (
            "status",
            "row",
            "interface",
            "position",
            "crg_source",
            "crg_trace",
            "rs_inst",
            "rs_cfg_en",
            "physical_instances",
            "effective_step",
            "findings",
        )
        self.result_tree = ttk.Treeview(
            result_frame,
            columns=columns,
            displaycolumns=("status", "row", "interface", "rs_inst", "effective_step", "physical_instances",
                            "findings", "crg_trace", "position", "crg_source", "rs_cfg_en"),
            height=4,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "status": "状态",
            "row": "Excel 行",
            "interface": "Interface",
            "position": "Position",
            "crg_source": "CRG Source",
            "crg_trace": "CRG Trace",
            "rs_inst": "例化匹配",
            "rs_cfg_en": "RS_CFG_EN",
            "physical_instances": "匹配实例",
            "effective_step": "实际/期望拍",
            "findings": "Finding",
        }
        widths = {
            "status": 65,
            "row": 74,
            "interface": 85,
            "position": 520,
            "crg_source": 520,
            "crg_trace": 90,
            "rs_inst": 253,
            "rs_cfg_en": 90,
            "physical_instances": 75,
            "effective_step": 95,
            "findings": 80,
        }
        for name in columns:
            anchor = "center" if name in {"row","physical_instances","effective_step","findings","crg_trace"} else "w"
            self.result_tree.heading(name, text=headings[name], anchor=anchor)
            self.result_tree.column(
                name, width=widths[name], minwidth=55, stretch=False, anchor=anchor
            )
        result_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_tree.yview)
        result_xscroll = ttk.Scrollbar(
            result_frame, orient="horizontal", command=self.result_tree.xview
        )
        self.result_tree.configure(
            yscrollcommand=result_scroll.set,
            xscrollcommand=result_xscroll.set,
        )
        self.result_tree.grid(row=1, column=0, sticky="nsew")
        result_scroll.grid(row=1, column=1, sticky="ns")
        result_xscroll.grid(row=2, column=0, sticky="ew")
        self.result_tree.tag_configure("pass", foreground=design.INK)
        self.result_tree.tag_configure("fail", foreground=design.RED, background=design.FAIL_WASH)
        self.result_tree.tag_configure("warning", foreground=design.AMBER, background=design.WARN_WASH)
        self.result_empty = ttk.Label(self.result_tree, style="Muted.TLabel", justify="center", anchor="center")
        self._show_result_empty("还没有检查结果\n验证 Excel 或运行 RTL 检查后，明细将显示在这里。")
        self.result_tree.bind("<<TreeviewSelect>>", self._on_result_selected)

        detail_pane = ttk.Panedwindow(tab, orient="horizontal")
        detail_pane.grid(row=2, column=0, sticky="nsew", pady=(8, 0))

        findings_frame = ttk.Frame(detail_pane, style="Surface.TFrame", padding=(8, 6), width=285, height=185)
        findings_frame.grid_propagate(False)
        findings_frame.rowconfigure(1, weight=1)
        findings_frame.columnconfigure(0, weight=1)
        ttk.Label(findings_frame, text="诊断项", style="CardTitle.TLabel", padding=(10,5)).grid(
            row=0, column=0, sticky="w")
        finding_columns = ("severity", "code", "instance", "message")
        self.finding_tree = ttk.Treeview(
            findings_frame,
            columns=finding_columns,
            displaycolumns=("severity", "code", "message", "instance"),
            height=3,
            show="headings",
            selectmode="browse",
        )
        for name, title, width in (
            ("severity", "级别", 70),
            ("code", "代码", 190),
            ("instance", "实例", 230),
            ("message", "说明", 420),
        ):
            self.finding_tree.heading(name, text=title, anchor="w")
            self.finding_tree.column(name, width=width, minwidth=60, stretch=name == "message")
        finding_scroll = ttk.Scrollbar(
            findings_frame, orient="vertical", command=self.finding_tree.yview
        )
        self.finding_tree.configure(yscrollcommand=finding_scroll.set)
        self.finding_tree.grid(row=1, column=0, sticky="nsew")
        finding_scroll.grid(row=1, column=1, sticky="ns")
        fx = ttk.Scrollbar(findings_frame, orient="horizontal", command=self.finding_tree.xview)
        self.finding_tree.configure(xscrollcommand=fx.set)
        fx.grid(row=2, column=0, sticky="ew")
        self.finding_empty = ttk.Label(self.finding_tree, text="请选择一条检查结果", style="Muted.TLabel")
        self.finding_empty.place(relx=.5, rely=.62, anchor="center")
        self.finding_tree.bind("<<TreeviewSelect>>", self._on_finding_selected)

        evidence_frame = ttk.Frame(detail_pane, style="Surface.TFrame", padding=(8, 6), width=430, height=185)
        evidence_frame.grid_propagate(False)
        evidence_frame.rowconfigure(1, weight=1)
        evidence_frame.columnconfigure(0, weight=1)
        inspector_bar = ttk.Frame(evidence_frame, padding=(10, 1, 4, 1))
        inspector_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(inspector_bar, text="检查证据", style="CardTitle.TLabel").pack(side="left")
        ttk.Button(inspector_bar, text="复制 JSON", style="Quiet.TButton",
                   command=lambda: self._copy_text(self.evidence_text, "证据 JSON")).pack(side="right")
        self.inspector_tabs = ttk.Notebook(evidence_frame)
        self.inspector_tabs.grid(row=1, column=0, sticky="nsew")
        summary_view, raw_view = ttk.Frame(self.inspector_tabs), ttk.Frame(self.inspector_tabs)
        self.inspector_tabs.add(summary_view, text="诊断摘要")
        self.match_view = ttk.Frame(self.inspector_tabs)
        self.inspector_tabs.add(self.match_view, text="匹配明细")
        self.inspector_tabs.add(raw_view, text="原始 JSON")
        self.copy_match_button = ttk.Button(
            inspector_bar, text="复制匹配明细", style="Quiet.TButton",
            command=lambda: self._copy_text(self.match_text, "匹配明细"))
        self.copy_match_button.pack(side="right")
        box, self.match_text = self.ui.text_panel(
            self.match_view, wrap="char", width=1, height=2, font=self.ui.f(10), bg=design.PAPER,
            fg=design.INK, padx=10, pady=4, highlightthickness=0, relief="flat", borderwidth=0)
        box.pack(fill="both", expand=True)
        self.match_text.configure(state="disabled")
        box, self.review_text = self.ui.text_panel(
            summary_view, wrap="word", width=1, height=2, font=self.ui.f(10), bg=design.PAPER,
            fg=design.INK, padx=10, pady=4, highlightthickness=0, relief="flat", borderwidth=0)
        box.pack(fill="both", expand=True)
        self.review_text.tag_configure("heading", foreground=design.CLAY_INK, font=self.ui.f(10,"bold"),
                                       spacing1=3, spacing3=1)
        self.review_text.tag_configure("body", spacing3=4)
        self.review_text.tag_configure("problem", foreground=design.RED)
        self.review_text.tag_configure("caution", foreground=design.AMBER)
        self.review_text.configure(state="disabled")
        box, self.evidence_text = self.ui.text_panel(
            raw_view,
            wrap="word",
            width=1, height=2, padx=9, pady=6, relief="flat", highlightthickness=0, borderwidth=0,
            font=self.ui.code(10), background=design.PAPER, foreground=design.INK,
        )
        box.pack(fill="both", expand=True)
        self.evidence_text.configure(state="disabled")
        detail_pane.add(findings_frame, weight=2)
        detail_pane.add(evidence_frame, weight=3)
        self._display_evidence(None)

    def _build_log_tab(self) -> None:
        self.log_tab.rowconfigure(0, weight=1)
        self.log_tab.columnconfigure(0, weight=1)
        panel = ttk.Frame(self.log_tab, style="Surface.TFrame", padding=(12, 8, 12, 12))
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        bar = ttk.Frame(panel, padding=(2, 4))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="会话输出", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(bar, text="STDOUT  /  STDERR", style="Muted.TLabel").pack(side="left", padx=10)
        ttk.Button(bar, text="复制日志", style="Quiet.TButton",
                   command=lambda: self._copy_text(self.log_text, "运行日志")).pack(side="right")
        console = ttk.Frame(panel, style="Console.TFrame", padding=8)
        console.grid(row=1, column=0, sticky="nsew")
        box, self.log_text = self.ui.text_panel(
            console,
            wrap="word",
            font=self.ui.code(10), borderwidth=0, highlightthickness=0, relief="flat", padx=12, pady=10,
            background=design.MONITOR,
            foreground="#eee9e0",
            insertbackground="#eee9e0",
        )
        box.pack(fill="both", expand=True)
        self.log_text.tag_configure("command", foreground="#efb89b")
        self.log_text.tag_configure("exit", foreground="#c6d2ad")
        self.log_text.configure(state="disabled")
        self.log_empty = tk.Label(self.log_text, text="等待首次运行\n运行验证或检查后，可在此追溯实际命令与输出。",
                                  justify="left", bg=design.MONITOR, fg="#c3bcb0", font=self.ui.f(10))
        self.log_empty.place(x=14, y=12)

    def _copy_text(self, widget, label) -> None:
        value = widget.get("1.0", "end-1c")
        if not value:
            self.status_var.set(f"暂无{label}可复制")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
        except tk.TclError:
            self.status_var.set("当前显示环境无法使用剪贴板")
        else:
            self.status_var.set(f"已复制{label}")

    def _build_status_bar(self) -> None:
        edge = tk.Frame(self.root, bg=design.LINE, pady=1)
        edge.grid(row=2, column=1, sticky="ew")
        actions = ttk.Frame(edge, padding=(16, 7))
        actions.pack(fill="x")
        actions.columnconfigure(1, weight=1)
        self.status_light = tk.Canvas(actions, width=12, height=12, highlightthickness=0, bg=design.PAPER)
        self.status_light.grid(row=0, column=0, padx=(0, 5))
        self.status_dot = self.status_light.create_oval(2, 2, 10, 10, fill=design.SUCCESS, outline="")
        status = ttk.Label(actions, textvariable=self.status_var, width=1, style="Muted.TLabel")
        status.grid(row=0, column=1, sticky="ew")
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=68)
        self.progress.grid(row=0, column=2, padx=6)
        self.progress.grid_remove()
        ttk.Button(actions, text="报告目录", command=self._open_report_directory,
                   style="Quiet.TButton").grid(row=0, column=3, padx=(3, 10))
        self.cancel_button = ttk.Button(actions, text="取消", style="Danger.TButton", state="disabled",
                                        command=self._cancel_operation)
        self.cancel_button.grid(row=0, column=4, padx=(0, 6))
        self.validate_button = ttk.Button(actions, text="验证 Excel", command=lambda: self._start_operation("validate"))
        self.validate_button.grid(row=0, column=5, padx=(0, 6))
        self.run_button = ttk.Button(actions, text="运行 RTL 检查", style="Primary.TButton",
                                     command=lambda: self._start_operation("check"))
        self.run_button.grid(row=0, column=6)
        self.summary_state_var.trace_add("write", lambda *_: self._status_color())

    def _status_color(self) -> None:
        state = self.summary_state_var.get()
        color = design.BLUE if self.running else (
            design.RED if state in {"FAIL", "ERROR"} else design.AMBER if state == "CANCELLED" else design.SUCCESS
        )
        self.status_light.itemconfigure(self.status_dot, fill=color)

    def _path_row(
        self,
        parent: Any,
        row: int,
        label: str,
        variable: Any,
        command: Any,
        *,
        button_text: str = "浏览",
        extra_buttons: tuple[tuple[str, Any], ...] = (),
    ) -> tuple[Any, ...]:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(10, 4), pady=4
        )
        buttons = [ttk.Button(parent, text=button_text, command=command)]
        buttons[0].grid(row=row, column=2, pady=4)
        for index, (text, extra_command) in enumerate(extra_buttons, start=3):
            button = ttk.Button(parent, text=text, command=extra_command)
            button.grid(row=row, column=index, padx=(4, 0), pady=4)
            buttons.append(button)
        return tuple(buttons)

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

    def _import_config(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            initialdir=self._initial_directory(self.config_var.get()),
            filetypes=(("JSON", "*.json"), ("所有文件", "*.*")),
        )
        if not selected:
            return
        try:
            config = load_complete_config(selected)
        except RsCheckError as exc:
            self.status_var.set("配置导入失败")
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        if self._excel_form_differs_from_loaded_config() and not (
            self._confirm_discard_excel_changes()
        ):
            return
        if self._module_rules_dirty and not self._confirm_discard_rule_changes():
            return
        if (
            self._position_mappings_dirty
            and not self._confirm_discard_position_changes()
        ):
            return
        if (
            self._crg_source_mappings_dirty
            and not self._confirm_discard_crg_source_changes()
        ):
            return
        try:
            config = load_complete_config(selected)
            self._apply_config(config, selected, "完整配置已导入")
        except (RsCheckError, GuiInputError) as exc:
            self.status_var.set("配置导入失败")
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return

    def _export_config(self) -> None:
        if self._loaded_config is None:
            messagebox.showerror(
                "导出失败",
                "请先导入或加载配置 JSON",
                parent=self.root,
            )
            return
        try:
            if not _same_config_path(
                self.config_var.get().strip(), self._loaded_config_path
            ):
                messagebox.showerror(
                    "导出失败",
                    "配置路径已变化，请先加载当前配置",
                    parent=self.root,
                )
                return
        except GuiInputError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        current_name = Path(self.config_var.get().strip()).name
        stem = Path(current_name).stem if current_name else "rscheck"
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            initialdir=self._initial_directory(self.config_var.get()),
            initialfile=f"{stem}.export.json",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"), ("所有文件", "*.*")),
            confirmoverwrite=True,
        )
        if not selected:
            return
        try:
            targets_current_config = _same_config_path(
                selected,
                self._loaded_config_path,
                include_hardlinks=True,
            )
        except GuiInputError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        if targets_current_config:
            messagebox.showerror(
                "导出失败",
                "导出目标不能覆盖当前配置，请选择其他文件名",
                parent=self.root,
            )
            return
        try:
            snapshot = self._config_snapshot_from_form()
            output = save_config(snapshot, selected)
        except (RsCheckError, GuiInputError) as exc:
            self.status_var.set("配置导出失败")
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.status_var.set("完整配置已导出")
        messagebox.showinfo(
            "导出成功",
            f"完整配置已导出到：\n{output}",
            parent=self.root,
        )

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
        config_path = self.config_var.get().strip()
        try:
            config = load_config(config_path)
        except RsCheckError as exc:
            self.status_var.set("配置加载失败")
            if show_error:
                messagebox.showerror("配置错误", str(exc), parent=self.root)
            return
        if confirm_discard:
            if self._excel_form_differs_from_loaded_config() and not (
                self._confirm_discard_excel_changes()
            ):
                return
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
            if (
                self._crg_source_mappings_dirty
                and not self._confirm_discard_crg_source_changes()
            ):
                return
        try:
            config = load_config(config_path)
            self._apply_config(config, config_path, "配置已加载")
        except (RsCheckError, GuiInputError) as exc:
            self.status_var.set("配置加载失败")
            if show_error:
                messagebox.showerror("配置错误", str(exc), parent=self.root)
            return

    def _apply_config(self, config: ToolConfig, path: str | Path, status: str) -> None:
        config_path = str(path)
        resolved_path = _resolved_config_path(path)
        self.config_var.set(config_path)
        self.sheet_var.set(str(config.excel.sheet))
        self.header_row_var.set(str(config.excel.header_row))
        self.data_start_row_var.set(str(config.excel.data_start_row))
        self.header_check_var.set(config.excel.validate_headers)
        self.crg_trace_max_depth_var.set(str(config.rtl.crg_trace_max_depth))
        for name in FIELD_NAMES:
            self.column_vars[name].set(str(config.excel.columns.get(name, "")))
        self._loaded_config = config
        self._loaded_config_path = resolved_path
        self._module_rules = dict(config.module_rules)
        self._module_rules_dirty = False
        self._position_mappings = dict(config.position_mappings)
        self._position_mappings_dirty = False
        self._crg_source_mappings = dict(config.crg_source_mappings)
        self._crg_source_mappings_dirty = False
        self._new_rule()
        self._new_position_mapping()
        self._new_crg_source_mapping()
        self._render_rule_tree()
        self._render_position_tree()
        self._render_crg_source_tree()
        self.status_var.set(status)

    def _config_snapshot_from_form(self) -> ToolConfig:
        if self._loaded_config is None:
            raise GuiInputError("请先导入或加载配置 JSON")
        candidate = replace(
            self._loaded_config,
            module_rules=dict(self._module_rules),
            position_mappings=dict(self._position_mappings),
            crg_source_mappings=dict(self._crg_source_mappings),
        )
        raw = config_to_dict(candidate)
        excel = raw["excel"]
        sheet = self.sheet_var.get()
        if sheet and sheet != str(self._loaded_config.excel.sheet):
            excel["sheet"] = (
                int(sheet)
                if sheet.isascii() and sheet.isdecimal()
                else sheet
            )
        header_row = self.header_row_var.get().strip()
        if header_row:
            excel["header_row"] = header_row
        data_start_row = self.data_start_row_var.get().strip()
        if data_start_row:
            excel["data_start_row"] = data_start_row
        excel["validate_headers"] = bool(self.header_check_var.get())
        raw["columns"] = {
            name: self.column_vars[name].get().strip() for name in FIELD_NAMES
            if name != "RS_route" or self.column_vars[name].get().strip()
        }
        raw["rtl"]["crg_trace_max_depth"] = (
            self.crg_trace_max_depth_var.get().strip()
        )
        return config_from_dict(raw)

    def _excel_form_differs_from_loaded_config(self) -> bool:
        if self._loaded_config is None:
            return False
        loaded = self._loaded_config.excel
        sheet = self.sheet_var.get()
        if sheet and sheet != str(loaded.sheet):
            return True
        header_row = self.header_row_var.get().strip()
        if header_row and header_row != str(loaded.header_row):
            return True
        data_start_row = self.data_start_row_var.get().strip()
        if data_start_row and data_start_row != str(loaded.data_start_row):
            return True
        if bool(self.header_check_var.get()) != loaded.validate_headers:
            return True
        if (
            self.crg_trace_max_depth_var.get().strip()
            != str(self._loaded_config.rtl.crg_trace_max_depth)
        ):
            return True
        return any(
            self.column_vars[name].get().strip() != str(loaded.columns.get(name, ""))
            for name in FIELD_NAMES
        )

    def _confirm_discard_excel_changes(self) -> bool:
        return bool(
            messagebox.askyesno(
                "未保存配置",
                "Excel、列号或 CRG Trace 设置有未保存修改，确定放弃吗？",
                parent=self.root,
            )
        )

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

    def _confirm_discard_crg_source_changes(self) -> bool:
        if not self._crg_source_mappings_dirty:
            return True
        return bool(
            messagebox.askyesno(
                "未保存映射",
                "CRG Source 映射库有未保存修改，确定放弃吗？",
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
                    rule.clk_port,
                    rule.rst_port,
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
        self.module_clk_port_var.set(rule.clk_port)
        self.module_rst_port_var.set(rule.rst_port)

    def _new_rule(self) -> None:
        self._editing_module_name = ""
        self.module_name_var.set("")
        self.module_has_rs_cfg_en_var.set(False)
        self.module_step_parameters_var.set("")
        self.module_clk_port_var.set("clk")
        self.module_rst_port_var.set("rst_n")
        if hasattr(self, "rule_tree"):
            self.rule_tree.selection_remove(*self.rule_tree.selection())

    def _apply_rule(self) -> None:
        try:
            rule = _module_rule_from_form(
                self.module_name_var.get(),
                self.module_has_rs_cfg_en_var.get(),
                self.module_step_parameters_var.get(),
                self.module_clk_port_var.get(),
                self.module_rst_port_var.get(),
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
        try:
            path_matches = _same_config_path(config_path, self._loaded_config_path)
        except GuiInputError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        if not path_matches:
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
        loaded_config = reloaded
        if self._position_mappings_dirty:
            loaded_config = replace(
                loaded_config,
                position_mappings=dict(baseline.position_mappings),
            )
        else:
            self._position_mappings = dict(reloaded.position_mappings)
            self._new_position_mapping()
            self._render_position_tree()
        if self._crg_source_mappings_dirty:
            loaded_config = replace(
                loaded_config,
                crg_source_mappings=dict(baseline.crg_source_mappings),
            )
        else:
            self._crg_source_mappings = dict(reloaded.crg_source_mappings)
            self._new_crg_source_mapping()
            self._render_crg_source_tree()
        self._loaded_config = loaded_config
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
        try:
            path_matches = _same_config_path(config_path, self._loaded_config_path)
        except GuiInputError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        if not path_matches:
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
        loaded_config = reloaded
        if self._module_rules_dirty:
            loaded_config = replace(
                loaded_config,
                module_rules=dict(baseline.module_rules),
            )
        else:
            self._module_rules = dict(reloaded.module_rules)
            self._new_rule()
            self._render_rule_tree()
        if self._crg_source_mappings_dirty:
            loaded_config = replace(
                loaded_config,
                crg_source_mappings=dict(baseline.crg_source_mappings),
            )
        else:
            self._crg_source_mappings = dict(reloaded.crg_source_mappings)
            self._new_crg_source_mapping()
            self._render_crg_source_tree()
        self._loaded_config = loaded_config
        self._position_mappings = dict(reloaded.position_mappings)
        self._position_mappings_dirty = False
        self._render_position_tree()
        self.status_var.set("Position 映射库已保存")

    def _render_crg_source_tree(self) -> None:
        if not hasattr(self, "crg_source_tree"):
            return
        selected_alias = self._editing_crg_source_alias
        for item in self.crg_source_tree.get_children():
            self.crg_source_tree.delete(item)
        self._crg_source_tree_aliases.clear()
        search = self.crg_source_search_var.get().strip().casefold()
        selected_iid = ""
        for index, alias in enumerate(sorted(self._crg_source_mappings)):
            rtl_path = self._crg_source_mappings[alias]
            if (
                search
                and search not in alias.casefold()
                and search not in rtl_path.casefold()
            ):
                continue
            iid = f"crg-source-mapping-{index}"
            self._crg_source_tree_aliases[iid] = alias
            self.crg_source_tree.insert(
                "", "end", iid=iid, values=(alias, rtl_path)
            )
            if alias == selected_alias:
                selected_iid = iid
        if selected_iid:
            self.crg_source_tree.selection_set(selected_iid)
            self.crg_source_tree.see(selected_iid)
        suffix = " · 未保存" if self._crg_source_mappings_dirty else ""
        self.crg_source_mapping_status_var.set(
            f"映射 {len(self._crg_source_mappings)}{suffix}"
        )

    def _on_crg_source_selected(self, _event: Any = None) -> None:
        selection = self.crg_source_tree.selection()
        if not selection:
            return
        alias = self._crg_source_tree_aliases.get(selection[0], "")
        rtl_path = self._crg_source_mappings.get(alias)
        if rtl_path is None:
            return
        self._editing_crg_source_alias = alias
        self.crg_source_alias_var.set(alias)
        self.crg_source_path_var.set(rtl_path)

    def _new_crg_source_mapping(self) -> None:
        self._editing_crg_source_alias = ""
        self.crg_source_alias_var.set("")
        self.crg_source_path_var.set("")
        if hasattr(self, "crg_source_tree"):
            self.crg_source_tree.selection_remove(
                *self.crg_source_tree.selection()
            )

    def _apply_crg_source_mapping(self) -> None:
        try:
            alias, rtl_path = _crg_source_mapping_from_form(
                self.crg_source_alias_var.get(), self.crg_source_path_var.get()
            )
        except GuiInputError as exc:
            messagebox.showerror("映射错误", str(exc), parent=self.root)
            return
        original = self._editing_crg_source_alias
        if alias in self._crg_source_mappings and alias != original:
            messagebox.showerror(
                "映射错误",
                f"CRG_source 简写 {alias!r} 已存在",
                parent=self.root,
            )
            return
        if original and original != alias:
            self._crg_source_mappings.pop(original, None)
        self._crg_source_mappings[alias] = rtl_path
        self._editing_crg_source_alias = alias
        self._crg_source_mappings_dirty = True
        self._render_crg_source_tree()
        self.status_var.set("CRG Source 映射已修改")

    def _delete_crg_source_mapping(self) -> None:
        alias = self._editing_crg_source_alias
        if not alias or alias not in self._crg_source_mappings:
            return
        if not messagebox.askyesno(
            "删除映射",
            f"确定删除 CRG_source 简写 {alias!r} 的映射吗？",
            parent=self.root,
        ):
            return
        del self._crg_source_mappings[alias]
        self._crg_source_mappings_dirty = True
        self._new_crg_source_mapping()
        self._render_crg_source_tree()
        self.status_var.set("CRG Source 映射已删除")

    def _save_crg_source_mappings(self) -> None:
        config_path = self.config_var.get().strip()
        if self._loaded_config is None or not config_path:
            messagebox.showerror("保存失败", "请先加载配置 JSON", parent=self.root)
            return
        try:
            path_matches = _same_config_path(config_path, self._loaded_config_path)
        except GuiInputError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        if not path_matches:
            messagebox.showerror(
                "保存失败",
                "配置路径已变化，请先加载当前配置",
                parent=self.root,
            )
            return
        baseline = self._loaded_config
        try:
            current = load_config(config_path)
            if current.crg_source_mappings != baseline.crg_source_mappings:
                messagebox.showerror(
                    "保存冲突",
                    "CRG Source 映射库已被外部修改，请先重新加载配置后再保存",
                    parent=self.root,
                )
                return
            updated = replace(
                current,
                crg_source_mappings=dict(self._crg_source_mappings),
            )
            output = save_config(updated, config_path)
            reloaded = load_config(output)
        except RsCheckError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        loaded_config = reloaded
        if self._module_rules_dirty:
            loaded_config = replace(
                loaded_config,
                module_rules=dict(baseline.module_rules),
            )
        else:
            self._module_rules = dict(reloaded.module_rules)
            self._new_rule()
            self._render_rule_tree()
        if self._position_mappings_dirty:
            loaded_config = replace(
                loaded_config,
                position_mappings=dict(baseline.position_mappings),
            )
        else:
            self._position_mappings = dict(reloaded.position_mappings)
            self._new_position_mapping()
            self._render_position_tree()
        self._loaded_config = loaded_config
        self._crg_source_mappings = dict(reloaded.crg_source_mappings)
        self._crg_source_mappings_dirty = False
        self._render_crg_source_tree()
        self.status_var.set("CRG Source 映射库已保存")

    def _update_source_mode(self) -> None:
        if self.source_mode_var.get() == LIVE_SOURCE:
            self.mode_badge.configure(text="数据源  /  在线 NPI", style="Live.Badge.TLabel")
            self.inventory_frame.grid_remove()
            self.live_frame.grid(row=1, column=0, sticky="nsew")
        else:
            self.mode_badge.configure(text="数据源  /  离线 Inventory", style="Offline.Badge.TLabel")
            self.live_frame.grid_remove()
            self.inventory_frame.grid(row=1, column=0, sticky="nsew")
        self.root.after_idle(self._arrange_setup)

    def _sheet_override_from_form(self) -> str:
        value = self.sheet_var.get()
        if (
            self._loaded_config is not None
            and value == str(self._loaded_config.excel.sheet)
        ):
            return ""
        return value

    def _request(self) -> GuiRunRequest:
        return GuiRunRequest(
            excel_path=self.excel_var.get(),
            config_path=self.config_var.get(),
            columns={name: variable.get() for name, variable in self.column_vars.items()},
            sheet=self._sheet_override_from_form(),
            header_row=self.header_row_var.get(),
            data_start_row=self.data_start_row_var.get(),
            validate_headers=self.header_check_var.get(),
            crg_trace_max_depth=self.crg_trace_max_depth_var.get(),
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

    def _start_position_hierarchy_search(self) -> None:
        if self.running or self.controller.is_running:
            return
        request = self._request()
        query = self.position_hierarchy_regex_var.get()
        if not query and self._hierarchy_model is not None:
            self._render_position_hierarchy_candidates(None)
            return
        index_path = Path(self._hierarchy_temp.name) / "index.json"
        try:
            build_position_hierarchy_search_command(request, query, session_index=index_path)
        except GuiInputError as exc:
            self.position_hierarchy_status_var.set("筛选无效，未执行查询")
            self._clear_position_hierarchy_candidates()
            messagebox.showerror(
                "查询输入错误",
                str(exc),
                parent=self._position_hierarchy_dialog or self.root,
            )
            return

        with self._cancel_guard:
            self._cancel_requested.clear()
        self._clear_position_hierarchy_candidates()
        self.position_hierarchy_status_var.set("正在按全路径筛选…" if query else "正在读取 Verdi DUT 层次…")
        self._active_action = "hierarchy-search"
        self._set_running(True)
        self.status_var.set("正在查询 Verdi DUT 层次")
        self._append_log("\n" + "=" * 72 + "\n")
        worker = threading.Thread(
            target=self._run_worker,
            args=("hierarchy-search", request, query, index_path, self._hierarchy_model is None),
            name="rscheck-gui-hierarchy-search",
            daemon=True,
        )
        worker.start()

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
        if self._crg_source_mappings_dirty:
            messagebox.showerror(
                "映射未保存",
                "请先在 CRG Source 映射库页保存修改",
                parent=self.root,
            )
            return
        config_path = self.config_var.get().strip()
        try:
            path_matches = bool(
                self._loaded_config is not None
                and config_path
                and _same_config_path(config_path, self._loaded_config_path)
            )
        except GuiInputError as exc:
            messagebox.showerror("配置未加载", str(exc), parent=self.root)
            return
        if not path_matches:
            messagebox.showerror(
                "配置未加载",
                "配置路径已变化，请先点击“加载”同步模块规则库、Position 映射库和 CRG Source 映射库",
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
        self._active_action = action
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

    def _run_worker(
        self, action: str, request: GuiRunRequest, hierarchy_query: str = "",
        index_path: Path | None = None, needs_model: bool = False,
    ) -> None:
        try:
            if action == "hierarchy-search":
                if self._cancel_requested.is_set():
                    self.events.put(WorkerOutcome(action=action))
                    return
                command = build_position_hierarchy_search_command(
                    request, hierarchy_query, session_index=index_path
                )
                process = self.controller.run(command, cwd=self.project_root)
                if process.cancelled or self._cancel_requested.is_set():
                    self.events.put(WorkerOutcome(action=action, process=process))
                    return
                if process.returncode == 0:
                    result = load_position_hierarchy_search(process.stdout) if hierarchy_query else None
                    model = (ScopeTree.from_index(load_hierarchy_index(index_path))
                             if needs_model and index_path is not None else None)
                    self.events.put(
                        WorkerOutcome(
                            action=action,
                            process=process,
                            hierarchy_search=result,
                            hierarchy_tree=model,
                        )
                    )
                    return
                self.events.put(WorkerOutcome(action=action, process=process))
                return
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
                    rows, skipped = load_validation_result(process.stdout)
                    self.events.put(
                        WorkerOutcome(action=action, process=process, validation_rows=rows, skipped_rows=skipped)
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
        if self._active_action == "hierarchy-search":
            self.status_var.set("正在取消层次查询")
            self.position_hierarchy_status_var.set("正在取消")
        else:
            self.status_var.set("正在取消")
        self.cancel_button.configure(state="disabled")
        if self._position_hierarchy_dialog is not None:
            try:
                self.position_hierarchy_cancel_button.configure(state="disabled")
            except tk.TclError:
                pass
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
        self._active_action = ""
        hierarchy_action = outcome.action == "hierarchy-search"
        process = outcome.process
        if process is not None:
            self._append_log("$ " + format_command(process.command) + "\n")
            if process.stdout and not hierarchy_action:
                self._append_log(process.stdout.rstrip() + "\n")
            if process.stderr:
                self._append_log(process.stderr.rstrip() + "\n")
            self._append_log(f"[exit {process.returncode}]\n")
            if process.cancelled:
                if hierarchy_action:
                    self.position_hierarchy_status_var.set("查询已取消")
                    self.status_var.set("Verdi DUT 层次查询已取消")
                    if self._position_hierarchy_close_pending:
                        self._destroy_position_hierarchy_search()
                else:
                    self._reset_summary("CANCELLED")
                    self.status_var.set("已取消")
                return
        if self._cancel_requested.is_set():
            if hierarchy_action:
                self.position_hierarchy_status_var.set("查询已取消")
                self.status_var.set("Verdi DUT 层次查询已取消")
                if self._position_hierarchy_close_pending:
                    self._destroy_position_hierarchy_search()
            else:
                self._reset_summary("CANCELLED")
                self.status_var.set("已取消")
            return
        if outcome.error:
            self._append_log(outcome.error + "\n")
            if hierarchy_action:
                self.position_hierarchy_status_var.set("查询失败")
                self.status_var.set("Verdi DUT 层次查询失败")
                messagebox.showerror(
                    "层次查询失败",
                    outcome.error,
                    parent=self._position_hierarchy_dialog or self.root,
                )
            else:
                self._reset_summary("ERROR")
                self.status_var.set("执行失败")
                messagebox.showerror("执行失败", outcome.error, parent=self.root)
            return
        if process is None:
            if hierarchy_action:
                self.position_hierarchy_status_var.set("查询失败")
                self.status_var.set("Verdi DUT 层次查询失败")
            else:
                self._reset_summary("ERROR")
                self.status_var.set("执行失败")
            return
        if hierarchy_action and process.returncode == 0 and (outcome.hierarchy_search or outcome.hierarchy_tree):
            if outcome.hierarchy_tree is not None:
                self._hierarchy_model = outcome.hierarchy_tree
            result = outcome.hierarchy_search
            self._render_position_hierarchy_candidates(result)
            index = result if result is not None else outcome.hierarchy_tree.index
            self._append_log(
                "[DUT 层次] " + self.position_hierarchy_status_var.get()
                + f" · 已访问 {index.visited_scopes} 个 scope\n"
            )
            for notice in index.notices:
                self._append_log(f"[hierarchy notice] {notice}\n")
            for warning in index.warnings:
                self._append_log(f"[hierarchy warning] {warning}\n")
            self.status_var.set("Verdi DUT 层次查询完成")
            return
        if outcome.action == "validate" and process.returncode == 0:
            self._render_validation(outcome.validation_rows, outcome.skipped_rows)
            self.status_var.set(f"Excel 验证完成，参与检查 {len(outcome.validation_rows)} 行，整行跳过 {len(outcome.skipped_rows)} 行")
            self.notebook.select(self.results_tab)
            return
        if outcome.action == "check" and process.returncode in (0, 1) and outcome.report:
            self._render_report(outcome.report)
            self.status_var.set("没有需检查的行，未运行 RTL 检查" if not outcome.report.rows else
                                "检查完成，发现差异" if process.returncode else "部分行无法定位例化，详见匹配明细"
                                if outcome.report.summary.get("partial_rows") else "检查通过")
            self._last_json_path = self.json_report_var.get().strip()
            self._last_csv_path = self.csv_report_var.get().strip()
            self.notebook.select(self.results_tab)
            return

        detail = process.stderr.strip() or process.stdout.strip() or f"退出码 {process.returncode}"
        if hierarchy_action:
            self.position_hierarchy_status_var.set("查询失败")
            self.status_var.set("Verdi DUT 层次查询失败")
            messagebox.showerror(
                "层次查询失败",
                detail[-4000:],
                parent=self._position_hierarchy_dialog or self.root,
            )
        else:
            self.status_var.set("执行失败")
            self._reset_summary("ERROR")
            messagebox.showerror("执行失败", detail[-4000:], parent=self.root)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._set_setup_controls_disabled(running)
        state = "disabled" if running else "normal"
        self.validate_button.configure(state=state)
        self.run_button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        self._set_position_hierarchy_search_running(running)
        if running:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()
        self._status_color()

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
                + list(self.crg_sources_tab.winfo_children())
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
        # Filtering detaches items; they must also be deleted before reusing IDs.
        if self._result_records:
            self.result_tree.delete(*self._result_records)
        for item in self.finding_tree.get_children():
            self.finding_tree.delete(item)
        self._result_records.clear()
        self._finding_records.clear()
        self._issue_rows.clear()
        self.result_count_var.set("")
        self._show_result_empty("等待检查结果")
        self.finding_empty.configure(text="请选择一条检查结果")
        self.finding_empty.place(relx=.5, rely=.62, anchor="center")
        self.result_tree.column("position", width=520, stretch=False)
        self.result_tree.column("crg_source", width=520, stretch=False)
        self.result_tree.xview_moveto(0)
        self._display_evidence(None)

    def _show_result_empty(self, message: str) -> None:
        if message:
            self.result_empty.configure(text=message)
            self.result_empty.place(relx=.5, rely=.63, anchor="center")
        else:
            self.result_empty.place_forget()

    def _filter_results(self) -> None:
        selection = self.result_tree.selection()
        only = self.only_issues_var.get()
        ids = [i for i in self._result_records if not only or i in self._issue_rows]
        self.result_tree.set_children("", *ids)
        total = len(self._result_records)
        self.result_count_var.set(f"显示 {len(ids)} / {total} 项" if total else "")
        if ids:
            self._show_result_empty("")
            iid = selection[0] if selection and selection[0] in ids else ids[0]
            self.result_tree.selection_set(iid)
            self.result_tree.see(iid)
            self._on_result_selected()
        else:
            if selection:
                self.result_tree.selection_remove(*selection)
            self.finding_tree.delete(*self.finding_tree.get_children())
            self._finding_records.clear()
            self.finding_empty.configure(text="无选中行")
            self.finding_empty.place(relx=.5, rely=.62, anchor="center")
            self._display_evidence(None)
            self._show_result_empty("没有错误或警告行\n关闭「仅看问题」可查看全部记录。"
                                    if total else "尚无检查结果")

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

    def _fit_result_crg_source_column(self, value: str) -> None:
        try:
            required = tkfont.nametofont("TkDefaultFont").measure(value) + 24
        except Exception:
            required = len(value) * 9 + 24
        current = int(self.result_tree.column("crg_source", "width"))
        if required > current:
            self.result_tree.column(
                "crg_source",
                width=min(required, 2400),
                stretch=False,
            )

    def _reset_summary(self, state: str) -> None:
        self.summary_state_var.set(state)
        if not self._result_records:
            self._show_result_empty({
                "RUNNING": "任务运行中，完成后在此显示结果。",
                "ERROR": "未生成检查结果，请查看运行日志。",
                "CANCELLED": "本次任务已取消。",
            }.get(state, "尚无检查结果"))
        self.summary_rows_var.set("行数 0")
        self.summary_pass_var.set("通过 0")
        self.summary_fail_var.set("失败 0")
        self.summary_errors_var.set("错误 0")
        self.summary_warnings_var.set("警告 0")

    def _render_validation(self, rows: tuple[Mapping[str, Any], ...], skipped: tuple[Mapping[str, Any], ...] = ()) -> None:
        self._clear_results()
        self.summary_state_var.set("VALID" if rows else "SKIPPED")
        self.summary_rows_var.set(f"行数 {len(rows)}" + (f" · 跳过 {len(skipped)}" if skipped else ""))
        self.summary_pass_var.set(f"通过 {len(rows)}")
        self.summary_fail_var.set("失败 0")
        self.summary_errors_var.set("错误 0")
        total_warnings = 0
        for index, spec in enumerate(rows):
            iid = f"validation-{index}"
            position_text = _position_display(spec)
            crg_source_text = _crg_source_display(spec)
            self._fit_result_position_column(position_text)
            self._fit_result_crg_source_column(crg_source_text)
            findings = [{"severity": "warning", "code": "EXCEL_FIELD_BLANK",
                         "message": blank_field_message(spec.get("sheet", ""), spec["row"], field),
                         "actual": {"field": field, "reason": "blank"}}
                        for field, reason in spec.get("ignored_fields", {}).items() if reason == "blank"]
            self._result_records[iid] = {"spec": spec, "findings": findings, "matched_instances": []}
            total_warnings += len(findings)
            if findings:
                self._issue_rows.add(iid)
            self.result_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "WARNING" if findings else "VALID",
                    spec.get("row", ""),
                    spec.get("Intf_type", ""),
                    position_text,
                    crg_source_text,
                    "N/A",
                    _instance_display(spec, {}),
                    spec.get("RS_CFG_EN", ""),
                    "-",
                    f"-/{spec.get('step', '')}",
                    f"0E/{len(findings)}W",
                ),
                tags=("warning" if findings else "pass",),
            )
        self.summary_warnings_var.set(f"警告 {total_warnings}")
        self._filter_results()
        if not rows:
            self._show_result_empty("没有需检查的规格行；详见运行日志。")

    def _render_report(self, report: LoadedReport) -> None:
        self._clear_results()
        summary = report.summary
        passed = bool(summary["passed"])
        skipped = summary.get("skipped_rows", 0)
        partial = summary.get("partial_rows", 0)
        self.summary_state_var.set("FAIL" if not passed else "SKIPPED" if not report.rows else
                                   "PARTIAL" if partial else "PASS")
        self.summary_rows_var.set(f"行数 {summary['rows']}" + (f" · 跳过 {skipped}" if skipped else "")
                                  + (f" · 部分检查 {partial}" if partial else ""))
        self.summary_pass_var.set(f"通过 {summary['passed_rows']}")
        self.summary_fail_var.set(f"失败 {summary['failed_rows']}")
        self.summary_errors_var.set(f"错误 {summary['errors']}")
        self.summary_warnings_var.set(f"警告 {summary['warnings']}")

        if report.global_findings:
            global_passed = not any(
                item.get("severity") == "error" for item in report.global_findings
            )
            global_record: Mapping[str, Any] = {
                "spec": {
                    "row": "-",
                    "Intf_type": "GLOBAL",
                    "position": "",
                    "RS_inst": "",
                    "RS_CFG_EN": "",
                    "step": "",
                },
                "passed": global_passed,
                "matched_instances": [],
                "findings": list(report.global_findings),
            }
            self._insert_result_record("global", global_record)
        for index, row in enumerate(report.rows):
            self._insert_result_record(f"row-{index}", row)
        self._filter_results()
        if not report.rows:
            self._show_result_empty("没有需检查的行。跳过的行号和原因见运行日志或导出的报告。")

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
        status = "WARNING" if passed and warnings else ("PASS" if passed else "FAIL")
        if status == "PASS" and (record.get("instance_match") or {}).get("mode") == "none":
            status = "PARTIAL"
        if status != "PASS":
            self._issue_rows.add(iid)
        tag = "warning" if passed and warnings else ("pass" if passed else "fail")
        step_check = record.get("step_check", {})
        if not isinstance(step_check, Mapping):
            step_check = {}
        physical_instances = step_check.get("physical_instances", len(instances))
        effective_step = step_check.get("effective_step", len(instances))
        expected_step = step_check.get("expected", spec.get("step", ""))
        effective_text = "?" if effective_step is None else str(effective_step)
        position_text = _position_display(spec)
        crg_source_text = _crg_source_display(spec)
        self._fit_result_position_column(position_text)
        self._fit_result_crg_source_column(crg_source_text)
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
                crg_source_text,
                _crg_trace_display(record),
                _instance_display(spec, record),
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
        self._display_evidence(record)
        children = self.finding_tree.get_children()
        if children:
            self.finding_empty.place_forget()
            self.finding_tree.selection_set(children[0])
        else:
            self.finding_empty.configure(text="本行没有错误或警告" if "passed" in record else "此步骤仅验证 Excel")
            self.finding_empty.place(relx=.5, rely=.62, anchor="center")

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
        self._display_evidence(record, finding)

    def _display_evidence(self, record, finding=None) -> None:
        payload = _evidence_payload(record, finding) if record else {}
        sections = _review_sections(record, payload, finding) if record else [
            ("尚未选择结果", "选中结果行可查看拍数、实例匹配和诊断原因。")]
        self.review_text.configure(state="normal")
        self.review_text.delete("1.0", "end")
        for heading, body in sections:
            tags = ("heading", "problem") if heading.startswith(("ERROR", "需处理")) else (
                ("heading", "caution") if heading.startswith(("WARNING", "有警告")) else ("heading",))
            self.review_text.insert("end", heading + "\n", tags)
            self.review_text.insert("end", body + "\n", "body")
        self.review_text.configure(state="disabled")
        self.review_text.yview_moveto(0)
        # Findings on the same row don't change its match provenance or reset its scroll position.
        if not hasattr(self, "_match_record") or record is not self._match_record:
            self._match_record = record
            self.match_text.configure(state="normal")
            self.match_text.delete("1.0", "end")
            self.match_text.insert("1.0", format_match_details(record))
            self.match_text.configure(state="disabled")
            self.match_text.yview_moveto(0)
            self.copy_match_button.state(["!disabled"] if record and record.get("instance_match") else ["disabled"])
        self._set_evidence(json.dumps(payload, ensure_ascii=False, indent=2) if payload else "")

    def _set_evidence(self, value: str) -> None:
        self.evidence_text.configure(state="normal")
        self.evidence_text.delete("1.0", "end")
        self.evidence_text.insert("1.0", value)
        self.evidence_text.configure(state="disabled")

    def _append_log(self, value: str) -> None:
        self.log_empty.place_forget()
        self.log_text.configure(state="normal")
        tag = "command" if value.startswith("$ ") else "exit" if value.startswith("[exit ") else ""
        self.log_text.insert("end", value, tag)
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
        if (
            self._crg_source_mappings_dirty
            and not self._confirm_discard_crg_source_changes()
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
