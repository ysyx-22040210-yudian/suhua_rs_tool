from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from rscheck.gui import RsCheckApp, WorkerOutcome, _evidence_payload
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
    def test_finding_evidence_keeps_instance_parameters(self) -> None:
        record = {
            "spec": {"RS_CFG_EN": "假门控"},
            "matched_instances": [
                {
                    "full_name": "top.u.PIPE_C0",
                    "parameters": {"RS_CFG_EN": "0"},
                }
            ],
        }
        finding = {
            "severity": "error",
            "code": "RS_CFG_EN_LABEL_MISMATCH",
            "instance": "top.u.PIPE_C0",
            "expected": "假门控",
            "actual": "真门控",
        }

        evidence = _evidence_payload(record, finding)

        self.assertEqual(
            evidence["matched_instances"][0]["parameters"]["RS_CFG_EN"], "0"
        )
        self.assertEqual(evidence["finding"]["code"], "RS_CFG_EN_LABEL_MISMATCH")

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
