from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))

import rscheck.gui as gui_module
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import INVENTORY_SOURCE, LIVE_SOURCE


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

    def poll() -> None:
        nonlocal completed, failed, started, timed_out
        if not started:
            started = True
            app._start_operation(operation)
            root.after(100, poll)
            return
        if timed_out:
            if app.running or app.controller.is_running:
                root.after(100, poll)
                return
            root.destroy()
            return
        if time.monotonic() > deadline:
            failed = True
            timed_out = True
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
            f"case={'negative' if args.negative else 'positive'} iterations={completed}",
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
