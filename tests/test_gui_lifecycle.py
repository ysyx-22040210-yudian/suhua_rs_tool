from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rscheck.gui import (
    RsCheckApp,
    WorkerOutcome,
    _evidence_payload,
    _module_rule_from_form,
    _parse_step_parameters,
)
from rscheck.gui_backend import GuiInputError, GuiRunRequest


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
            "module_rule": {
                "name": "pipe",
                "has_rs_cfg_en": True,
                "step_parameters": ["rs_mode"],
            },
            "step_check": {"effective_step": 1},
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
        self.assertEqual(evidence["step_check"]["effective_step"], 1)

    def test_module_rule_form_parses_multiple_parameters(self) -> None:
        rule = _module_rule_from_form(" rs_pipe ", True, "rs_mode， pipe_enable")
        self.assertEqual(rule.name, "rs_pipe")
        self.assertTrue(rule.has_rs_cfg_en)
        self.assertEqual(rule.step_parameters, ("rs_mode", "pipe_enable"))
        with self.assertRaisesRegex(GuiInputError, "不能重复"):
            _parse_step_parameters("rs_mode,rs_mode")
        with self.assertRaisesRegex(GuiInputError, "不能同时"):
            _parse_step_parameters("RS_CFG_EN")

    def test_unsaved_module_rules_block_operations(self) -> None:
        app = object.__new__(RsCheckApp)
        app._module_rules_dirty = True
        app._running = False
        app.controller = SimpleNamespace(is_running=False)
        app.root = _FakeRoot()
        app._request = Mock(side_effect=AssertionError("must not build a request"))

        with patch("rscheck.gui.messagebox.showerror") as showerror:
            app._start_operation("check")

        app._request.assert_not_called()
        showerror.assert_called_once()

    def test_changed_config_path_must_be_loaded_before_running(self) -> None:
        app = object.__new__(RsCheckApp)
        app._module_rules_dirty = False
        app._running = False
        app.controller = SimpleNamespace(is_running=False)
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: "new-config.json")
        app._loaded_config = object()
        app._loaded_config_path = "old-config.json"
        app._request = Mock(side_effect=AssertionError("must not build a request"))

        with patch("rscheck.gui.messagebox.showerror") as showerror:
            app._start_operation("check")

        app._request.assert_not_called()
        showerror.assert_called_once()

    def test_close_keeps_window_when_dirty_rules_are_not_discarded(self) -> None:
        app = object.__new__(RsCheckApp)
        app._module_rules_dirty = True
        app._running = False
        app.controller = SimpleNamespace(is_running=False)
        app.root = _FakeRoot()
        app._confirm_discard_rule_changes = Mock(return_value=False)

        app._on_close()

        self.assertFalse(app.root.destroyed)
        app._confirm_discard_rule_changes.assert_called_once_with()

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
