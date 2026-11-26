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
    parser.add_argument("--target", choices=("position", "CRG_source"), default="position")
    parser.add_argument("--npi-lib-dir")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--expect-first", choices=("collector", "disk"),
                        help="assert first-open source (use a private cache directory for repeatable tests)")
    parser.add_argument("--exercise-clear", action="store_true", help="also verify a manual clear in the actual picker")
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
    browse_calls = 0
    real_run = app.controller.run
    def count_browse(command, **kwargs):
        nonlocal browse_calls
        if "--browse" in command:
            browse_calls += 1
        return real_run(command, **kwargs)
    app.controller.run = count_browse
    root.geometry("1180x780")
    root.title(f"RTL RS {args.target} DUT Hierarchy GUI Smoke")
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
    app.crg_source_alias_var.set("hierarchy_crg_alias")
    is_position = args.target == "position"
    editor = app.position_path_var if is_position else app.crg_source_path_var
    other = app.crg_source_path_var if is_position else app.position_path_var
    other.set("keep.other.path")
    app.notebook.select(app.positions_tab if is_position else app.crg_sources_tab)
    baseline_mappings = dict(app._position_mappings)
    baseline_dirty = app._position_mappings_dirty
    baseline_crg = dict(app._crg_source_mappings), app._crg_source_mappings_dirty
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
    open_started = 0.0
    open_times = []
    open_sources = []

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
        nonlocal completed, displayed, dialog_mapped, phase, open_started
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
            open_started = time.monotonic()
            (app.position_hierarchy_pick_button if is_position else app.crg_source_hierarchy_pick_button).invoke()
            phase = "wait-dialog"
            root.after(100, poll)
            return
        if phase in ("wait-dialog", "wait-refresh"):
            dialog = app._position_hierarchy_dialog
            if dialog is None:
                fail("hierarchy candidate dialog was not created")
                root.after(100, poll)
                return
            if not dialog.winfo_ismapped() or app.running or app.controller.is_running:
                root.after(100, poll)
                return
            dialog_mapped = True
            open_times.append((time.monotonic() - open_started) * 1000)
            status = app.position_hierarchy_status_var.get()
            origin = {"已从 KDB 加载": "collector", "命中内存缓存": "memory", "命中本地缓存": "disk"}.get(status.split(" · ")[0])
            expected = "collector" if phase == "wait-refresh" else "memory" if completed else args.expect_first
            if not origin or (expected and origin != expected):
                fail(f"unexpected hierarchy source: {status}; expected {expected}")
                root.after(100, poll)
                return
            open_sources.append(origin)
            if args.exercise_clear and not completed and phase == "wait-dialog":
                app.hierarchy_cache_clear_button.invoke()
                phase = "wait-refresh"
                root.after(100, poll)
                return
            model = app._hierarchy_model
            tree = app._position_hierarchy_tree
            if not model or args.expected_path not in model.by_path:
                fail("unfiltered DUT tree is absent or incomplete")
                root.after(100, poll)
                return
            # Reveal the target by walking the actual, initially collapsed
            # branches before using a regex. Fire the same event as Tk's arrow.
            lineage = []
            parent = model.parents[args.expected_path]
            while parent:
                lineage.append(parent)
                parent = model.parents[parent]
            for parent in reversed(lineage):
                node = app._scope_iid(parent)
                tree.focus(node)
                tree.event_generate("<<TreeviewOpen>>")
                tree.item(node, open=True)
            if not tree.exists(app._scope_iid(args.expected_path)):
                fail("unfiltered tree failed to reveal the expected path")
                root.after(100, poll)
                return
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
            displayed = len(app._hierarchy_hits)
            selected_iid = next(
                (
                    iid
                    for iid, path in candidate_paths.items()
                    if path == args.expected_path
                ),
                "",
            )
            if not selected_iid or args.expected_path not in app._hierarchy_hits:
                fail(
                    f"expected path {args.expected_path!r} is absent from "
                    f"{sorted(candidate_paths.values())!r}"
                )
                root.after(100, poll)
                return
            tree.selection_set(selected_iid)
            app._on_position_hierarchy_candidate_selected()
            app._use_selected_position_hierarchy()
            if editor.get() != args.expected_path or other.get() != "keep.other.path":
                fail("selected hierarchy path was not copied into the editor")
            elif (app.position_alias_var.get(), app.crg_source_alias_var.get()) != ("hierarchy_probe_alias", "hierarchy_crg_alias"):
                fail("selecting a hierarchy path changed an alias")
            elif dict(app._position_mappings) != baseline_mappings:
                fail("selecting a hierarchy path modified the mapping database")
            elif app._position_mappings_dirty != baseline_dirty:
                fail("selecting a hierarchy path changed the mapping dirty state")
            elif (app._crg_source_mappings, app._crg_source_mappings_dirty) != baseline_crg:
                fail("selecting a hierarchy path changed the CRG database or dirty state")
            if failed:
                root.after(100, poll)
                return
            completed += 1
            if completed < args.iterations:
                phase = "start"
                root.after(100, poll)
                return
            if browse_calls != open_sources.count("collector"):
                fail(f"unexpected extra hierarchy collections: {browse_calls}, sources={open_sources}")
                root.after(100, poll)
                return
            print(
                ("POSITION_HIERARCHY_GUI_PASS: " if is_position else "CRG_SOURCE_HIERARCHY_GUI_PASS: ")
                +
                f"regex={args.regex!r} selected={args.expected_path} "
                f"displayed={displayed} iterations={completed} "
                f"mapping-dirty={str(app._position_mappings_dirty).lower()} "
                f"window=mapped dialog=mapped window_id={window_id} "
                f"browse_calls={browse_calls} first={open_sources[0]} memory_hits={open_sources.count('memory')} "
                f"max_reopen_ms={max(open_times[2 if args.exercise_clear else 1:], default=0):.0f} "
                f"manual_reload={int(args.exercise_clear)}"
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
