from __future__ import annotations

import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rscheck.gui import (
    RsCheckApp,
    WorkerOutcome,
    _evidence_payload,
    _module_rule_from_form,
    _parse_step_parameters,
    _position_display,
    _position_mapping_from_form,
)
from rscheck.gui_backend import GuiInputError, GuiRunRequest
from rscheck.model import ExcelConfig, ModuleRule, RtlConfig, ToolConfig


class _FakeRoot:
    def __init__(self) -> None:
        self.destroyed = False
        self.after_calls = []

    def destroy(self) -> None:
        self.destroyed = True

    def after(self, delay: int, callback) -> None:
        self.after_calls.append((delay, callback))


class GuiLifecycleTests(unittest.TestCase):
    @staticmethod
    def _config(
        *,
        module_rules=None,
        position_mappings=None,
    ) -> ToolConfig:
        return ToolConfig(
            excel=ExcelConfig(),
            rtl=RtlConfig(),
            module_rules=module_rules or {},
            position_mappings=position_mappings or {},
        )

    def test_finding_evidence_keeps_instance_parameters(self) -> None:
        record = {
            "spec": {
                "RS_CFG_EN": "假门控",
                "position_alias": "core_pipe",
                "position": "tb.dut.u_core.u_pipe",
            },
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
        self.assertEqual(evidence["spec"]["position_alias"], "core_pipe")
        self.assertEqual(evidence["spec"]["position"], "tb.dut.u_core.u_pipe")

    def test_module_rule_form_parses_multiple_parameters(self) -> None:
        rule = _module_rule_from_form(" rs_pipe ", True, "rs_mode， pipe_enable")
        self.assertEqual(rule.name, "rs_pipe")
        self.assertTrue(rule.has_rs_cfg_en)
        self.assertEqual(rule.step_parameters, ("rs_mode", "pipe_enable"))
        with self.assertRaisesRegex(GuiInputError, "不能重复"):
            _parse_step_parameters("rs_mode,rs_mode")
        with self.assertRaisesRegex(GuiInputError, "不能同时"):
            _parse_step_parameters("RS_CFG_EN")

    def test_position_mapping_form_trims_and_requires_both_values(self) -> None:
        alias, rtl_path = _position_mapping_from_form(
            " core pipe ", " tb.dut.core u_rs "
        )
        self.assertEqual(alias, "core pipe")
        self.assertEqual(rtl_path, "tb.dut.core u_rs")
        with self.assertRaisesRegex(GuiInputError, "position 简写不能为空"):
            _position_mapping_from_form("  ", "tb.dut")
        with self.assertRaisesRegex(GuiInputError, "不能以.*开头或结尾"):
            _position_mapping_from_form(".core", "tb.dut")
        with self.assertRaisesRegex(GuiInputError, "RTL 层次全路径不能为空"):
            _position_mapping_from_form("core", "  ")
        with self.assertRaisesRegex(GuiInputError, "不能仅由"):
            _position_mapping_from_form("core", "...")

    def test_position_display_shows_alias_and_resolved_path(self) -> None:
        self.assertEqual(
            _position_display(
                {
                    "position_alias": "core_pipe",
                    "position": "tb.dut.u_core.u_pipe",
                }
            ),
            "core_pipe -> tb.dut.u_core.u_pipe",
        )
        self.assertEqual(
            _position_display({"position_alias": "", "position": "tb.dut"}),
            "tb.dut",
        )

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
        app._position_mappings_dirty = False
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

    def test_unsaved_position_mappings_block_operations(self) -> None:
        app = object.__new__(RsCheckApp)
        app._module_rules_dirty = False
        app._position_mappings_dirty = True
        app._running = False
        app.controller = SimpleNamespace(is_running=False)
        app.root = _FakeRoot()
        app._request = Mock(side_effect=AssertionError("must not build a request"))

        with patch("rscheck.gui.messagebox.showerror") as showerror:
            app._start_operation("check")

        app._request.assert_not_called()
        showerror.assert_called_once_with(
            "映射未保存",
            "请先在 Position 映射库页保存修改",
            parent=app.root,
        )

    def test_saving_position_mappings_uses_current_disk_config(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline_rules = {
            "baseline": ModuleRule("baseline", False),
        }
        external_rules = {
            "external": ModuleRule("external", True),
        }
        loaded_config = self._config(
            module_rules=baseline_rules,
            position_mappings={"old": "tb.dut.old"},
        )
        current_disk_config = self._config(
            module_rules=external_rules,
            position_mappings={"old": "tb.dut.old"},
        )
        reloaded_config = self._config(
            module_rules=external_rules,
            position_mappings={"core": "tb.dut.u_core"},
        )
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = loaded_config
        app._loaded_config_path = str(Path(config_path).resolve())
        app._position_mappings = {"core": "tb.dut.u_core"}
        app._position_mappings_dirty = True
        app._module_rules_dirty = True
        app._module_rules = {"draft": ModuleRule("draft", False)}
        app._new_rule = Mock()
        app._render_rule_tree = Mock()
        app._render_position_tree = Mock()
        app.status_var = SimpleNamespace(set=Mock())

        with (
            patch("rscheck.gui.save_config", return_value=config_path) as save_current,
            patch(
                "rscheck.gui.load_config",
                side_effect=(current_disk_config, reloaded_config),
            ) as load_current,
        ):
            app._save_position_mappings()

        saved_config = save_current.call_args.args[0]
        self.assertEqual(saved_config.module_rules, external_rules)
        self.assertEqual(
            saved_config.position_mappings, {"core": "tb.dut.u_core"}
        )
        self.assertEqual(load_current.call_count, 2)
        self.assertEqual(app._loaded_config.module_rules, baseline_rules)
        self.assertEqual(
            app._loaded_config.position_mappings, {"core": "tb.dut.u_core"}
        )
        self.assertFalse(app._position_mappings_dirty)
        self.assertTrue(app._module_rules_dirty)
        app._new_rule.assert_not_called()
        app._render_rule_tree.assert_not_called()
        app._render_position_tree.assert_called_once_with()

    def test_saving_module_rules_uses_current_disk_config(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline_positions = {"baseline": "tb.dut.baseline"}
        external_positions = {"external": "tb.dut.external"}
        baseline_rule = ModuleRule("rs_pipe", False)
        edited_rule = ModuleRule("rs_pipe", True)
        edited_rules = {"rs_pipe": edited_rule}
        loaded_config = self._config(
            module_rules={"rs_pipe": baseline_rule},
            position_mappings=baseline_positions,
        )
        current_disk_config = self._config(
            module_rules={"rs_pipe": baseline_rule},
            position_mappings=external_positions,
        )
        reloaded_config = self._config(
            module_rules=edited_rules,
            position_mappings=external_positions,
        )
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = loaded_config
        app._loaded_config_path = str(Path(config_path).resolve())
        app._module_rules = edited_rules
        app._module_rules_dirty = True
        app._position_mappings_dirty = True
        app._position_mappings = {"draft": "tb.dut.draft"}
        app._new_position_mapping = Mock()
        app._render_position_tree = Mock()
        app._render_rule_tree = Mock()
        app.status_var = SimpleNamespace(set=Mock())

        with (
            patch("rscheck.gui.save_config", return_value=config_path) as save_current,
            patch(
                "rscheck.gui.load_config",
                side_effect=(current_disk_config, reloaded_config),
            ) as load_current,
        ):
            app._save_module_rules()

        saved_config = save_current.call_args.args[0]
        self.assertEqual(saved_config.module_rules, edited_rules)
        self.assertEqual(saved_config.position_mappings, external_positions)
        self.assertEqual(load_current.call_count, 2)
        self.assertEqual(app._loaded_config.module_rules, edited_rules)
        self.assertEqual(
            app._loaded_config.position_mappings, baseline_positions
        )
        self.assertFalse(app._module_rules_dirty)
        self.assertTrue(app._position_mappings_dirty)
        app._new_position_mapping.assert_not_called()
        app._render_position_tree.assert_not_called()
        app._render_rule_tree.assert_called_once_with()

    def test_saving_position_mappings_refreshes_clean_module_rules(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline_rule = ModuleRule("baseline", False)
        external_rule = ModuleRule("external", True)
        baseline = self._config(
            module_rules={"baseline": baseline_rule},
            position_mappings={"old": "tb.dut.old"},
        )
        current = self._config(
            module_rules={"external": external_rule},
            position_mappings={"old": "tb.dut.old"},
        )
        reloaded = self._config(
            module_rules={"external": external_rule},
            position_mappings={"new": "tb.dut.new"},
        )
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = baseline
        app._loaded_config_path = str(Path(config_path).resolve())
        app._position_mappings = {"new": "tb.dut.new"}
        app._position_mappings_dirty = True
        app._module_rules_dirty = False
        app._new_rule = Mock()
        app._render_rule_tree = Mock()
        app._render_position_tree = Mock()
        app.status_var = SimpleNamespace(set=Mock())

        with (
            patch("rscheck.gui.save_config", return_value=config_path),
            patch("rscheck.gui.load_config", side_effect=(current, reloaded)),
        ):
            app._save_position_mappings()

        self.assertIs(app._loaded_config, reloaded)
        self.assertEqual(app._module_rules, {"external": external_rule})
        app._new_rule.assert_called_once_with()
        app._render_rule_tree.assert_called_once_with()

    def test_saving_module_rules_refreshes_clean_position_mappings(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline_rule = ModuleRule("rs_pipe", False)
        edited_rule = ModuleRule("rs_pipe", True)
        baseline = self._config(
            module_rules={"rs_pipe": baseline_rule},
            position_mappings={"old": "tb.dut.old"},
        )
        current = self._config(
            module_rules={"rs_pipe": baseline_rule},
            position_mappings={"external": "tb.dut.external"},
        )
        reloaded = self._config(
            module_rules={"rs_pipe": edited_rule},
            position_mappings={"external": "tb.dut.external"},
        )
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = baseline
        app._loaded_config_path = str(Path(config_path).resolve())
        app._module_rules = {"rs_pipe": edited_rule}
        app._module_rules_dirty = True
        app._position_mappings_dirty = False
        app._new_position_mapping = Mock()
        app._render_position_tree = Mock()
        app._render_rule_tree = Mock()
        app.status_var = SimpleNamespace(set=Mock())

        with (
            patch("rscheck.gui.save_config", return_value=config_path),
            patch("rscheck.gui.load_config", side_effect=(current, reloaded)),
        ):
            app._save_module_rules()

        self.assertIs(app._loaded_config, reloaded)
        self.assertEqual(
            app._position_mappings, {"external": "tb.dut.external"}
        )
        app._new_position_mapping.assert_called_once_with()
        app._render_position_tree.assert_called_once_with()

    def test_position_mapping_save_rejects_external_same_database_change(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline = self._config(position_mappings={"core": "tb.dut.old"})
        current = self._config(position_mappings={"core": "tb.dut.external"})
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = baseline
        app._loaded_config_path = str(Path(config_path).resolve())
        app._position_mappings = {"core": "tb.dut.local"}
        app._position_mappings_dirty = True

        with (
            patch("rscheck.gui.load_config", return_value=current),
            patch("rscheck.gui.save_config") as save_config,
            patch("rscheck.gui.messagebox.showerror") as showerror,
        ):
            app._save_position_mappings()

        save_config.assert_not_called()
        showerror.assert_called_once_with(
            "保存冲突",
            "Position 映射库已被外部修改，请先重新加载配置后再保存",
            parent=app.root,
        )
        self.assertTrue(app._position_mappings_dirty)
        self.assertIs(app._loaded_config, baseline)

    def test_module_rule_save_rejects_external_same_database_change(self) -> None:
        app = object.__new__(RsCheckApp)
        config_path = "config.json"
        baseline_rule = ModuleRule("rs_pipe", False)
        external_rule = ModuleRule("rs_pipe", True)
        local_rule = ModuleRule("rs_pipe", False, ("rs_mode",))
        baseline = self._config(module_rules={"rs_pipe": baseline_rule})
        current = self._config(module_rules={"rs_pipe": external_rule})
        app.root = _FakeRoot()
        app.config_var = SimpleNamespace(get=lambda: config_path)
        app._loaded_config = baseline
        app._loaded_config_path = str(Path(config_path).resolve())
        app._module_rules = {"rs_pipe": local_rule}
        app._module_rules_dirty = True

        with (
            patch("rscheck.gui.load_config", return_value=current),
            patch("rscheck.gui.save_config") as save_config,
            patch("rscheck.gui.messagebox.showerror") as showerror,
        ):
            app._save_module_rules()

        save_config.assert_not_called()
        showerror.assert_called_once_with(
            "保存冲突",
            "模块规则库已被外部修改，请先重新加载配置后再保存",
            parent=app.root,
        )
        self.assertTrue(app._module_rules_dirty)
        self.assertIs(app._loaded_config, baseline)

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

    def test_close_keeps_window_when_dirty_positions_are_not_discarded(self) -> None:
        app = object.__new__(RsCheckApp)
        app._module_rules_dirty = False
        app._position_mappings_dirty = True
        app._running = False
        app.controller = SimpleNamespace(is_running=False)
        app.root = _FakeRoot()
        app._confirm_discard_position_changes = Mock(return_value=False)

        app._on_close()

        self.assertFalse(app.root.destroyed)
        app._confirm_discard_position_changes.assert_called_once_with()

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
