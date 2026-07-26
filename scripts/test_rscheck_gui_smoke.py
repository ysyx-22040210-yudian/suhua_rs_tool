from __future__ import annotations

import argparse
import csv
import json
import shlex
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
        "--crg-trace-max-depth",
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
_RS_CFG_DONTCARE_TEXT = "任意非标准文本"
_RS_CFG_NA_TEXT = "NA"
_CRG_SOURCE_ALIAS = "core_clock_source"
_CRG_SOURCE_FULL_PATH = "top.u_soc.u_crg_core"


def _single_csv_crg_trace_evaluation(
    rows: Sequence[Mapping[str, str | None]],
) -> Mapping[str, object]:
    if len(rows) != 1:
        return {}
    raw_evidence = rows[0].get("crg_trace_evidence")
    if not isinstance(raw_evidence, str):
        return {}
    try:
        evidence = json.loads(raw_evidence)
    except json.JSONDecodeError:
        return {}
    if (
        isinstance(evidence, list)
        and len(evidence) == 1
        and isinstance(evidence[0], dict)
    ):
        return evidence[0]
    return {}


def _install_callback_fail_fast(
    root: Any,
    mark_failed: Callable[[], None],
) -> None:
    def fail_on_callback_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: object,
    ) -> None:
        mark_failed()
        print("GUI_SMOKE_FAIL: unhandled Tk callback exception", file=sys.stderr)
        traceback.print_exception(
            exc_type,
            exc_value,
            exc_traceback,
            file=sys.stderr,
        )
        sys.stderr.flush()
        root.destroy()

    root.report_callback_exception = fail_on_callback_exception


def _clock_trace_fields(
    clock_port: str,
    modules: Sequence[tuple[str, str]],
    *,
    max_depth: int = 16,
    status: str = "complete",
    diagnostics: Sequence[str] = (),
) -> dict[str, object]:
    path: list[str] = []
    traced_modules = []
    for depth, (instance, module) in enumerate(modules, start=1):
        path.append(instance)
        traced_modules.append(
            {
                "instance": instance,
                "module": module,
                "depth": depth,
                "path": list(path),
            }
        )
    return {
        "clk_sources": [
            {"instance": instance, "module": module}
            for instance, module in modules
        ],
        "clock_trace": {
            "clock_port": clock_port,
            "max_depth": max_depth,
            "excluded_inputs": ["clk", "rst_n"],
            "status": status,
            "modules": traced_modules,
            "diagnostics": list(diagnostics),
        },
    }


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
        choices=(
            "config",
            "positions",
            "crg-sources",
            "rules",
            "results",
            "log",
        ),
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
    parser.add_argument(
        "--custom-port",
        action="store_true",
        help="run an online passing case whose module uses clock_i/reset_ni",
    )
    parser.add_argument(
        "--clk-without-rst",
        action="store_true",
        help="prove an existing clk is found when the configured rst is absent",
    )
    parser.add_argument(
        "--rs-cfg-dontcare",
        action="store_true",
        help="prove Excel RS_CFG_EN is ignored when the module rule declares none",
    )
    parser.add_argument(
        "--rs-cfg-na",
        action="store_true",
        help="prove Excel RS_CFG_EN=NA skips gating checks for this row",
    )
    parser.add_argument(
        "--crg-source-mapping",
        action="store_true",
        help="prove an Excel CRG_source alias resolves to its RTL full path",
    )
    parser.add_argument(
        "--crg-depth-limit",
        action="store_true",
        help="run the online three-level CRG path with a maximum depth of two",
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
                    f"{position}.u_crg",
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
                            "RS_CRG_EN": "0",
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
                        **_clock_trace_fields(
                            "clk", [(f"{position}.u_crg", "crg_core")]
                        ),
                    }
                ],
            }
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {"schema_version": 3, "positions": positions, "warnings": []},
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
                f"{position}.u_crg",
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
                    "RS_CRG_EN": "0",
                    "WIDTH": "8",
                    "ignored_mode": "0",
                },
                "ports": {
                    "clk": {
                        "connection": f"{position}.clk_default",
                        "type": "npiNet",
                    },
                    "rst_n": {
                        "connection": f"{position}.rst_n",
                        "type": "npiNet",
                    },
                },
                **_clock_trace_fields(
                    "clk", [(f"{position}.u_crg", "crg_default")]
                ),
            }
        )
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 3,
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


def _write_rs_cfg_dontcare_inputs(output: Path) -> tuple[Path, Path]:
    specs_path = output / "rs_cfg_dontcare_specs.csv"
    inventory_path = output / "rs_cfg_dontcare_inventory.json"
    position = "top.u_dontcare"
    instance_name = "DONTCARE_RS"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "DONTCARE_IF",
                "rs_pipe",
                instance_name,
                position,
                1,
                "clk_dontcare",
                "rst_n",
                f"{position}.u_crg",
                _RS_CFG_DONTCARE_TEXT,
            )
        )
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 3,
                "positions": {
                    position: {
                        "found": True,
                        "instances": [
                            {
                                "name": instance_name,
                                "full_name": f"{position}.{instance_name}",
                                "module": "rs_pipe",
                                "file": "rs_cfg_dontcare_top.sv",
                                "line": 12,
                                "parameters": {
                                    "WIDTH": "8",
                                    "rs_mode": "1",
                                },
                                "ports": {
                                    "clk": {
                                        "connection": f"{position}.clk_dontcare",
                                        "type": "npiNet",
                                    },
                                    "rst": {
                                        "connection": f"{position}.rst_n",
                                        "type": "npiNet",
                                    },
                                },
                                **_clock_trace_fields(
                                    "clk",
                                    [(f"{position}.u_crg", "crg_dontcare")],
                                ),
                            }
                        ],
                    }
                },
                "warnings": [],
            },
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


def _write_rs_cfg_na_inputs(output: Path) -> tuple[Path, Path]:
    specs_path = output / "rs_cfg_na_specs.csv"
    inventory_path = output / "rs_cfg_na_inventory.json"
    position = "top.u_rs_cfg_na"
    instance_name = "NA_RS"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "NA_IF",
                "rs_pipe",
                instance_name,
                position,
                1,
                "clk_na",
                "rst_n",
                f"{position}.u_crg",
                _RS_CFG_NA_TEXT,
            )
        )
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 3,
                "positions": {
                    position: {
                        "found": True,
                        "instances": [
                            {
                                "name": instance_name,
                                "full_name": f"{position}.{instance_name}",
                                "module": "rs_pipe",
                                "file": "rs_cfg_na_top.sv",
                                "line": 18,
                                "parameters": {
                                    "RS_CRG_EN": "1",
                                    "WIDTH": "8",
                                    "rs_mode": "1",
                                },
                                "ports": {
                                    "clk": {
                                        "connection": f"{position}.clk_na",
                                        "type": "npiNet",
                                    },
                                    "rst": {
                                        "connection": f"{position}.rst_n",
                                        "type": "npiNet",
                                    },
                                },
                                **_clock_trace_fields(
                                    "clk", [(f"{position}.u_crg", "crg_na")]
                                ),
                            }
                        ],
                    }
                },
                "warnings": [],
            },
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


def _write_crg_source_mapping_inputs(output: Path) -> tuple[Path, Path]:
    specs_path = output / "crg_source_mapping_specs.csv"
    inventory_path = output / "crg_source_mapping_inventory.json"
    position = "top.u_crg_mapping"
    instance_name = "CRG_MAP_RS"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "CRG_MAP_IF",
                "rs_pipe",
                instance_name,
                position,
                1,
                "clk_crg_map",
                "rst_n",
                _CRG_SOURCE_ALIAS,
                "假门控",
            )
        )
    with inventory_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "schema_version": 3,
                "positions": {
                    position: {
                        "found": True,
                        "instances": [
                            {
                                "name": instance_name,
                                "full_name": f"{position}.{instance_name}",
                                "module": "rs_pipe",
                                "file": "crg_source_mapping_top.sv",
                                "line": 21,
                                "parameters": {
                                    "RS_CRG_EN": "0",
                                    "WIDTH": "8",
                                    "rs_mode": "1",
                                },
                                "ports": {
                                    "clk": {
                                        "connection": f"{position}.clk_crg_map",
                                        "type": "npiNet",
                                    },
                                    "rst": {
                                        "connection": f"{position}.rst_n",
                                        "type": "npiNet",
                                    },
                                },
                                **_clock_trace_fields(
                                    "clk",
                                    [
                                        ("top.u_crg_mapping.u_occ", "clk_occ"),
                                        (
                                            "top.u_crg_mapping.u_clk_mux",
                                            "clk_mux",
                                        ),
                                        (_CRG_SOURCE_FULL_PATH, "crg_core"),
                                    ],
                                ),
                            }
                        ],
                    }
                },
                "warnings": [],
            },
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


def _write_custom_port_spec(output: Path) -> Path:
    specs_path = output / "custom_port_specs.csv"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "CUSTOM_IF",
                "rs_custom",
                "CUSTOM_RS",
                "tile_core",
                1,
                "clk_rs",
                "rst_n",
                "intentionally_wrong_source",
                "假门控",
            )
        )
    return specs_path


def _write_clk_without_rst_spec(output: Path) -> Path:
    specs_path = output / "clk_without_rst_specs.csv"
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(_BUSINESS_HEADERS)
        writer.writerow(
            (
                "CLK_ONLY_IF",
                "rs_clk_only",
                "CLK_ONLY_RS",
                "tile_core",
                1,
                "clk_rs",
                "rst_n",
                "crg_core",
                "假门控",
            )
        )
    return specs_path


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
    if args.crg_depth_limit and (
        not online
        or args.validate_only
        or args.negative
        or args.expect_partial_load
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_dontcare
        or args.rs_cfg_na
        or args.crg_source_mapping
        or args.generated_rows
    ):
        raise SystemExit(
            "--crg-depth-limit requires a positive online full check and is "
            "mutually exclusive with all other special modes"
        )
    if args.rs_cfg_dontcare and (
        args.negative
        or args.expect_partial_load
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_na
        or args.crg_source_mapping
        or args.generated_rows
    ):
        raise SystemExit(
            "--rs-cfg-dontcare is mutually exclusive with all other special modes"
        )
    if args.rs_cfg_dontcare and online:
        raise SystemExit(
            "--rs-cfg-dontcare is available only in offline inventory mode"
        )
    if args.rs_cfg_dontcare and args.validate_only:
        raise SystemExit(
            "--rs-cfg-dontcare requires a full check, not --validate-only"
        )
    if args.rs_cfg_na and (
        args.negative
        or args.expect_partial_load
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_dontcare
        or args.crg_source_mapping
        or args.generated_rows
    ):
        raise SystemExit(
            "--rs-cfg-na is mutually exclusive with all other special modes"
        )
    if args.rs_cfg_na and online:
        raise SystemExit("--rs-cfg-na is available only in offline inventory mode")
    if args.rs_cfg_na and args.validate_only:
        raise SystemExit("--rs-cfg-na requires a full check, not --validate-only")
    if args.crg_source_mapping and (
        args.negative
        or args.expect_partial_load
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_dontcare
        or args.rs_cfg_na
        or args.generated_rows
    ):
        raise SystemExit(
            "--crg-source-mapping is mutually exclusive with all other special modes"
        )
    if args.crg_source_mapping and online:
        raise SystemExit(
            "--crg-source-mapping is available only in offline inventory mode"
        )
    if args.crg_source_mapping and args.validate_only:
        raise SystemExit(
            "--crg-source-mapping requires a full check, not --validate-only"
        )
    if online and args.generated_rows:
        raise SystemExit("--generated-rows is available only in offline inventory mode")
    if args.expect_partial_load and not online:
        raise SystemExit("--expect-partial-load requires online NPI mode")
    if args.expect_partial_load and (
        args.negative
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_na
        or args.crg_source_mapping
        or args.generated_rows
        or args.validate_only
    ):
        raise SystemExit(
            "--expect-partial-load requires a positive online full check"
        )
    if args.negative and (
        args.generated_rows
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_na
        or args.crg_source_mapping
    ):
        raise SystemExit(
            "--negative is mutually exclusive with --generated-rows, --custom-port, "
            "and --clk-without-rst"
        )
    if args.default_rule and online:
        raise SystemExit("--default-rule is available only in offline inventory mode")
    if args.default_rule and (
        args.negative
        or args.generated_rows
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_na
        or args.crg_source_mapping
    ):
        raise SystemExit(
            "--default-rule is mutually exclusive with --negative, --generated-rows, "
            "--custom-port, and --clk-without-rst"
        )
    if args.default_rule and args.validate_only:
        raise SystemExit("--default-rule requires a full check, not --validate-only")
    if args.custom_port and not online:
        raise SystemExit("--custom-port requires online NPI mode")
    if args.custom_port and args.validate_only:
        raise SystemExit("--custom-port requires a full check, not --validate-only")
    if args.custom_port and args.clk_without_rst:
        raise SystemExit("--custom-port and --clk-without-rst are mutually exclusive")
    if args.clk_without_rst and not online:
        raise SystemExit("--clk-without-rst requires online NPI mode")
    if args.clk_without_rst and args.validate_only:
        raise SystemExit("--clk-without-rst requires a full check, not --validate-only")

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
    if app.notebook.tab(app.crg_sources_tab, "text") != "CRG Source映射库":
        raise SystemExit("GUI CRG Source mapping database tab label is incorrect")
    if (
        app.crg_source_tree.heading("alias", "text") != "CRG Source简称"
        or app.crg_source_tree.heading("rtl_path", "text") != "RTL完整路径"
    ):
        raise SystemExit("GUI CRG Source mapping column labels are incorrect")
    app.notebook.select(app.crg_sources_tab)
    root.update()
    if not app.crg_source_tree.winfo_ismapped():
        raise SystemExit("GUI CRG Source mapping database is not visible")
    app.notebook.select(app.rules_tab)
    root.update()
    rule_columns = (
        "module",
        "rs_cfg_en",
        "clk_port",
        "rst_port",
        "step_parameters",
    )
    rule_width = sum(int(app.rule_tree.column(name, "width")) for name in rule_columns)
    if app.rule_tree.winfo_width() <= 1 or rule_width > app.rule_tree.winfo_width() + 2:
        raise SystemExit(
            "GUI module rule table overflows at the 980x680 minimum window size"
        )
    app.notebook.select(app.setup_tab)
    root.update()
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
    if args.custom_port:
        source_config.setdefault("module_rules", {})["rs_custom"] = {
            "has_rs_cfg_en": True,
            "step_parameters": [],
            "clk_port": "clock_i",
            "rst_port": "reset_ni",
        }
        source_config_text = (
            json.dumps(source_config, ensure_ascii=False, indent=2) + "\n"
        )
    if args.clk_without_rst:
        source_config.setdefault("module_rules", {})["rs_clk_only"] = {
            "has_rs_cfg_en": True,
            "step_parameters": [],
            "clk_port": "clk",
            "rst_port": "rst_n",
        }
        source_config_text = (
            json.dumps(source_config, ensure_ascii=False, indent=2) + "\n"
        )
    if args.rs_cfg_dontcare:
        source_config.setdefault("module_rules", {})["rs_pipe"] = {
            "has_rs_cfg_en": False,
            "step_parameters": ["rs_mode"],
            "clk_port": "clk",
            "rst_port": "rst",
        }
    crg_source_mappings = source_config.setdefault("crg_source_mappings", {})
    crg_source_mappings.setdefault("gui_crg_alias", "top.u_crg_source")
    if args.crg_source_mapping:
        crg_source_mappings[_CRG_SOURCE_ALIAS] = _CRG_SOURCE_FULL_PATH
    source_config_text = json.dumps(source_config, ensure_ascii=False, indent=2) + "\n"
    original_position_mappings = source_config.get("position_mappings", {})
    if original_position_mappings.get("tile_core") != "top.u_tile":
        raise SystemExit(
            "sample config must map position alias 'tile_core' to 'top.u_tile'"
        )
    original_module_rules = source_config.get("module_rules", {})
    original_crg_source_mappings = source_config.get("crg_source_mappings", {})
    if original_crg_source_mappings.get("gui_crg_alias") != "top.u_crg_source":
        raise SystemExit(
            "temporary config must map CRG_source alias 'gui_crg_alias' "
            "to 'top.u_crg_source'"
        )
    if args.default_rule and "rs_default_pipe" in original_module_rules:
        raise SystemExit("default-rule smoke module must not be explicitly registered")
    config_path.write_text(source_config_text, encoding="utf-8")
    app.config_var.set(str(config_path))
    app._load_config_from_form()
    if source_config.get("excel", {}).get("validate_headers") is not False:
        raise SystemExit("sample config must disable strict header validation by default")
    if app.header_check_var.get():
        raise SystemExit("GUI loaded strict header validation as enabled by default")
    expected_trace_depth = source_config.get("rtl", {}).get(
        "crg_trace_max_depth"
    )
    if expected_trace_depth != 16 or app.crg_trace_max_depth_var.get() != "16":
        raise SystemExit("GUI did not load CRG trace maximum depth 16")

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

    app.crg_source_alias_var.set("gui_smoke_crg_source")
    app.crg_source_path_var.set("top.u_gui_smoke_crg")
    app._apply_crg_source_mapping()
    if not app._crg_source_mappings_dirty:
        raise SystemExit("GUI CRG_source mapping edit did not set dirty state")
    app._save_crg_source_mappings()
    if app._crg_source_mappings_dirty:
        raise SystemExit("GUI CRG_source mapping save did not clear dirty state")
    app._load_config_from_form()
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    if saved_config.get("crg_source_mappings", {}).get(
        "gui_smoke_crg_source"
    ) != "top.u_gui_smoke_crg":
        raise SystemExit("GUI CRG_source mapping save/reload failed")
    app.crg_source_search_var.set("gui_smoke")
    filtered_crg_sources = app.crg_source_tree.get_children()
    if (
        len(filtered_crg_sources) != 1
        or app._crg_source_tree_aliases.get(filtered_crg_sources[0])
        != "gui_smoke_crg_source"
    ):
        raise SystemExit(
            "GUI CRG_source mapping search did not isolate the saved entry"
        )
    app.crg_source_tree.selection_set(filtered_crg_sources[0])
    app._on_crg_source_selected()
    app.crg_source_path_var.set("top.u_gui_smoke_crg_updated")
    app._apply_crg_source_mapping()
    if not app._crg_source_mappings_dirty:
        raise SystemExit("GUI CRG_source mapping update did not set dirty state")
    app._save_crg_source_mappings()
    if app._crg_source_mappings_dirty:
        raise SystemExit(
            "GUI CRG_source mapping update save did not clear dirty state"
        )
    app._load_config_from_form()
    updated_config = json.loads(config_path.read_text(encoding="utf-8"))
    if updated_config.get("crg_source_mappings", {}).get(
        "gui_smoke_crg_source"
    ) != "top.u_gui_smoke_crg_updated":
        raise SystemExit("GUI CRG_source mapping update/reload failed")
    app.crg_source_search_var.set("gui_smoke")
    filtered_crg_sources = app.crg_source_tree.get_children()
    if len(filtered_crg_sources) != 1:
        raise SystemExit("GUI CRG_source mapping disappeared before delete")
    app.crg_source_tree.selection_set(filtered_crg_sources[0])
    app._on_crg_source_selected()
    app._delete_crg_source_mapping()
    if not app._crg_source_mappings_dirty:
        raise SystemExit("GUI CRG_source mapping delete did not set dirty state")
    app._save_crg_source_mappings()
    if app._crg_source_mappings_dirty:
        raise SystemExit(
            "GUI CRG_source mapping delete save did not clear dirty state"
        )
    app._load_config_from_form()
    restored_config = json.loads(config_path.read_text(encoding="utf-8"))
    if restored_config.get("crg_source_mappings", {}) != (
        original_crg_source_mappings
    ):
        raise SystemExit(
            "GUI CRG_source mapping delete did not restore the original mapping set"
        )
    app.crg_source_search_var.set("gui_crg_alias")
    restored_crg_sources = app.crg_source_tree.get_children()
    if (
        len(restored_crg_sources) != 1
        or app._crg_source_tree_aliases.get(restored_crg_sources[0])
        != "gui_crg_alias"
    ):
        raise SystemExit(
            "GUI CRG_source mapping search did not find gui_crg_alias after restore"
        )

    app.module_name_var.set("gui_smoke_rule")
    app.module_has_rs_cfg_en_var.set(False)
    app.module_step_parameters_var.set("smoke_mode")
    app.module_clk_port_var.set("")
    app.module_rst_port_var.set("  ")
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
        "clk_port": "clk",
        "rst_port": "rst_n",
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
    if (
        app.module_clk_port_var.get() != "clk"
        or app.module_rst_port_var.get() != "rst_n"
    ):
        raise SystemExit("GUI module rule selection lost default port names")
    app.module_step_parameters_var.set("smoke_mode, extra_mode")
    app.module_clk_port_var.set("clock_i")
    app.module_rst_port_var.set("reset_ni")
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
        "clk_port": "clock_i",
        "rst_port": "reset_ni",
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
    app.crg_trace_max_depth_var.set("32")
    for index, name in enumerate(FIELD_NAMES, start=20):
        app.column_vars[name].set(str(index))
    app._module_rules["gui_unsaved_rule"] = ModuleRule(
        "gui_unsaved_rule",
        False,
        ("smoke_enable",),
        "gui_clk",
        "gui_reset_n",
    )
    app._position_mappings["gui_unsaved_position"] = "top.u_gui_unsaved"
    app._crg_source_mappings["gui_unsaved_crg_source"] = (
        "top.u_gui_unsaved_crg_source"
    )
    app._module_rules_dirty = True
    app._position_mappings_dirty = True
    app._crg_source_mappings_dirty = True
    gui_module.filedialog.asksaveasfilename = (
        lambda **_kwargs: str(export_path)
    )
    app._export_config()
    if app.config_var.get() != active_path_before_export:
        raise SystemExit("GUI config export unexpectedly changed the active config path")
    if (
        not app._module_rules_dirty
        or not app._position_mappings_dirty
        or not app._crg_source_mappings_dirty
    ):
        raise SystemExit("GUI config export unexpectedly cleared database dirty state")
    exported_config = _read_json_object(export_path, "exported config")
    expected_roots = {
        "excel",
        "columns",
        "rtl",
        "position_mappings",
        "crg_source_mappings",
        "module_rules",
    }
    if set(exported_config) != expected_roots:
        raise SystemExit(
            "GUI config export root sections mismatch: "
            f"{sorted(exported_config)!r}"
        )
    expected_exported_rtl = dict(source_config.get("rtl", {}))
    expected_exported_rtl["crg_trace_max_depth"] = 32
    if exported_config.get("rtl") != expected_exported_rtl:
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
        "clk_port": "gui_clk",
        "rst_port": "gui_reset_n",
    }:
        raise SystemExit("GUI config export omitted an unsaved module rule")
    if exported_config.get("position_mappings", {}).get(
        "gui_unsaved_position"
    ) != "top.u_gui_unsaved":
        raise SystemExit("GUI config export omitted an unsaved position mapping")
    expected_exported_crg_source_mappings = {
        **original_crg_source_mappings,
        "gui_unsaved_crg_source": "top.u_gui_unsaved_crg_source",
    }
    if exported_config.get("crg_source_mappings", {}) != (
        expected_exported_crg_source_mappings
    ):
        raise SystemExit(
            "GUI config export did not preserve the complete CRG_source database"
        )
    active_config = _read_json_object(config_path, "active config")
    if "gui_unsaved_rule" in active_config.get("module_rules", {}) or (
        "gui_unsaved_position" in active_config.get("position_mappings", {})
    ) or (
        "gui_unsaved_crg_source"
        in active_config.get("crg_source_mappings", {})
    ):
        raise SystemExit("GUI config export modified the active config file")

    app.sheet_var.set("not imported")
    app._module_rules.clear()
    app._position_mappings.clear()
    app._crg_source_mappings.clear()
    gui_module.filedialog.askopenfilename = lambda **_kwargs: str(export_path)
    app._import_config()
    if app.config_var.get() != str(export_path):
        raise SystemExit("GUI config import did not switch the active config path")
    if (
        app._module_rules_dirty
        or app._position_mappings_dirty
        or app._crg_source_mappings_dirty
    ):
        raise SystemExit("GUI config import did not clear database dirty state")
    if app.sheet_var.get() != "PortableSmoke":
        raise SystemExit("GUI config import did not restore Excel settings")
    if app.crg_trace_max_depth_var.get() != "32":
        raise SystemExit("GUI config import did not restore CRG trace maximum depth")
    if "gui_unsaved_rule" not in app._module_rules:
        raise SystemExit("GUI config import did not restore the module rule database")
    imported_rule = app._module_rules["gui_unsaved_rule"]
    if (
        imported_rule.clk_port != "gui_clk"
        or imported_rule.rst_port != "gui_reset_n"
    ):
        raise SystemExit("GUI config import lost module rule port names")
    if "gui_unsaved_position" not in app._position_mappings:
        raise SystemExit("GUI config import did not restore the position database")
    if app._crg_source_mappings != expected_exported_crg_source_mappings:
        raise SystemExit(
            "GUI config import did not restore the complete CRG_source database"
        )
    if (
        app._loaded_config is None
        or app._loaded_config.rtl
        != gui_module.replace(rtl_before_export, crg_trace_max_depth=32)
    ):
        raise SystemExit("GUI config import did not restore the RTL configuration")

    app.config_var.set(str(config_path))
    app._load_config_from_form()
    if app.config_var.get() != str(config_path):
        raise SystemExit("GUI smoke failed to restore its active config after I/O test")
    if (
        app._crg_source_mappings != original_crg_source_mappings
        or app._crg_source_mappings_dirty
    ):
        raise SystemExit(
            "GUI smoke failed to restore the clean original CRG_source database"
        )
    config_io_verified = True
    if args.crg_depth_limit:
        app.crg_trace_max_depth_var.set("2")
    expected_rows = (
        1
        if args.negative
        or args.default_rule
        or args.custom_port
        or args.clk_without_rst
        or args.rs_cfg_dontcare
        or args.rs_cfg_na
        or args.crg_source_mapping
        else args.generated_rows or 2
    )
    expected_tree_rows = expected_rows + (1 if args.expect_partial_load else 0)
    sample_position_mapping = not (
        args.negative
        or args.default_rule
        or args.rs_cfg_dontcare
        or args.rs_cfg_na
        or args.crg_source_mapping
        or args.generated_rows
    )
    if args.default_rule:
        excel_path, inventory_path = _write_default_rule_inputs(output)
    elif args.rs_cfg_dontcare:
        excel_path, inventory_path = _write_rs_cfg_dontcare_inputs(output)
    elif args.rs_cfg_na:
        excel_path, inventory_path = _write_rs_cfg_na_inputs(output)
    elif args.crg_source_mapping:
        excel_path, inventory_path = _write_crg_source_mapping_inputs(output)
    elif args.custom_port:
        excel_path = _write_custom_port_spec(output)
        inventory_path = project_root / "tests" / "fixtures" / "inventory.json"
    elif args.clk_without_rst:
        excel_path = _write_clk_without_rst_spec(output)
        inventory_path = project_root / "tests" / "fixtures" / "inventory.json"
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

    def mark_failed() -> None:
        nonlocal failed
        failed = True

    _install_callback_fail_fast(root, mark_failed)

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
            "VALID"
            if args.validate_only
            else "FAIL"
            if args.negative or args.clk_without_rst
            else "PASS"
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
            expected_group = (
                "DEFAULT_RS"
                if args.default_rule
                else "CUSTOM_RS"
                if args.custom_port
                else "CLK_ONLY_RS"
                if args.clk_without_rst
                else "DONTCARE_RS"
                if args.rs_cfg_dontcare
                else "NA_RS"
                if args.rs_cfg_na
                else "CRG_MAP_RS"
                if args.crg_source_mapping
                else "AAAA_BBB"
            )
            expected_instances = (
                "2"
                if args.default_rule
                else "1"
                if args.custom_port
                or args.clk_without_rst
                or args.rs_cfg_dontcare
                or args.rs_cfg_na
                or args.crg_source_mapping
                else "6"
            )
            expected_step_cell = (
                "2/2"
                if args.default_rule
                else "1/1"
                if args.custom_port
                or args.clk_without_rst
                or args.rs_cfg_dontcare
                or args.rs_cfg_na
                or args.crg_source_mapping
                else "5/6"
                if args.negative
                else "5/5"
            )
            if (
                len(first_values) < 11
                or str(first_values[6]) != expected_group
                or str(first_values[8]) != expected_instances
                or str(first_values[9]) != expected_step_cell
                or (
                    not args.validate_only
                    and str(first_values[5])
                    != (
                        "WARNING"
                        if args.custom_port or args.crg_depth_limit
                        else "PASS"
                    )
                )
                or (
                    args.crg_depth_limit
                    and (
                        str(first_values[0]) != "WARNING"
                        or str(first_values[10]) != "0E/6W"
                    )
                )
                or (
                    args.crg_source_mapping
                    and str(first_values[4])
                    != f"{_CRG_SOURCE_ALIAS} -> {_CRG_SOURCE_FULL_PATH}"
                )
                or (
                    (
                        args.rs_cfg_dontcare
                        or args.rs_cfg_na
                        or args.crg_source_mapping
                    )
                    and (
                        len(first_values) < 11
                        or str(first_values[0]) != "PASS"
                        or str(first_values[7])
                        != (
                            _RS_CFG_DONTCARE_TEXT
                            if args.rs_cfg_dontcare
                            else _RS_CFG_NA_TEXT
                            if args.rs_cfg_na
                            else "假门控"
                        )
                        or str(first_values[10]) != "0E/0W"
                    )
                )
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
            if raw_report.get("schema_version") != 4:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: report schema is not v4: "
                    f"{raw_report.get('schema_version')!r}",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if raw_inventory.get("schema_version") != 3:
                failed = True
                print(
                    "GUI_SMOKE_FAIL: inventory schema is not v3: "
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
                    len(global_values) < 11
                    or str(global_values[0]) != "WARNING"
                    or str(global_values[2]) != "GLOBAL"
                    or str(global_values[10]) != "0E/1W"
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: GLOBAL warning row is not displayed as WARNING",
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
            expected_label = (
                _RS_CFG_DONTCARE_TEXT
                if args.rs_cfg_dontcare
                else _RS_CFG_NA_TEXT
                if args.rs_cfg_na
                else "真门控"
                if args.negative
                else "假门控"
            )
            if args.rs_cfg_dontcare:
                report_rows = raw_report.get("rows")
                report_row = (
                    report_rows[0]
                    if isinstance(report_rows, list)
                    and len(report_rows) == 1
                    and isinstance(report_rows[0], dict)
                    else {}
                )
                report_spec = report_row.get("spec", {})
                report_rule = report_row.get("module_rule", {})
                report_instances = report_row.get("matched_instances", [])
                report_parameters = (
                    report_instances[0].get("parameters", {})
                    if isinstance(report_instances, list)
                    and len(report_instances) == 1
                    and isinstance(report_instances[0], dict)
                    else {}
                )
                if (
                    not isinstance(report_rows, list)
                    or len(report_rows) != 1
                    or not isinstance(report_spec, dict)
                    or report_spec.get("RS_CFG_EN") != _RS_CFG_DONTCARE_TEXT
                    or not isinstance(report_rule, dict)
                    or report_rule.get("has_rs_cfg_en") is not False
                    or not isinstance(report_parameters, dict)
                    or "RS_CRG_EN" in report_parameters
                    or report_row.get("findings") != []
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: serialized RS_CFG_EN don't-care evidence "
                        "is incomplete",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            if args.rs_cfg_na:
                report_rows = raw_report.get("rows")
                report_row = (
                    report_rows[0]
                    if isinstance(report_rows, list)
                    and len(report_rows) == 1
                    and isinstance(report_rows[0], dict)
                    else {}
                )
                report_spec = report_row.get("spec", {})
                report_rule = report_row.get("module_rule", {})
                report_instances = report_row.get("matched_instances", [])
                report_parameters = (
                    report_instances[0].get("parameters", {})
                    if isinstance(report_instances, list)
                    and len(report_instances) == 1
                    and isinstance(report_instances[0], dict)
                    else {}
                )
                try:
                    with csv_report_path.open(
                        encoding="utf-8-sig", newline=""
                    ) as stream:
                        csv_rows = list(csv.DictReader(stream))
                except OSError as exc:
                    failed = True
                    print(
                        f"GUI_SMOKE_FAIL: cannot read CSV report: {exc}",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if (
                    not isinstance(report_rows, list)
                    or len(report_rows) != 1
                    or not isinstance(report_spec, dict)
                    or report_spec.get("RS_CFG_EN") != _RS_CFG_NA_TEXT
                    or not isinstance(report_rule, dict)
                    or report_rule.get("has_rs_cfg_en") is not True
                    or not isinstance(report_parameters, dict)
                    or report_parameters.get("RS_CRG_EN") != "1"
                    or report_row.get("findings") != []
                    or len(csv_rows) != 1
                    or csv_rows[0].get("RS_CFG_EN") != _RS_CFG_NA_TEXT
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: serialized RS_CFG_EN=NA skip evidence "
                        "is incomplete",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            if args.crg_source_mapping:
                report_rows = raw_report.get("rows")
                report_row = (
                    report_rows[0]
                    if isinstance(report_rows, list)
                    and len(report_rows) == 1
                    and isinstance(report_rows[0], dict)
                    else {}
                )
                report_spec = report_row.get("spec", {})
                crg_check = report_row.get("crg_source_check", {})
                crg_instances = (
                    crg_check.get("instances", [])
                    if isinstance(crg_check, dict)
                    else []
                )
                crg_evaluation = (
                    crg_instances[0]
                    if isinstance(crg_instances, list)
                    and len(crg_instances) == 1
                    and isinstance(crg_instances[0], dict)
                    else {}
                )
                matched_crg = crg_evaluation.get("matched")
                try:
                    with csv_report_path.open(
                        encoding="utf-8-sig", newline=""
                    ) as stream:
                        csv_rows = list(csv.DictReader(stream))
                except OSError as exc:
                    failed = True
                    print(
                        f"GUI_SMOKE_FAIL: cannot read CSV report: {exc}",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                csv_crg_evaluation = _single_csv_crg_trace_evaluation(csv_rows)
                csv_matched_crg = csv_crg_evaluation.get("matched")
                if (
                    not isinstance(report_rows, list)
                    or len(report_rows) != 1
                    or not isinstance(report_spec, dict)
                    or report_spec.get("CRG_source") != _CRG_SOURCE_FULL_PATH
                    or report_spec.get("crg_source_alias") != _CRG_SOURCE_ALIAS
                    or not isinstance(crg_check, dict)
                    or crg_check.get("status") != "pass"
                    or crg_check.get("expected") != _CRG_SOURCE_FULL_PATH
                    or crg_evaluation.get("status") != "matched"
                    or not isinstance(matched_crg, dict)
                    or matched_crg.get("instance") != _CRG_SOURCE_FULL_PATH
                    or matched_crg.get("depth") != 3
                    or matched_crg.get("path")
                    != [
                        "top.u_crg_mapping.u_occ",
                        "top.u_crg_mapping.u_clk_mux",
                        _CRG_SOURCE_FULL_PATH,
                    ]
                    or report_row.get("findings") != []
                    or len(csv_rows) != 1
                    or csv_rows[0].get("CRG_source") != _CRG_SOURCE_FULL_PATH
                    or csv_rows[0].get("crg_source_alias") != _CRG_SOURCE_ALIAS
                    or csv_rows[0].get("crg_trace_status") != "pass"
                    or csv_rows[0].get("crg_trace_max_depth") != "16"
                    or csv_crg_evaluation.get("status") != "matched"
                    or not isinstance(csv_matched_crg, dict)
                    or csv_matched_crg.get("path")
                    != [
                        "top.u_crg_mapping.u_occ",
                        "top.u_crg_mapping.u_clk_mux",
                        _CRG_SOURCE_FULL_PATH,
                    ]
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: serialized CRG_source mapping evidence "
                        "is incomplete",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            if args.crg_depth_limit:
                report_rows = raw_report.get("rows")
                first_row = (
                    report_rows[0]
                    if isinstance(report_rows, list)
                    and len(report_rows) == 2
                    and isinstance(report_rows[0], dict)
                    else {}
                )
                second_row = (
                    report_rows[1]
                    if isinstance(report_rows, list)
                    and len(report_rows) == 2
                    and isinstance(report_rows[1], dict)
                    else {}
                )
                crg_check = first_row.get("crg_source_check", {})
                evaluations = (
                    crg_check.get("instances", [])
                    if isinstance(crg_check, dict)
                    else []
                )
                depth_findings = [
                    finding
                    for finding in first_row.get("findings", [])
                    if isinstance(finding, dict)
                    and finding.get("code") == "CRG_TRACE_DEPTH_LIMIT"
                ]
                try:
                    with csv_report_path.open(
                        encoding="utf-8-sig", newline=""
                    ) as stream:
                        depth_csv_rows = list(csv.DictReader(stream))
                    depth_csv_evidence = [
                        json.loads(row.get("crg_trace_evidence", ""))
                        for row in depth_csv_rows
                    ]
                except (OSError, json.JSONDecodeError) as exc:
                    failed = True
                    print(
                        f"GUI_SMOKE_FAIL: cannot read CRG depth CSV evidence: {exc}",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                warning_csv_rows = [
                    (row, evidence)
                    for row, evidence in zip(depth_csv_rows, depth_csv_evidence)
                    if row.get("crg_trace_status") == "warning"
                ]
                pass_csv_rows = [
                    (row, evidence)
                    for row, evidence in zip(depth_csv_rows, depth_csv_evidence)
                    if row.get("crg_trace_status") == "pass"
                ]
                if (
                    crg_check.get("status") != "warning"
                    or len(evaluations) != 6
                    or any(
                        not isinstance(evaluation, dict)
                        or evaluation.get("status") != "depth_limited"
                        or evaluation.get("max_depth") != 2
                        for evaluation in evaluations
                    )
                    or len(depth_findings) != 6
                    or any(
                        finding.get("severity") != "warning"
                        for finding in depth_findings
                    )
                    or second_row.get("crg_source_check", {}).get("status")
                    != "pass"
                    or len(depth_csv_rows) != 7
                    or len(warning_csv_rows) != 6
                    or len(pass_csv_rows) != 1
                    or any(
                        row.get("crg_trace_max_depth") != "2"
                        or not isinstance(evidence, list)
                        or len(evidence) != 6
                        or any(
                            not isinstance(item, dict)
                            or item.get("status") != "depth_limited"
                            or item.get("max_depth") != 2
                            for item in evidence
                        )
                        for row, evidence in warning_csv_rows
                    )
                    or pass_csv_rows[0][0].get("crg_trace_max_depth") != "2"
                    or not isinstance(pass_csv_rows[0][1], list)
                    or len(pass_csv_rows[0][1]) != 1
                    or not isinstance(pass_csv_rows[0][1][0], dict)
                    or pass_csv_rows[0][1][0].get("status") != "matched"
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: CRG depth-limit evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
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
                    parameter_evidence_valid = isinstance(parameters, dict) and (
                        "RS_CRG_EN" not in parameters
                        if args.rs_cfg_dontcare
                        else parameters.get("RS_CRG_EN")
                        == ("1" if args.rs_cfg_na else "0")
                    )
                    if not parameter_evidence_valid:
                        failed = True
                        print(
                            "GUI_SMOKE_FAIL: RS_CRG_EN parameter evidence mismatch",
                            file=sys.stderr,
                        )
                        root.destroy()
                        return
                module_rule = record.get("module_rule")
                step_check = record.get("step_check")
                configured_rule = original_module_rules.get(
                    spec.get("RS_module"), {}
                )
                if not isinstance(configured_rule, dict):
                    configured_rule = {}
                expected_step_parameters = (
                    []
                    if args.default_rule
                    else configured_rule.get("step_parameters", [])
                )
                expected_clk_port = configured_rule.get("clk_port", "clk")
                expected_rst_port = configured_rule.get("rst_port", "rst_n")
                expected_has_rs_cfg_en = not args.rs_cfg_dontcare
                if (
                    not isinstance(module_rule, dict)
                    or module_rule.get("name") != spec.get("RS_module")
                    or module_rule.get("has_rs_cfg_en") is not expected_has_rs_cfg_en
                    or module_rule.get("step_parameters") != expected_step_parameters
                    or module_rule.get("clk_port") != expected_clk_port
                    or module_rule.get("rst_port") != expected_rst_port
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
                    if not expected_step_parameters:
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
                if args.rs_cfg_dontcare and (
                    spec.get("RS_module") != "rs_pipe"
                    or spec.get("RS_inst") != "DONTCARE_RS"
                    or step_check.get("expected") != 1
                    or step_check.get("physical_instances") != 1
                    or step_check.get("effective_step") != 1
                    or contribution_values != [1]
                    or len(instances) != 1
                    or not isinstance(instances[0].get("parameters"), dict)
                    or "RS_CRG_EN" in instances[0]["parameters"]
                    or record.get("findings") != []
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: RS_CFG_EN don't-care result evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.rs_cfg_na and (
                    spec.get("RS_module") != "rs_pipe"
                    or spec.get("RS_inst") != "NA_RS"
                    or spec.get("RS_CFG_EN") != _RS_CFG_NA_TEXT
                    or step_check.get("expected") != 1
                    or step_check.get("physical_instances") != 1
                    or step_check.get("effective_step") != 1
                    or contribution_values != [1]
                    or len(instances) != 1
                    or not isinstance(instances[0].get("parameters"), dict)
                    or instances[0]["parameters"].get("RS_CRG_EN") != "1"
                    or record.get("findings") != []
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: RS_CFG_EN=NA result evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.crg_source_mapping and (
                    spec.get("RS_module") != "rs_pipe"
                    or spec.get("RS_inst") != "CRG_MAP_RS"
                    or spec.get("CRG_source") != _CRG_SOURCE_FULL_PATH
                    or spec.get("crg_source_alias") != _CRG_SOURCE_ALIAS
                    or step_check.get("expected") != 1
                    or step_check.get("physical_instances") != 1
                    or step_check.get("effective_step") != 1
                    or contribution_values != [1]
                    or len(instances) != 1
                    or instances[0].get("full_name")
                    != "top.u_crg_mapping.CRG_MAP_RS"
                    or not isinstance(record.get("crg_source_check"), dict)
                    or record["crg_source_check"].get("status") != "pass"
                    or record.get("findings") != []
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: CRG_source mapping result evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.crg_depth_limit:
                    trace_findings = [
                        finding
                        for finding in record.get("findings", [])
                        if isinstance(finding, dict)
                        and finding.get("code") == "CRG_TRACE_DEPTH_LIMIT"
                    ]
                    expected_trace_findings = (
                        6 if spec.get("RS_inst") == "AAAA_BBB" else 0
                    )
                    expected_trace_status = (
                        "warning"
                        if spec.get("RS_inst") == "AAAA_BBB"
                        else "pass"
                    )
                    if (
                        len(trace_findings) != expected_trace_findings
                        or not isinstance(record.get("crg_source_check"), dict)
                        or record["crg_source_check"].get("status")
                        != expected_trace_status
                    ):
                        failed = True
                        print(
                            "GUI_SMOKE_FAIL: rendered CRG depth-limit evidence mismatch",
                            file=sys.stderr,
                        )
                        root.destroy()
                        return
                custom_findings = [
                    finding
                    for finding in record.get("findings", [])
                    if isinstance(finding, dict)
                ]
                if args.custom_port and (
                    spec.get("RS_module") != "rs_custom"
                    or spec.get("RS_inst") != "CUSTOM_RS"
                    or spec.get("CRG_source") != "intentionally_wrong_source"
                    or step_check.get("expected") != 1
                    or step_check.get("physical_instances") != 1
                    or step_check.get("effective_step") != 1
                    or contribution_values != [1]
                    or len(instances) != 1
                    or set(instances[0].get("ports", {}))
                    != {"clock_i", "reset_ni", "d", "q"}
                    or len(custom_findings) != 1
                    or custom_findings[0].get("severity") != "warning"
                    or custom_findings[0].get("code")
                    != "CRG_SOURCE_NOT_FOUND"
                    or not isinstance(record.get("crg_source_check"), dict)
                    or record["crg_source_check"].get("status") != "warning"
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: custom module port or CRG evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
                if args.clk_without_rst:
                    finding_codes = {
                        finding.get("code")
                        for finding in record.get("findings", [])
                        if isinstance(finding, dict)
                    }
                    clk_port = (
                        instances[0].get("ports", {}).get("clk", {})
                        if len(instances) == 1 and isinstance(instances[0], dict)
                        else {}
                    )
                    if (
                        spec.get("RS_module") != "rs_clk_only"
                        or spec.get("RS_inst") != "CLK_ONLY_RS"
                        or step_check.get("expected") != 1
                        or step_check.get("physical_instances") != 1
                        or step_check.get("effective_step") != 1
                        or contribution_values != [1]
                        or len(instances) != 1
                        or set(instances[0].get("ports", {})) != {"clk", "d", "q"}
                        or not isinstance(clk_port, dict)
                        or clk_port.get("connection") != "top.u_tile.clk_rs"
                        or finding_codes != {"RST_PORT_MISSING"}
                    ):
                        failed = True
                        print(
                            "GUI_SMOKE_FAIL: clk-present/rst-missing evidence mismatch "
                            f"ports={instances[0].get('ports', {}) if instances else {}} "
                            f"findings={sorted(finding_codes)!r}",
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
            if args.negative or args.clk_without_rst:
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
            evidence_crg_check = evidence.get("crg_source_check")
            evidence_rule = evidence.get("module_rule")
            evidence_configured_rule = original_module_rules.get(
                evidence_spec.get("RS_module") if isinstance(evidence_spec, dict) else "",
                {},
            )
            if not isinstance(evidence_configured_rule, dict):
                evidence_configured_rule = {}
            evidence_clk_port = evidence_configured_rule.get("clk_port", "clk")
            evidence_rst_port = evidence_configured_rule.get("rst_port", "rst_n")
            evidence_crg_instances = (
                evidence_crg_check.get("instances", [])
                if isinstance(evidence_crg_check, dict)
                else []
            )
            evidence_crg_evaluation = (
                evidence_crg_instances[0]
                if isinstance(evidence_crg_instances, list)
                and len(evidence_crg_instances) == 1
                and isinstance(evidence_crg_instances[0], dict)
                else {}
            )
            evidence_matched_crg = evidence_crg_evaluation.get("matched")
            if (
                not isinstance(evidence_spec, dict)
                or evidence_spec.get("RS_CFG_EN") != expected_label
                or not isinstance(evidence_instances, list)
                or not evidence_instances
                or not isinstance(evidence_step, dict)
                or not isinstance(evidence_crg_check, dict)
                or not isinstance(evidence_rule, dict)
                or evidence_rule.get("clk_port") != evidence_clk_port
                or evidence_rule.get("rst_port") != evidence_rst_port
            ):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: evidence panel lost row or instance context",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if args.rs_cfg_dontcare:
                evidence_parameters = (
                    evidence_instances[0].get("parameters", {})
                    if len(evidence_instances) == 1
                    and isinstance(evidence_instances[0], dict)
                    else {}
                )
                if (
                    evidence_rule.get("has_rs_cfg_en") is not False
                    or not isinstance(evidence_parameters, dict)
                    or "RS_CRG_EN" in evidence_parameters
                    or "finding" in evidence
                    or app.finding_tree.get_children()
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: GUI RS_CFG_EN don't-care evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            if args.rs_cfg_na:
                evidence_parameters = (
                    evidence_instances[0].get("parameters", {})
                    if len(evidence_instances) == 1
                    and isinstance(evidence_instances[0], dict)
                    else {}
                )
                if (
                    evidence_rule.get("has_rs_cfg_en") is not True
                    or evidence_spec.get("RS_CFG_EN") != _RS_CFG_NA_TEXT
                    or not isinstance(evidence_parameters, dict)
                    or evidence_parameters.get("RS_CRG_EN") != "1"
                    or "finding" in evidence
                    or app.finding_tree.get_children()
                ):
                    failed = True
                    print(
                        "GUI_SMOKE_FAIL: GUI RS_CFG_EN=NA skip evidence mismatch",
                        file=sys.stderr,
                    )
                    root.destroy()
                    return
            if args.crg_source_mapping and (
                evidence_spec.get("CRG_source") != _CRG_SOURCE_FULL_PATH
                or evidence_spec.get("crg_source_alias") != _CRG_SOURCE_ALIAS
                or evidence_crg_check.get("status") != "pass"
                or not isinstance(evidence_matched_crg, dict)
                or evidence_matched_crg.get("path")
                != [
                    "top.u_crg_mapping.u_occ",
                    "top.u_crg_mapping.u_clk_mux",
                    _CRG_SOURCE_FULL_PATH,
                ]
                or "finding" in evidence
                or app.finding_tree.get_children()
            ):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: GUI CRG_source mapping evidence mismatch",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if args.crg_depth_limit and (
                evidence_crg_check.get("status") != "warning"
                or len(evidence_crg_instances) != 6
                or any(
                    not isinstance(evaluation, dict)
                    or evaluation.get("status") != "depth_limited"
                    or evaluation.get("max_depth") != 2
                    for evaluation in evidence_crg_instances
                )
            ):
                failed = True
                print(
                    "GUI_SMOKE_FAIL: GUI CRG depth-limit evidence mismatch",
                    file=sys.stderr,
                )
                root.destroy()
                return
            if (
                args.negative or args.clk_without_rst
            ) and not isinstance(evidence.get("finding"), dict):
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
        expected_failure = args.negative or args.clk_without_rst
        if (
            args.validate_only or not expected_failure
        ) and app.summary_errors_var.get() != "错误 0":
            failed = True
            print(f"GUI_SMOKE_FAIL: {app.summary_errors_var.get()}", file=sys.stderr)
            root.destroy()
            return
        if (
            not args.validate_only
            and expected_failure
            and app.summary_errors_var.get() == "错误 0"
        ):
            failed = True
            print("GUI_SMOKE_FAIL: expected failure has no errors", file=sys.stderr)
            root.destroy()
            return
        if args.clk_without_rst and app.summary_errors_var.get() != "错误 1":
            failed = True
            print(
                "GUI_SMOKE_FAIL: clk-without-rst must have exactly one error",
                file=sys.stderr,
            )
            root.destroy()
            return
        expected_warning_text = (
            "警告 6"
            if args.crg_depth_limit
            else "警告 1"
            if args.expect_partial_load or args.custom_port
            else "警告 0"
        )
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
            f"case={'negative' if args.negative else 'default-rule' if args.default_rule else 'custom-port' if args.custom_port else 'clk-present-rst-missing' if args.clk_without_rst else 'rs-cfg-dontcare' if args.rs_cfg_dontcare else 'rs-cfg-na' if args.rs_cfg_na else 'crg-source-mapping' if args.crg_source_mapping else 'crg-depth-limit' if args.crg_depth_limit else 'partial-load' if args.expect_partial_load else 'positive'} "
            f"iterations={completed} "
            f"window=mapped window_id={window_id}"
            " header-map=column-index strict-header=false"
            f"{' contract=elab-only' if online else ''}"
            f"{' position-map=tile_core->top.u_tile' if sample_position_mapping else ''}"
            f"{' npi-positions=full-path-only' if sample_position_mapping and not args.validate_only else ''}"
            f"{' rule=unregistered-default has-rs-cfg-en=true step-parameters=[] physical=2 effective=2 contributions=1,1' if args.default_rule else ''}"
            f" rule-ports={'clk/rst_n' if args.default_rule or args.clk_without_rst else 'clock_i/reset_ni' if args.custom_port else 'clk/rst'}"
            f"{' clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING' if args.clk_without_rst else ''}"
            f"{' has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none parsed-rs-cfg-en=任意非标准文本' if args.rs_cfg_dontcare else ''}"
            f"{' rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none' if args.rs_cfg_na else ''}"
            f"{' crg-source-map=core_clock_source->top.u_soc.u_crg_core gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none' if args.crg_source_mapping else ''}"
            f"{' crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND' if args.custom_port else ''}"
            f"{' crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6' if args.crg_depth_limit else ''}"
            f"{' notice=NPI_LOAD_PARTIAL' if args.expect_partial_load else ''}"
            f"{' config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules module-rule-ports=preserved crg-source-db=crud-complete dirty-copy=export-preserved/import-cleared' if config_io_verified else ''}"
            " rules-layout=980x680-fit"
            f"{' schemas=report-v4/inventory-v3' if not args.validate_only else ''}",
            flush=True,
        )
        visible_tabs = {
            "config": app.setup_tab,
            "positions": app.positions_tab,
            "crg-sources": app.crg_sources_tab,
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
