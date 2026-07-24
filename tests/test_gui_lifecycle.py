from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from rscheck.gui import RsCheckApp, WorkerOutcome
from rscheck.gui_backend import GuiRunRequest


class _FakeRoot:
    def __init__(self) -> None:
        self.destroyed = False
        self.after_calls = []

    def destroy(self) -> None:
        self.destroyed = True

    def after(self, delay: int, callback) -> None:
        self.after_calls.append((delay, callback))


class GuiLifecycleTests(unittest.TestCase):
    def test_cancel_before_worker_start_never_invokes_process_controller(self) -> None:
        app = object.__new__(RsCheckApp)
        app._cancel_requested = threading.Event()
        app._cancel_requested.set()
        app.events = queue.Queue()
        app.controller = SimpleNamespace(run=Mock(side_effect=AssertionError("must not run")))

        app._run_worker("check", GuiRunRequest(excel_path="x", config_path="c"))

        app.controller.run.assert_not_called()
        outcome = app.events.get_nowait()
        self.assertIsInstance(outcome, WorkerOutcome)
        self.assertIsNone(outcome.process)

    def test_closing_waits_for_worker_outcome_before_destroying_root(self) -> None:
        app = object.__new__(RsCheckApp)
        app.root = _FakeRoot()
        app.events = queue.Queue()
        app._closing = True
        app._running = True
        app.controller = SimpleNamespace(is_running=False)

        def set_running(value: bool) -> None:
            app._running = value

        app._set_running = set_running
        app._poll_events()
        self.assertFalse(app.root.destroyed)

        app.events.put(WorkerOutcome(action="check"))
        app._poll_events()
        self.assertFalse(app.running)
        self.assertTrue(app.root.destroyed)


if __name__ == "__main__":
    unittest.main()
