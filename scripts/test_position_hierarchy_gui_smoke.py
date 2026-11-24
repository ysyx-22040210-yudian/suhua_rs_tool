from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(DEFAULT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))

import rscheck.gui as gui_module
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import LIVE_SOURCE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a visible Verdi hierarchy selection GUI smoke test"
    )
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--collector", required=True)
    parser.add_argument("--elab-db", required=True)
    parser.add_argument("--regex", required=True)
    parser.add_argument("--expected-path", required=True)
    parser.add_argument("--npi-lib-dir")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--visible-seconds", type=float, default=1.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.timeout < 1:
        raise SystemExit("--timeout must be positive")
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    if args.visible_seconds < 0:
        raise SystemExit("--visible-seconds must not be negative")

    project_root = Path(args.project_root).resolve()
    collector = Path(args.collector).resolve()
    elab_db = Path(args.elab_db).resolve()
    if not (project_root / "pyproject.toml").is_file():
        raise SystemExit(f"not an rtl-rs-check project: {project_root}")
    if not collector.is_file():
        raise SystemExit(f"collector not found: {collector}")
    if not elab_db.is_dir():
        raise SystemExit(f"elaborated KDB not found: {elab_db}")

    root = tk.Tk()
    app = RsCheckApp(root)
    root.geometry("1180x780")
    root.title("RTL RS Position Hierarchy GUI Smoke")
    callback_errors: list[str] = []
    modal_errors: list[str] = []
    original_showerror = gui_module.messagebox.showerror

    def capture_showerror(title, message, **_options) -> None:
        modal_errors.append(f"{title}: {message}")

    gui_module.messagebox.showerror = capture_showerror

    def callback_exception(exc_type, exc, exc_traceback) -> None:
        callback_errors.append(
            "".join(traceback.format_exception(exc_type, exc, exc_traceback))
        )

    root.report_callback_exception = callback_exception
    app.source_mode_var.set(LIVE_SOURCE)
    app._update_source_mode()
    app.collector_var.set(str(collector))
    app.elab_db_var.set(str(elab_db))
    app.npi_lib_var.set(args.npi_lib_dir or "")
    app.npi_timeout_var.set(str(args.timeout))
    app.position_alias_var.set("hierarchy_probe_alias")
    app.position_hierarchy_regex_var.set(args.regex)
    baseline_mappings = dict(app._position_mappings)
    baseline_dirty = app._position_mappings_dirty
    root.update()
    window_id = hex(root.winfo_id())
    if not root.winfo_ismapped():
        root.destroy()
        raise SystemExit("GUI smoke window is not mapped")
    print(f"POSITION_HIERARCHY_GUI_WINDOW: window=mapped window_id={window_id}")

    deadline = time.monotonic() + args.timeout * args.iterations + 30
    completed = 0
    displayed = 0
    dialog_mapped = False
    failed = False
    failure_message = ""
    phase = "start"

    def fail(message: str) -> None:
        nonlocal failed, failure_message, phase
        if failed:
            return
        failed = True
        failure_message = message
        phase = "failed"
        print(f"POSITION_HIERARCHY_GUI_FAIL: {message}", file=sys.stderr)
        if app.running or app.controller.is_running:
            app._cancel_operation()

    def poll() -> None:
        nonlocal completed, displayed, dialog_mapped, phase
        if callback_errors:
            fail("Tk callback exception:\n" + callback_errors[0])
        if modal_errors:
            fail("unexpected GUI error: " + modal_errors[0])
        if time.monotonic() > deadline:
            fail("timeout")
        if phase == "failed":
            if app.running or app.controller.is_running:
                root.after(100, poll)
                return
            root.destroy()
            return
        if phase == "start":
            app._open_position_hierarchy_search()
            phase = "wait-dialog"
            root.after(100, poll)
            return
        if phase == "wait-dialog":
            dialog = app._position_hierarchy_dialog
            if dialog is None:
                fail("hierarchy candidate dialog was not created")
                root.after(100, poll)
                return
            if not dialog.winfo_ismapped():
                root.after(100, poll)
                return
            dialog_mapped = True
            app.position_hierarchy_regex_var.set(args.regex)
            app._start_position_hierarchy_search()
            if not app.running:
                fail("hierarchy search did not start")
            else:
                phase = "wait"
            root.after(100, poll)
            return
        if phase == "wait":
            if app.running or app.controller.is_running:
                root.after(100, poll)
                return
            tree = app._position_hierarchy_tree
            if tree is None:
                fail("candidate window disappeared before selection")
                root.after(100, poll)
                return
            candidate_paths = dict(app._position_hierarchy_paths)
            displayed = len(candidate_paths)
            selected_iid = next(
                (
                    iid
                    for iid, path in candidate_paths.items()
                    if path == args.expected_path
                ),
                "",
            )
            if not selected_iid:
                fail(
                    f"expected path {args.expected_path!r} is absent from "
                    f"{sorted(candidate_paths.values())!r}"
                )
                root.after(100, poll)
                return
            tree.selection_set(selected_iid)
            app._on_position_hierarchy_candidate_selected()
            app._use_selected_position_hierarchy()
            if app.position_path_var.get() != args.expected_path:
                fail("selected hierarchy path was not copied into the editor")
            elif app.position_alias_var.get() != "hierarchy_probe_alias":
                fail("selecting a hierarchy path changed the position alias")
            elif dict(app._position_mappings) != baseline_mappings:
                fail("selecting a hierarchy path modified the mapping database")
            elif app._position_mappings_dirty != baseline_dirty:
                fail("selecting a hierarchy path changed the mapping dirty state")
            if failed:
                root.after(100, poll)
                return
            completed += 1
            if completed < args.iterations:
                phase = "start"
                root.after(100, poll)
                return
            print(
                "POSITION_HIERARCHY_GUI_PASS: "
                f"regex={args.regex!r} selected={args.expected_path} "
                f"displayed={displayed} iterations={completed} "
                f"mapping-dirty={str(app._position_mappings_dirty).lower()} "
                f"window=mapped dialog=mapped window_id={window_id}"
            )
            phase = "done"
            root.after(max(0, int(args.visible_seconds * 1000)), root.destroy)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        gui_module.messagebox.showerror = original_showerror
    if app.controller.is_running:
        app.controller.cancel()
    if failed:
        if failure_message:
            print(f"POSITION_HIERARCHY_GUI_ERROR: {failure_message}", file=sys.stderr)
        return 1
    return 0 if completed == args.iterations and dialog_mapped else 1


if __name__ == "__main__":
    raise SystemExit(main())
