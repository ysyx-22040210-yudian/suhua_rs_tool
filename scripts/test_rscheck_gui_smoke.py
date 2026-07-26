from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))

import rscheck.gui as gui_module
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE, LIVE_SOURCE, build_check_command
from rscheck.model import FIELD_NAMES, ModuleRule


_ONLINE_OPTIONS_WITH_VALUE = frozenset(
    {
        "--collector",
        "--column",
        "--config",
        "--csv-report",
        "--data-start-row",
        "--elab-db",
        "--excel",
        "--header-row",
        "--json-report",
        "--keep-inventory",
        "--npi-lib-dir",
        "--npi-timeout",
        "--sheet",
    }
)
_ONLINE_FLAG_OPTIONS = frozenset({"--header-check", "--no-header-check"})
_BUSINESS_HEADERS = (
    "接口分类",
    "模块类型",
    "实例组",
    "位置简称",
    "有效拍数",
    "时钟连接",
    "复位连接",
    "时钟源模块",
    "假门控标记",
)


def _window_identifier(root: object) -> str:
    return f"0x{int(root.winfo_id()):x}"


def _online_command_contract_errors(
    command_line: str,
    *,
    expected_command: Sequence[str],
    expected_collector: str,
    expected_elab_db: str,
) -> tuple[str, ...]:
    try:
        tokens = shlex.split(command_line)
    except ValueError as exc:
        return (f"command cannot be parsed: {exc}",)

    errors: list[str] = []
    if tokens != list(expected_command):
        errors.append("command differs from the exact GUI request")

    expected_prefix = [expected_command[0], "-m", "rscheck", "check"]
    if tokens[:4] != expected_prefix:
        errors.append("command prefix is not '<python> -m rscheck check'")

    option_values: dict[str, list[str]] = {}
    index = 4
    while index < len(tokens):
        option = tokens[index]
        if option in _ONLINE_FLAG_OPTIONS:
            option_values.setdefault(option, []).append("")
            index += 1
            continue
        if option in _ONLINE_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                errors.append(f"{option} has no value")
                break
            option_values.setdefault(option, []).append(tokens[index + 1])
            index += 2
            continue
        errors.append(f"unexpected option or positional argument: {option!r}")
        index += 1

    expected_sources = {
        "--collector": expected_collector,
        "--elab-db": expected_elab_db,
    }
    for option, expected_value in expected_sources.items():
        if option_values.get(option) != [expected_value]:
            errors.append(f"{option} must occur once with the requested value")
    return tuple(errors)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a visible rscheck GUI smoke test")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--collector")
    parser.add_argument("--elab-db")
    parser.add_argument("--npi-lib-dir")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--generated-rows",
        type=int,
        default=0,
        help="generate this many passing specification/inventory rows for load testing",
    )
    parser.add_argument("--visible-seconds", type=float, default=2.0)
    parser.add_argument("--start-delay", type=float, default=0.1)
    parser.add_argument(
        "--visible-tab",
        choices=("config", "positions", "rules", "results", "log"),
        default="results",
        help="tab left visible after the smoke assertions pass",
    )
    parser.add_argument("--negative", action="store_true")
    parser.add_argument(
        "--expect-partial-load",
        action="store_true",
        help="expect one non-fatal NPI_LOAD_PARTIAL global warning",
    )
    parser.add_argument(
        "--default-rule",
        action="store_true",
        help="run an offline passing case for an unregistered RS_module",
    )
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _write_generated_inputs(output: Path, row_count: int) -> tuple[Path, Path]:
    specs_path = output / "generated_specs.csv"
    inventory_path = output / "generated_inventory.json"
    positions = {}
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        for index in range(row_count):
            position = f"top.load_{index:05d}"
            prefix = f"PIPE_{index:05d}"
            instance_name = f"{prefix}_S0"
            writer.writerow(
                (
                    f"IF_{index:05d}",
                    "rs_pipe",
                    prefix,
                    position,
                    1,
                    "clk_rs",
                    "rst_n",
                    "crg_core",
                    "假门控",
                )
            )
            positions[position] = {
                "found": True,
                "instances": [
                    {
                        "name": instance_name,
                        "full_name": f"{position}.{instance_name}",
                        "module": "rs_pipe",
                        "file": "generated_rs_top.sv",
                        "line": index + 1,
                        "parameters": {
                            "RS_CFG_EN": "0",
                            "WIDTH": "1",
                            "rs_mode": "1",
                        },
                        "ports": {
                            "clk": {
                                "connection": f"{position}.clk_rs",
                                "type": "npiNet",
                            },
                            "rst": {
                                "connection": f"{position}.rst_n",
                                "type": "npiNet",
                            },
                        },
                        "clk_sources": [
                            {
                                "instance": f"{position}.u_crg",
                                "module": "crg_core",
                            }
                        ],
                    }
                ],
            }
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {"schema_version": 2, "positions": positions, "warnings": []},
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


def _write_default_rule_inputs(output: Path) -> tuple[Path, Path]:
    specs_path = output / "default_rule_specs.csv"
    inventory_path = output / "default_rule_inventory.json"
    position = "top.u_default"
    prefix = "DEFAULT_RS"
    module = "rs_default_pipe"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "DEFAULT_IF",
                module,
                prefix,
                position,
                2,
                "clk_default",
                "rst_n",
                "crg_default",
                "假门控",
            )
        )
    instances = []
    for index in range(2):
        name = f"{prefix}_C{index}"
        instances.append(
            {
                "name": name,
                "full_name": f"{position}.{name}",
                "module": module,
                "file": "default_rule_top.sv",
                "line": index + 10,
                "parameters": {
                    "RS_CFG_EN": "0",
                    "WIDTH": "8",
                    "ignored_mode": "0",
                },
                "ports": {
                    "clk": {
                        "connection": f"{position}.clk_default",
                        "type": "npiNet",
                    },
                    "rst": {
                        "connection": f"{position}.rst_n",
                        "type": "npiNet",
                    },
                },
                "clk_sources": [
                    {
                        "instance": f"{position}.u_crg",
                        "module": "crg_default",
                    }
                ],
            }
        )
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 2,
                "positions": {
                    position: {
                        "found": True,
                        "instances": instances,
                    }
                },
                "warnings": [],
            },
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


def _read_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label} JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} JSON root must be an object: {path}")
    return value


def main() -> int:
    args = _parser().parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be at least 1")
    if args.generated_rows < 0:
        raise SystemExit("--generated-rows must not be negative")
    if args.start_delay < 0:
        raise SystemExit("--start-delay must not be negative")
    project_root = Path(args.project_root).resolve()
    online = bool(args.collector or args.elab_db)
    if online and not (args.collector and args.elab_db):
        raise SystemExit("--collector and --elab-db must be provided together")
    if online and args.generated_rows:
        raise SystemExit("--generated-rows is available only in offline inventory mode")
    if args.expect_partial_load and not online:
        raise SystemExit("--expect-partial-load requires online NPI mode")
    if args.expect_partial_load and (
        args.negative or args.default_rule or args.generated_rows or args.validate_only
    ):
        raise SystemExit(
            "--expect-partial-load requires a positive online full check"
        )
    if args.negative and args.generated_rows:
        raise SystemExit("--negative and --generated-rows are mutually exclusive")
    if args.default_rule and online:
        raise SystemExit("--default-rule is available only in offline inventory mode")
    if args.default_rule and (args.negative or args.generated_rows):
        raise SystemExit(
            "--default-rule is mutually exclusive with --negative and --generated-rows"
        )
    if args.default_rule and args.validate_only:
        raise SystemExit("--default-rule requires a full check, not --validate-only")

    root = tk.Tk()
    app = RsCheckApp(root)
    root.geometry("980x680")
    root.title("RTL RS Check GUI Smoke")
    root.update()
    config_buttons = (
        (app.config_import_button, "导入"),
        (app.config_reload_button, "加载"),
        (app.config_export_button, "导出"),
    )
    previous_right = 0
    window_right = root.winfo_rootx() + root.winfo_width()
    for button, expected_text in config_buttons:
        if str(button.cget("text")) != expected_text or not button.winfo_ismapped():
            raise SystemExit(
                f"GUI config button is missing or clipped: {expected_text}"
            )
        left = button.winfo_rootx()
        right = left + button.winfo_width()
        if button.winfo_width() <= 1 or left < previous_right or right > window_right:
            raise SystemExit(
                f"GUI config button layout overlaps or overflows: {expected_text}"
            )
        previous_right = right
    modal_errors: list[str] = []

    def capture_error(title: str, message: str, **_kwargs: object) -> None:
        modal_errors.append(f"{title}: {message}")

    gui_module.messagebox.showerror = capture_error
    gui_module.messagebox.showinfo = lambda *_args, **_kwargs: None
    gui_module.messagebox.askyesno = lambda *_args, **_kwargs: True
    temp = tempfile.TemporaryDirectory(prefix="rscheck-gui-smoke-")
    output = Path(temp.name)
    config_path = output / "rscheck.gui-smoke.json"
    source_config_text = (project_root / "config" / "rscheck.example.json").read_text(
        encoding="utf-8"
    )
    source_config = json.loads(source_config_text)
    original_position_mappings = source_config.get("position_mappings", {})
    if original_position_mappings.get("tile_core") != "top.u_tile":
        raise SystemExit(
            "sample config must map position alias 'tile_core' to 'top.u_tile'"
        )
    original_module_rules = source_config.get("module_rules", {})
    if args.default_rule and "rs_default_pipe" in original_module_rules:
        raise SystemExit("default-rule smoke module must not be explicitly registered")
    config_path.write_text(source_config_text, encoding="utf-8")
    app.config_var.set(str(config_path))
    app._load_config_from_form()
    if source_config.get("excel", {}).get("validate_headers") is not False:
        raise SystemExit("sample config must disable strict header validation by default")
    if app.header_check_var.get():
        raise SystemExit("GUI loaded strict header validation as enabled by default")

    app.position_alias_var.set("gui_smoke_position")
    app.position_path_var.set("top.u_gui_smoke")
    app._apply_position_mapping()
    if not app._position_mappings_dirty:
        raise SystemExit("GUI position mapping edit did not set dirty state")
    app._save_position_mappings()
    if app._position_mappings_dirty:
        raise SystemExit("GUI position mapping save did not clear dirty state")
    app._load_config_from_form()
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    if saved_config.get("position_mappings", {}).get("gui_smoke_position") != (
        "top.u_gui_smoke"
    ):
        raise SystemExit("GUI position mapping save/reload failed")
    app.position_search_var.set("gui_smoke")
    filtered_positions = app.position_tree.get_children()
    if (
        len(filtered_positions) != 1
        or app._position_tree_aliases.get(filtered_positions[0])
        != "gui_smoke_position"
    ):
        raise SystemExit("GUI position mapping search did not isolate the saved entry")
    app.position_tree.selection_set(filtered_positions[0])
    app._on_position_selected()
    app.position_path_var.set("top.u_gui_smoke_updated")
    app._apply_position_mapping()
    if not app._position_mappings_dirty:
        raise SystemExit("GUI position mapping update did not set dirty state")
    app._save_position_mappings()
    if app._position_mappings_dirty:
        raise SystemExit("GUI position mapping update save did not clear dirty state")
    app._load_config_from_form()
    updated_config = json.loads(config_path.read_text(encoding="utf-8"))
    if updated_config.get("position_mappings", {}).get("gui_smoke_position") != (
        "top.u_gui_smoke_updated"
    ):
        raise SystemExit("GUI position mapping update/reload failed")
    app.position_search_var.set("gui_smoke")
    filtered_positions = app.position_tree.get_children()
    if len(filtered_positions) != 1:
        raise SystemExit("GUI position mapping disappeared before delete")
    app.position_tree.selection_set(filtered_positions[0])
    app._on_position_selected()
    app._delete_position_mapping()
    if not app._position_mappings_dirty:
        raise SystemExit("GUI position mapping delete did not set dirty state")
    app._save_position_mappings()
    if app._position_mappings_dirty:
        raise SystemExit("GUI position mapping delete save did not clear dirty state")
    app._load_config_from_form()
    restored_config = json.loads(config_path.read_text(encoding="utf-8"))
    if restored_config.get("position_mappings", {}) != original_position_mappings:
        raise SystemExit(
            "GUI position mapping delete did not restore the original mapping set"
        )
    app.position_search_var.set("tile_core")
    restored_positions = app.position_tree.get_children()
    if (
        len(restored_positions) != 1
        or app._position_tree_aliases.get(restored_positions[0]) != "tile_core"
    ):
        raise SystemExit("GUI position mapping search did not find tile_core after restore")

    app.module_name_var.set("gui_smoke_rule")
    app.module_has_rs_cfg_en_var.set(False)
    app.module_step_parameters_var.set("smoke_mode")
    app._apply_rule()
    if not app._module_rules_dirty:
        raise SystemExit("GUI module rule edit did not set dirty state")
    app._save_module_rules()
    if app._module_rules_dirty:
        raise SystemExit("GUI module rule save did not clear dirty state")
    app._load_config_from_form()
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    if saved_config.get("module_rules", {}).get("gui_smoke_rule") != {
        "has_rs_cfg_en": False,
        "step_parameters": ["smoke_mode"],
    }:
        raise SystemExit("GUI module rule save/reload failed")
    app.module_search_var.set("gui_smoke")
    filtered_rules = app.rule_tree.get_children()
    if (
        len(filtered_rules) != 1
        or app._rule_tree_names.get(filtered_rules[0]) != "gui_smoke_rule"
    ):
        raise SystemExit("GUI module rule search did not isolate the saved rule")
    app.rule_tree.selection_set(filtered_rules[0])
    app._on_rule_selected()
    app.module_step_parameters_var.set("smoke_mode, extra_mode")
    app._apply_rule()
    if not app._module_rules_dirty:
        raise SystemExit("GUI module rule update did not set dirty state")
    app._save_module_rules()
    if app._module_rules_dirty:
        raise SystemExit("GUI module rule update save did not clear dirty state")
    app._load_config_from_form()
    updated_config = json.loads(config_path.read_text(encoding="utf-8"))
    if updated_config.get("module_rules", {}).get("gui_smoke_rule") != {
        "has_rs_cfg_en": False,
        "step_parameters": ["smoke_mode", "extra_mode"],
    }:
        raise SystemExit("GUI module rule update/reload failed")
    app.module_search_var.set("gui_smoke")
    filtered_rules = app.rule_tree.get_children()
    if len(filtered_rules) != 1:
        raise SystemExit("GUI module rule disappeared before delete")
    app.rule_tree.selection_set(filtered_rules[0])
    app._on_rule_selected()
    app._delete_rule()
    if not app._module_rules_dirty:
        raise SystemExit("GUI module rule delete did not set dirty state")
    app._save_module_rules()
    if app._module_rules_dirty:
        raise SystemExit("GUI module rule delete save did not clear dirty state")
    app._load_config_from_form()
    restored_config = json.loads(config_path.read_text(encoding="utf-8"))
    if restored_config.get("module_rules", {}) != original_module_rules:
        raise SystemExit("GUI module rule delete did not restore the original rule set")
    app.module_search_var.set("rs_pipe")
    if len(app.rule_tree.get_children()) != 1:
        raise SystemExit("GUI module rule search did not find rs_pipe after restore")

    active_path_before_export = app.config_var.get()
    rtl_before_export = app._loaded_config.rtl
    export_path = output / "rscheck.complete-config.export.json"
    app.sheet_var.set("PortableSmoke")
    app.header_row_var.set("4")
    app.data_start_row_var.set("6")
    app.header_check_var.set(True)
    for index, name in enumerate(FIELD_NAMES, start=20):
        app.column_vars[name].set(str(index))
    app._module_rules["gui_unsaved_rule"] = ModuleRule(
        "gui_unsaved_rule", False, ("smoke_enable",)
    )
    app._position_mappings["gui_unsaved_position"] = "top.u_gui_unsaved"
    app._module_rules_dirty = True
    app._position_mappings_dirty = True
    gui_module.filedialog.asksaveasfilename = (
        lambda **_kwargs: str(export_path)
    )
    app._export_config()
    if app.config_var.get() != active_path_before_export:
        raise SystemExit("GUI config export unexpectedly changed the active config path")
    if not app._module_rules_dirty or not app._position_mappings_dirty:
        raise SystemExit("GUI config export unexpectedly cleared database dirty state")
    exported_config = _read_json_object(export_path, "exported config")
    expected_roots = {
        "excel",
        "columns",
        "rtl",
        "position_mappings",
        "module_rules",
    }
    if set(exported_config) != expected_roots:
        raise SystemExit(
            "GUI config export root sections mismatch: "
            f"{sorted(exported_config)!r}"
        )
    if exported_config.get("rtl") != source_config.get("rtl"):
        raise SystemExit("GUI config export did not preserve the complete RTL config")
    if exported_config.get("excel") != {
        "sheet": "PortableSmoke",
        "header_row": 4,
        "data_start_row": 6,
        "validate_headers": True,
    }:
        raise SystemExit("GUI config export did not preserve current Excel settings")
    expected_columns = {
        name: index for index, name in enumerate(FIELD_NAMES, start=20)
    }
    if exported_config.get("columns") != expected_columns:
        raise SystemExit("GUI config export did not preserve all column mappings")
    if exported_config.get("module_rules", {}).get("gui_unsaved_rule") != {
        "has_rs_cfg_en": False,
        "step_parameters": ["smoke_enable"],
    }:
        raise SystemExit("GUI config export omitted an unsaved module rule")
    if exported_config.get("position_mappings", {}).get(
        "gui_unsaved_position"
    ) != "top.u_gui_unsaved":
        raise SystemExit("GUI config export omitted an unsaved position mapping")
    active_config = _read_json_object(config_path, "active config")
    if "gui_unsaved_rule" in active_config.get("module_rules", {}) or (
        "gui_unsaved_position" in active_config.get("position_mappings", {})
    ):
        raise SystemExit("GUI config export modified the active config file")

    app.sheet_var.set("not imported")
    app._module_rules.clear()
    app._position_mappings.clear()
    gui_module.filedialog.askopenfilename = lambda **_kwargs: str(export_path)
    app._import_config()
    if app.config_var.get() != str(export_path):
        raise SystemExit("GUI config import did not switch the active config path")
    if app._module_rules_dirty or app._position_mappings_dirty:
        raise SystemExit("GUI config import did not clear database dirty state")
    if app.sheet_var.get() != "PortableSmoke":
        raise SystemExit("GUI config import did not restore Excel settings")
    if "gui_unsaved_rule" not in app._module_rules:
        raise SystemExit("GUI config import did not restore the module rule database")
    if "gui_unsaved_position" not in app._position_mappings:
        raise SystemExit("GUI config import did not restore the position database")
    if app._loaded_config is None or app._loaded_config.rtl != rtl_before_export:
        raise SystemExit("GUI config import did not restore the RTL configuration")

    app.config_var.set(str(config_path))
    app._load_config_from_form()
    if app.config_var.get() != str(config_path):
        raise SystemExit("GUI smoke failed to restore its active config after I/O test")
    config_io_verified = True
    expected_rows = (
        1 if args.negative or args.default_rule else args.generated_rows or 2
    )
    expected_tree_rows = expected_rows + (1 if args.expect_partial_load else 0)
    sample_position_mapping = not (
        args.negative or args.default_rule or args.generated_rows
    )
    if args.default_rule:
        excel_path, inventory_path = _write_default_rule_inputs(output)
    elif args.generated_rows:
        excel_path, inventory_path = _write_generated_inputs(
            output, args.generated_rows
        )
    else:
        excel_path = (
            project_root / "tests" / "fixtures" / "specs_negative.csv"
            if args.negative
            else project_root / "examples" / "specs.csv"
        )
        inventory_path = project_root / "tests" / "fixtures" / "inventory.json"
    app.excel_var.set(str(excel_path))
    app.config_var.set(str(config_path))
    app.sheet_var.set("1")
    report_path = output / "report.json"
    csv_report_path = output / "report.csv"
    kept_inventory_path = output / "inventory.json"
    app.json_report_var.set(str(report_path))
    app.csv_report_var.set(str(csv_report_path))

    if online:
        app.source_mode_var.set(LIVE_SOURCE)
        app.collector_var.set(args.collector)
        app.elab_db_var.set(args.elab_db)
        app.npi_lib_var.set(args.npi_lib_dir or "")
        app.npi_timeout_var.set(str(args.timeout))
        app.keep_inventory_var.set(str(kept_inventory_path))
    else:
        app.source_mode_var.set(INVENTORY_SOURCE)
        app.inventory_var.set(str(inventory_path))
    app._update_source_mode()
    operation = "validate" if args.validate_only else "check"

    deadline = time.monotonic() + (args.timeout + 30) * args.iterations
    completed = 0
    started = False
    failed = False
    timed_out = False
    cancel_deadline: float | None = None
    window_id = ""
    window_reported = False
    window_seen = False

    def poll() -> None:
        nonlocal cancel_deadline, completed, failed, started, timed_out
        nonlocal window_id, window_reported, window_seen
        window_seen = window_seen or bool(
            root.winfo_ismapped() and root.winfo_viewable()
        )
        if window_seen and not window_reported:
            window_id = _window_identifier(root)
            print(
                f"GUI_SMOKE_WINDOW: window=mapped window_id={window_id}",
                flush=True,
            )
            window_reported = True
        if not started:
            started = True
            app._start_operation(operation)
            root.after(100, poll)
            return
        if timed_out:
            if app.running or app.controller.is_running:
                if cancel_deadline is not None and time.monotonic() > cancel_deadline:
                    print(
                        "GUI_SMOKE_FAIL: cancellation did not finish within 15 seconds",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                root.after(100, poll)
                return
            root.destroy()
            return
        if time.monotonic() > deadline:
            failed = True
            timed_out = True
            cancel_deadline = time.monotonic() + 15
            print("GUI_SMOKE_FAIL: timeout", file=sys.stderr)
            app._cancel_operation()
            root.after(100, poll)
            return
        if app.running:
            root.after(100, poll)
            return
        expected_state = (
            "VALID" if args.validate_only else "FAIL" if args.negative else "PASS"
        )
        if app.summary_state_var.get() != expected_state:
            failed = True
            print(
                "GUI_SMOKE_FAIL: "
                f"state={app.summary_state_var.get()} status={app.status_var.get()} "
                f"modal_errors={modal_errors}",
                file=sys.stderr,
            )
            root.destroy()
            return
        if len(app.result_tree.get_children()) != expected_tree_rows:
            failed = True
            print(
                f"GUI_SMOKE_FAIL: expected {expected_tree_rows} result rows",
                file=sys.stderr,
            )
            root.destroy()
            return
        root.update_idletasks()
        position_width = int(app.result_tree.column("position", "width"))
        initial_xview = app.result_tree.xview()
        if (
            position_width < 520
            or not str(app.result_tree.cget("xscrollcommand"))
            or initial_xview[1] >= 1.0
        ):
            failed = True
            print(
                "GUI_SMOKE_FAIL: result Position column is not horizontally "
                f"scrollable: width={position_width} xview={initial_xview!r}",
                file=sys.stderr,
            )
            root.destroy()
            return
        app.result_tree.xview_moveto(1.0)
        root.update_idletasks()
        if app.result_tree.xview()[0] <= 0.0:
            failed = True
            print(
                "GUI_SMOKE_FAIL: result horizontal scrollbar did not move",
                file=sys.stderr,
            )
            root.destroy()
            return
        app.result_tree.xview_moveto(0.0)
        if sample_position_mapping:
            for iid, record in app._result_records.items():
                if iid == "global":
                    continue
                spec = record.get("spec", {})
                if (
                    not isinstance(spec, dict)
                    or spec.get("position") != "top.u_tile"
                    or spec.get("position_alias") != "tile_core"
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: position mapping evidence mismatch "
                        f"{spec!r}",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
        if not args.validate_only and not args.generated_rows:
            first_result = "row-0"
            first_values = app.result_tree.item(first_result, "values")
            expected_group = "DEFAULT_RS" if args.default_rule else "AAAA_BBB"
            expected_instances = "2" if args.default_rule else "6"
            expected_step_cell = (
                "2/2" if args.default_rule else "5/6" if args.negative else "5/5"
            )
            if (
                len(first_values) < 8
                or str(first_values[4]) != expected_group
                or str(first_values[6]) != expected_instances
                or str(first_values[7]) != expected_step_cell
            ):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: first result row does not display the expected "
                    f"physical/effective steps: {first_values!r}",
                    file=sys.stderr,
                )
                root.destroy()
                return
        if not args.validate_only:
            try:
                raw_report = _read_json_object(report_path, "report")
                raw_inventory = _read_json_object(
                    kept_inventory_path if online else inventory_path,
                    "inventory",
                )
            except RuntimeError as exc:
                failed = True
                print(f"GUI_SMOKE_FAIL: {exc}", file=sys.stderr)
                root.destroy()
                return
            if raw_report.get("schema_version") != 3:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: report schema is not v3: "
                    f"{raw_report.get('schema_version')!r}",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if raw_inventory.get("schema_version") != 2:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: inventory schema is not v2: "
                    f"{raw_inventory.get('schema_version')!r}",
                    file=sys.stderr,
                )
                root.destroy()
                return
            global_findings = raw_report.get("global_findings")
            inventory_notices = raw_inventory.get("notices", [])
            if args.expect_partial_load:
                if (
                    not isinstance(global_findings, list)
                    or len(global_findings) != 1
                    or global_findings[0].get("severity") != "warning"
                    or global_findings[0].get("code") != "NPI_LOAD_PARTIAL"
                    or not isinstance(inventory_notices, list)
                    or len(inventory_notices) != 1
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: partial-load warning evidence is missing",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                global_values = app.result_tree.item("global", "values")
                if (
                    len(global_values) < 9
                    or str(global_values[0]) != "PASS"
                    or str(global_values[2]) != "GLOBAL"
                    or str(global_values[8]) != "0E/1W"
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: GLOBAL warning row is not displayed as PASS",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            elif global_findings or inventory_notices:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: unexpected global findings or inventory notices",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if sample_position_mapping:
                inventory_positions = raw_inventory.get("positions")
                if (
                    not isinstance(inventory_positions, dict)
                    or set(inventory_positions) != {"top.u_tile"}
                    or "tile_core" in inventory_positions
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: NPI inventory positions are not resolved "
                        f"full paths only: {inventory_positions!r}",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            expected_label = "真门控" if args.negative else "假门控"
            for iid, record in app._result_records.items():
                if iid == "global":
                    continue
                spec = record.get("spec", {})
                if (
                    not isinstance(spec, dict)
                    or spec.get("RS_CFG_EN") != expected_label
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: RS_CFG_EN spec evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                for instance in record.get("matched_instances", []):
                    parameters = instance.get("parameters")
                    if (
                        not isinstance(parameters, dict)
                        or parameters.get("RS_CFG_EN") != "0"
                    ):
                        failed = True
                        print(
                            "GUI_SMOKE_FAIL: RS_CFG_EN parameter evidence mismatch",
                            file=sys.stderr,
                        )
                        root.destroy()
                        return
                module_rule = record.get("module_rule")
                step_check = record.get("step_check")
                expected_step_parameters = [] if args.default_rule else ["rs_mode"]
                if (
                    not isinstance(module_rule, dict)
                    or module_rule.get("name") != spec.get("RS_module")
                    or module_rule.get("has_rs_cfg_en") is not True
                    or module_rule.get("step_parameters") != expected_step_parameters
                    or not isinstance(step_check, dict)
                    or not isinstance(step_check.get("effective_step"), int)
                    or (
                        not args.negative
                        and step_check.get("effective_step") != spec.get("step")
                    )
                    or step_check.get("physical_instances")
                    != len(record.get("matched_instances", []))
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: module rule or effective step evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                instances = record.get("matched_instances", [])
                contributions = step_check.get("contributions")
                if (
                    not isinstance(instances, list)
                    or not isinstance(contributions, list)
                    or len(contributions) != len(instances)
                    or step_check.get("expected") != spec.get("step")
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: contribution cardinality or expected step mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                contribution_values: list[int] = []
                contribution_evidence_valid = True
                for instance, contribution in zip(instances, contributions):
                    if not isinstance(instance, dict) or not isinstance(
                        contribution, dict
                    ):
                        contribution_evidence_valid = False
                        break
                    value = contribution.get("contribution")
                    parameter_evidence = contribution.get("parameters")
                    rs_mode_evidence = (
                        parameter_evidence.get("rs_mode")
                        if isinstance(parameter_evidence, dict)
                        else None
                    )
                    expected_state = "zero" if value == 0 else "nonzero"
                    if (
                        type(value) is not int
                        or value not in (0, 1)
                        or contribution.get("instance") != instance.get("full_name")
                        or instance.get("step_evaluation") != contribution
                    ):
                        contribution_evidence_valid = False
                        break
                    if args.default_rule:
                        if parameter_evidence != {} or value != 1:
                            contribution_evidence_valid = False
                            break
                    elif (
                        not isinstance(rs_mode_evidence, dict)
                        or rs_mode_evidence.get("present") is not True
                        or rs_mode_evidence.get("raw_value")
                        != instance.get("parameters", {}).get("rs_mode")
                        or rs_mode_evidence.get("state") != expected_state
                    ):
                        contribution_evidence_valid = False
                        break
                    contribution_values.append(value)
                if not contribution_evidence_valid or sum(
                    contribution_values
                ) != step_check.get("effective_step"):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: per-instance step contribution evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if spec.get("RS_inst") == "AAAA_BBB" and (
                    step_check.get("physical_instances") != 6
                    or step_check.get("effective_step") != 5
                    or contribution_values != [1, 1, 0, 1, 1, 1]
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: sample dynamic-step evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.default_rule and (
                    spec.get("RS_module") != "rs_default_pipe"
                    or spec.get("RS_module") in original_module_rules
                    or spec.get("RS_inst") != "DEFAULT_RS"
                    or step_check.get("expected") != 2
                    or step_check.get("physical_instances") != 2
                    or step_check.get("effective_step") != 2
                    or contribution_values != [1, 1]
                    or any(
                        instance.get("module") != "rs_default_pipe"
                        or instance.get("parameters", {}).get("ignored_mode") != "0"
                        for instance in instances
                    )
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: unregistered module default-rule evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.negative:
                    finding_codes = {
                        finding.get("code")
                        for finding in record.get("findings", [])
                        if isinstance(finding, dict)
                    }
                    required_codes = {
                        "RS_CFG_EN_LABEL_MISMATCH",
                        "STEP_MISMATCH",
                    }
                    if not required_codes.issubset(finding_codes):
                        failed = True
                        print(
                            "GUI_SMOKE_FAIL: negative case is missing required findings "
                            f"{sorted(required_codes - finding_codes)!r}",
                            file=sys.stderr,
                        )
                        root.destroy()
                        return
            if args.expect_partial_load:
                app.result_tree.selection_set("row-0")
            app._on_result_selected()
            if args.negative:
                finding_children = app.finding_tree.get_children()
                if finding_children:
                    app.finding_tree.selection_set(finding_children[0])
                    app._on_finding_selected()
            try:
                evidence = json.loads(app.evidence_text.get("1.0", "end"))
            except (json.JSONDecodeError, TypeError):
                failed = True
                print("GUI_SMOKE_FAIL: evidence panel is not JSON", file=sys.stderr)
                root.destroy()
                return
            evidence_spec = evidence.get("spec")
            evidence_instances = evidence.get("matched_instances")
            evidence_step = evidence.get("step_check")
            evidence_rule = evidence.get("module_rule")
            if (
                not isinstance(evidence_spec, dict)
                or evidence_spec.get("RS_CFG_EN") != expected_label
                or not isinstance(evidence_instances, list)
                or not evidence_instances
                or not isinstance(evidence_step, dict)
                or not isinstance(evidence_rule, dict)
            ):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: evidence panel lost row or instance context",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if args.negative and not isinstance(evidence.get("finding"), dict):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: finding evidence lost row context",
                    file=sys.stderr,
                )
                root.destroy()
                return
        if app.summary_rows_var.get() != f"行数 {expected_rows}":
            failed = True
            print(f"GUI_SMOKE_FAIL: {app.summary_rows_var.get()}", file=sys.stderr)
            root.destroy()
            return
        if (
            args.validate_only or not args.negative
        ) and app.summary_errors_var.get() != "错误 0":
            failed = True
            print(f"GUI_SMOKE_FAIL: {app.summary_errors_var.get()}", file=sys.stderr)
            root.destroy()
            return
        if (
            not args.validate_only
            and args.negative
            and app.summary_errors_var.get() == "错误 0"
        ):
            failed = True
            print("GUI_SMOKE_FAIL: negative case has no errors", file=sys.stderr)
            root.destroy()
            return
        expected_warning_text = "警告 1" if args.expect_partial_load else "警告 0"
        if app.summary_warnings_var.get() != expected_warning_text:
            failed = True
            print(f"GUI_SMOKE_FAIL: {app.summary_warnings_var.get()}", file=sys.stderr)
            root.destroy()
            return
        if not window_seen:
            failed = True
            print("GUI_SMOKE_FAIL: Tk window was never mapped", file=sys.stderr)
            root.destroy()
            return
        if online:
            command_lines = [
                line[2:]
                for line in app.log_text.get("1.0", "end").splitlines()
                if line.startswith("$ ")
            ]
            expected_command_count = completed + 1
            if len(command_lines) != expected_command_count:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: online command count mismatch "
                    f"expected={expected_command_count} actual={len(command_lines)}",
                    file=sys.stderr,
                )
                root.destroy()
                return
            expected_command = build_check_command(
                app._request(),
                internal_json_report=app.json_report_var.get(),
            )
            contract_errors = _online_command_contract_errors(
                command_lines[-1],
                expected_command=expected_command,
                expected_collector=args.collector or "",
                expected_elab_db=args.elab_db or "",
            )
            if contract_errors:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: online command contract mismatch "
                    + "; ".join(contract_errors),
                    file=sys.stderr,
                )
                root.destroy()
                return
        completed += 1
        if completed < args.iterations:
            app._start_operation(operation)
            root.after(100, poll)
            return
        print(
            "GUI_SMOKE_PASS: "
            f"state={app.summary_state_var.get()} rows={app.summary_rows_var.get()} "
            f"errors={app.summary_errors_var.get()} "
            f"warnings={app.summary_warnings_var.get()} "
            f"mode={'validate' if args.validate_only else 'online' if online else 'offline'} "
            f"case={'negative' if args.negative else 'default-rule' if args.default_rule else 'partial-load' if args.expect_partial_load else 'positive'} "
            f"iterations={completed} "
            f"window=mapped window_id={window_id}"
            " header-map=column-index strict-header=false"
            f"{' contract=elab-only' if online else ''}"
            f"{' position-map=tile_core->top.u_tile' if sample_position_mapping else ''}"
            f"{' npi-positions=full-path-only' if sample_position_mapping and not args.validate_only else ''}"
            f"{' rule=unregistered-default has-rs-cfg-en=true step-parameters=[] physical=2 effective=2 contributions=1,1' if args.default_rule else ''}"
            f"{' notice=NPI_LOAD_PARTIAL' if args.expect_partial_load else ''}"
            f"{' config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules' if config_io_verified else ''}"
            f"{' schemas=report-v3/inventory-v2' if not args.validate_only else ''}",
            flush=True,
        )
        visible_tabs = {
            "config": app.setup_tab,
            "positions": app.positions_tab,
            "rules": app.rules_tab,
            "results": app.results_tab,
            "log": app.log_tab,
        }
        app.notebook.select(visible_tabs[args.visible_tab])
        root.after(max(100, int(args.visible_seconds * 1000)), root.destroy)

    root.after(max(0, int(args.start_delay * 1000)), poll)
    root.mainloop()
    temp.cleanup()
    return 1 if failed or modal_errors or completed != args.iterations else 0


if __name__ == "__main__":
    raise SystemExit(main())
