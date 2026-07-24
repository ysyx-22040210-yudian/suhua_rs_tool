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
    parser.add_argument("--negative", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def _write_generated_inputs(output: Path, row_count: int) -> tuple[Path, Path]:
    specs_path = output / "generated_specs.csv"
    inventory_path = output / "generated_inventory.json"
    positions = {}
    with specs_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "Intf_type",
                "RS_module",
                "RS_inst",
                "position",
                "step",
                "clk",
                "rst",
                "CRG_source",
            )
        )
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
            {"schema_version": 1, "positions": positions, "warnings": []},
            stream,
            ensure_ascii=True,
        )
    return specs_path, inventory_path


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
    if args.negative and args.generated_rows:
        raise SystemExit("--negative and --generated-rows are mutually exclusive")

    root = tk.Tk()
    app = RsCheckApp(root)
    root.title("RTL RS Check GUI Smoke")
    modal_errors: list[str] = []

    def capture_error(title: str, message: str, **_kwargs: object) -> None:
        modal_errors.append(f"{title}: {message}")

    gui_module.messagebox.showerror = capture_error
    temp = tempfile.TemporaryDirectory(prefix="rscheck-gui-smoke-")
    output = Path(temp.name)
    expected_rows = 1 if args.negative else args.generated_rows or 2
    if args.generated_rows:
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
    app.config_var.set(str(project_root / "config" / "rscheck.example.json"))
    app.sheet_var.set("1")
    app.json_report_var.set(str(output / "report.json"))
    app.csv_report_var.set(str(output / "report.csv"))

    if online:
        app.source_mode_var.set(LIVE_SOURCE)
        app.collector_var.set(args.collector)
        app.elab_db_var.set(args.elab_db)
        app.npi_lib_var.set(args.npi_lib_dir or "")
        app.npi_timeout_var.set(str(args.timeout))
        app.keep_inventory_var.set(str(output / "inventory.json"))
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
        if len(app.result_tree.get_children()) != expected_rows:
            failed = True
            print(
                f"GUI_SMOKE_FAIL: expected {expected_rows} result rows",
                file=sys.stderr,
            )
            root.destroy()
            return
        if app.summary_rows_var.get() != f"行数 {expected_rows}":
            failed = True
            print(
                f"GUI_SMOKE_FAIL: {app.summary_rows_var.get()}", file=sys.stderr
            )
            root.destroy()
            return
        if (
            args.validate_only or not args.negative
        ) and app.summary_errors_var.get() != "错误 0":
            failed = True
            print(
                f"GUI_SMOKE_FAIL: {app.summary_errors_var.get()}", file=sys.stderr
            )
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
        if app.summary_warnings_var.get() != "警告 0":
            failed = True
            print(
                f"GUI_SMOKE_FAIL: {app.summary_warnings_var.get()}", file=sys.stderr
            )
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
            f"rows={app.summary_rows_var.get()} errors={app.summary_errors_var.get()} "
            f"warnings={app.summary_warnings_var.get()} "
            f"mode={'validate' if args.validate_only else 'online' if online else 'offline'} "
            f"case={'negative' if args.negative else 'positive'} iterations={completed} "
            f"window=mapped window_id={window_id}"
            f"{' contract=elab-only' if online else ''}",
            flush=True,
        )
        app.notebook.select(app.results_tab)
        root.after(max(100, int(args.visible_seconds * 1000)), root.destroy)

    root.after(max(0, int(args.start_delay * 1000)), poll)
    root.mainloop()
    temp.cleanup()
    return 1 if failed or modal_errors or completed != args.iterations else 0


if __name__ == "__main__":
    raise SystemExit(main())
