from __future__ import annotations

import os
import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rscheck.config import load_config as read_config
from rscheck.gui import (
    RsCheckApp,
    WorkerOutcome,
    _evidence_payload,
    _module_rule_from_form,
    _parse_step_parameters,
    _position_display,
    _position_mapping_from_form,
    _same_config_path,
)
from rscheck.gui_backend import GuiInputError, GuiRunRequest
from rscheck.model import (
    ConfigError,
    ExcelConfig,
    FIELD_NAMES,
    ModuleRule,
    RtlConfig,
    ToolConfig,
)


class _FakeRoot:
    def __init__(self) -> None:
        self.destroyed = False
        self.after_calls = []

    def destroy(self) -> None:
        self.destroyed = True

    def after(self, delay: int, callback) -> None:
        self.after_calls.append((delay, callback))


class _FakeVar:
    def __init__(self, value=None) -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class GuiLifecycleTests(unittest.TestCase):
    def _make_hardlink(self, source: Path, link: Path) -> None:
        try:
            os.link(source, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard links are unavailable: {exc}")

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

    @staticmethod
    def _complete_config(*, offset: int = 0) -> ToolConfig:
        rules = {
            f"rs_pipe_{offset}": ModuleRule(
                f"rs_pipe_{offset}",
                True,
                ("rs_mode", "pipe_enable"),
            ),
            f"rs_plain_{offset}": ModuleRule(f"rs_plain_{offset}", False),
        }
        return ToolConfig(
            excel=ExcelConfig(
                sheet=f"Signals_{offset}",
                header_row=3 + offset,
                data_start_row=5 + offset,
                validate_headers=bool(offset % 2),
                columns={
                    name: offset * len(FIELD_NAMES) + index + 1
                    for index, name in enumerate(FIELD_NAMES)
                },
            ),
            rtl=RtlConfig(
                clk_port=f"clk_{offset}",
                rst_port=f"rst_{offset}",
                suffix_regex=rf"_C(?P<index>[0-9]+)_{offset}",
                index_base=offset,
                require_contiguous_indices=True,
                allow_leaf_signal_match=True,
                crg_match="module_or_instance",
            ),
            module_rules=rules,
            position_mappings={
                f"core_{offset}": f"tb.dut_{offset}.u_core",
                f"pipe_{offset}": f"tb.dut_{offset}.u_core.u_pipe",
            },
        )

    @staticmethod
    def _app_with_config(config: ToolConfig, path: str | Path) -> RsCheckApp:
        app = object.__new__(RsCheckApp)
        resolved_path = str(Path(path).resolve())
        app.root = _FakeRoot()
        app.project_root = Path.cwd()
        app.config_var = _FakeVar(resolved_path)
        app.sheet_var = _FakeVar(str(config.excel.sheet))
        app.header_row_var = _FakeVar(str(config.excel.header_row))
        app.data_start_row_var = _FakeVar(str(config.excel.data_start_row))
        app.header_check_var = _FakeVar(config.excel.validate_headers)
        app.column_vars = {
            name: _FakeVar(str(config.excel.columns[name])) for name in FIELD_NAMES
        }
        app._loaded_config = config
        app._loaded_config_path = resolved_path
        app._module_rules = dict(config.module_rules)
        app._position_mappings = dict(config.position_mappings)
        app._module_rules_dirty = False
        app._position_mappings_dirty = False
        app._new_rule = Mock()
        app._new_position_mapping = Mock()
        app._render_rule_tree = Mock()
        app._render_position_tree = Mock()
        app.status_var = _FakeVar("ready")
        return app

    @staticmethod
    def _config_state(app: RsCheckApp) -> tuple:
        return (
            app.config_var.get(),
            app._loaded_config,
            app._loaded_config_path,
            dict(app._module_rules),
            dict(app._position_mappings),
            app._module_rules_dirty,
            app._position_mappings_dirty,
            app.sheet_var.get(),
            app.header_row_var.get(),
            app.data_start_row_var.get(),
            app.header_check_var.get(),
            {name: app.column_vars[name].get() for name in FIELD_NAMES},
        )

    def test_export_then_import_round_trips_complete_config_and_databases(self) -> None:
        baseline = self._complete_config(offset=1)
        exported_rules = self._complete_config(offset=7).module_rules
        exported_positions = self._complete_config(offset=7).position_mappings

        with tempfile.TemporaryDirectory() as temporary:
            current_path = Path(temporary) / "current.json"
            export_path = Path(temporary) / "portable.json"
            export_path.write_text("old data", encoding="utf-8")
            export_app = self._app_with_config(baseline, current_path)
            export_app.sheet_var.set("Portable Signals")
            export_app.header_row_var.set("12")
            export_app.data_start_row_var.set("14")
            export_app.header_check_var.set(True)
            for index, name in enumerate(FIELD_NAMES):
                export_app.column_vars[name].set(str(20 + index))
            export_app._module_rules = dict(exported_rules)
            export_app._position_mappings = dict(exported_positions)
            export_app._module_rules_dirty = True
            export_app._position_mappings_dirty = True
            before_export = self._config_state(export_app)

            with patch(
                "rscheck.gui.filedialog.asksaveasfilename",
                return_value=str(export_path),
            ) as save_dialog:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    with patch("rscheck.gui.messagebox.showinfo"):
                        export_app._export_config()

            showerror.assert_not_called()
            self.assertTrue(save_dialog.call_args.kwargs["confirmoverwrite"])
            self.assertEqual(self._config_state(export_app), before_export)

            expected = ToolConfig(
                excel=ExcelConfig(
                    sheet="Portable Signals",
                    header_row=12,
                    data_start_row=14,
                    validate_headers=True,
                    columns={name: 20 + index for index, name in enumerate(FIELD_NAMES)},
                ),
                rtl=baseline.rtl,
                module_rules=exported_rules,
                position_mappings=exported_positions,
            )
            self.assertEqual(read_config(export_path), expected)

            old_config = self._complete_config(offset=2)
            import_app = self._app_with_config(
                old_config, Path(temporary) / "old-current.json"
            )
            with patch(
                "rscheck.gui.filedialog.askopenfilename",
                return_value=str(export_path),
            ):
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    import_app._import_config()

            showerror.assert_not_called()
            self.assertEqual(import_app._loaded_config, expected)
            self.assertEqual(import_app.config_var.get(), str(export_path))
            self.assertEqual(
                import_app._loaded_config_path, str(export_path.resolve())
            )
            self.assertEqual(import_app._module_rules, dict(exported_rules))
            self.assertEqual(import_app._position_mappings, dict(exported_positions))
            self.assertFalse(import_app._module_rules_dirty)
            self.assertFalse(import_app._position_mappings_dirty)
            self.assertEqual(import_app.sheet_var.get(), "Portable Signals")
            self.assertEqual(import_app.header_row_var.get(), "12")
            self.assertEqual(import_app.data_start_row_var.get(), "14")
            self.assertTrue(import_app.header_check_var.get())
            self.assertEqual(
                {name: int(import_app.column_vars[name].get()) for name in FIELD_NAMES},
                dict(expected.excel.columns),
            )
            import_app._new_rule.assert_called_once_with()
            import_app._new_position_mapping.assert_called_once_with()
            import_app._render_rule_tree.assert_called_once_with()
            import_app._render_position_tree.assert_called_once_with()

    def test_import_cancel_keeps_complete_current_state(self) -> None:
        app = self._app_with_config(self._complete_config(offset=3), "current.json")
        app._module_rules_dirty = True
        app._position_mappings_dirty = True
        before = self._config_state(app)

        with patch("rscheck.gui.filedialog.askopenfilename", return_value=""):
            with patch("rscheck.gui.load_complete_config") as load_candidate:
                app._import_config()

        load_candidate.assert_not_called()
        self.assertEqual(self._config_state(app), before)

    def test_invalid_import_is_atomic_and_does_not_prompt_to_discard(self) -> None:
        app = self._app_with_config(self._complete_config(offset=4), "current.json")
        app._module_rules_dirty = True
        app._position_mappings_dirty = True
        app._confirm_discard_rule_changes = Mock(
            side_effect=AssertionError("invalid input must be rejected before confirmation")
        )
        app._confirm_discard_position_changes = Mock(
            side_effect=AssertionError("invalid input must be rejected before confirmation")
        )
        before = self._config_state(app)

        with patch(
            "rscheck.gui.filedialog.askopenfilename", return_value="invalid.json"
        ):
            with patch(
                "rscheck.gui.load_complete_config",
                side_effect=ConfigError("invalid JSON"),
            ):
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    app._import_config()

        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)
        app._new_rule.assert_not_called()
        app._new_position_mapping.assert_not_called()
        app._render_rule_tree.assert_not_called()
        app._render_position_tree.assert_not_called()

    def test_import_aborts_atomically_when_dirty_database_is_not_discarded(self) -> None:
        app = self._app_with_config(self._complete_config(offset=5), "current.json")
        app._module_rules_dirty = True
        app._position_mappings_dirty = True
        app._confirm_discard_rule_changes = Mock(return_value=True)
        app._confirm_discard_position_changes = Mock(return_value=False)
        before = self._config_state(app)
        candidate = self._complete_config(offset=6)

        with patch(
            "rscheck.gui.filedialog.askopenfilename", return_value="candidate.json"
        ):
            with patch("rscheck.gui.load_complete_config", return_value=candidate):
                app._import_config()

        app._confirm_discard_rule_changes.assert_called_once_with()
        app._confirm_discard_position_changes.assert_called_once_with()
        self.assertEqual(self._config_state(app), before)
        app._new_rule.assert_not_called()
        app._new_position_mapping.assert_not_called()
        app._render_rule_tree.assert_not_called()
        app._render_position_tree.assert_not_called()

    def test_import_aborts_when_candidate_changes_during_confirmation(self) -> None:
        app = self._app_with_config(self._complete_config(offset=5), "current.json")
        app._module_rules_dirty = True
        app._confirm_discard_rule_changes = Mock(return_value=True)
        before = self._config_state(app)
        candidate = self._complete_config(offset=6)

        with patch(
            "rscheck.gui.filedialog.askopenfilename", return_value="candidate.json"
        ):
            with patch(
                "rscheck.gui.load_complete_config",
                side_effect=(candidate, ConfigError("candidate changed")),
            ):
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    app._import_config()

        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)
        app._new_rule.assert_not_called()
        app._new_position_mapping.assert_not_called()

    def test_import_path_resolution_failure_is_atomic(self) -> None:
        app = self._app_with_config(self._complete_config(offset=5), "current.json")
        before = self._config_state(app)
        candidate = self._complete_config(offset=6)

        with patch(
            "rscheck.gui.filedialog.askopenfilename", return_value="candidate.json"
        ):
            with patch(
                "rscheck.gui.load_complete_config", return_value=candidate
            ):
                with patch(
                    "rscheck.gui._resolved_config_path",
                    side_effect=GuiInputError("cannot resolve candidate"),
                ):
                    with patch("rscheck.gui.messagebox.showerror") as showerror:
                        app._import_config()

        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)
        app._new_rule.assert_not_called()
        app._new_position_mapping.assert_not_called()

    def test_import_confirms_before_discarding_excel_form_changes(self) -> None:
        app = self._app_with_config(self._complete_config(offset=6), "current.json")
        app.sheet_var.set("draft sheet")
        app._confirm_discard_excel_changes = Mock(return_value=False)
        app._confirm_discard_rule_changes = Mock(
            side_effect=AssertionError("database confirmation must not run")
        )
        before = self._config_state(app)
        candidate = self._complete_config(offset=7)

        with patch(
            "rscheck.gui.filedialog.askopenfilename", return_value="candidate.json"
        ):
            with patch("rscheck.gui.load_complete_config", return_value=candidate):
                app._import_config()

        app._confirm_discard_excel_changes.assert_called_once_with()
        self.assertEqual(self._config_state(app), before)
        app._new_rule.assert_not_called()

    def test_numeric_sheet_name_keeps_string_type_when_exported(self) -> None:
        baseline = self._complete_config(offset=7)
        baseline = replace(
            baseline,
            excel=replace(baseline.excel, sheet="123"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            current = Path(temporary) / "current.json"
            exported = Path(temporary) / "exported.json"
            app = self._app_with_config(baseline, current)

            self.assertEqual(app._sheet_override_from_form(), "")
            app.sheet_var.set("124")
            self.assertEqual(app._sheet_override_from_form(), "124")
            app.sheet_var.set("123")

            with patch(
                "rscheck.gui.filedialog.asksaveasfilename",
                return_value=str(exported),
            ):
                with patch("rscheck.gui.messagebox.showinfo"):
                    app._export_config()

            reloaded = read_config(exported)

        self.assertEqual(reloaded.excel.sheet, "123")
        self.assertIsInstance(reloaded.excel.sheet, str)

    def test_export_cancel_keeps_state_and_does_not_write(self) -> None:
        app = self._app_with_config(self._complete_config(offset=7), "current.json")
        app._module_rules_dirty = True
        before = self._config_state(app)

        with patch("rscheck.gui.filedialog.asksaveasfilename", return_value=""):
            with patch("rscheck.gui.save_config") as save_output:
                app._export_config()

        save_output.assert_not_called()
        self.assertEqual(self._config_state(app), before)

    def test_export_rejects_changed_unloaded_config_path(self) -> None:
        app = self._app_with_config(self._complete_config(offset=7), "current.json")
        app.config_var.set("another-config.json")
        before = self._config_state(app)

        with patch("rscheck.gui.filedialog.asksaveasfilename") as save_dialog:
            with patch("rscheck.gui.save_config") as save_output:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    app._export_config()

        save_dialog.assert_not_called()
        save_output.assert_not_called()
        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)

    def test_export_path_resolution_error_is_controlled(self) -> None:
        app = self._app_with_config(self._complete_config(offset=7), "current.json")
        before = self._config_state(app)

        with patch("rscheck.gui.Path.resolve", side_effect=OSError("bad path")):
            with patch("rscheck.gui.filedialog.asksaveasfilename") as save_dialog:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    app._export_config()

        save_dialog.assert_not_called()
        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)

    def test_export_rejects_invalid_excel_snapshot_before_writing(self) -> None:
        for invalid_case in ("duplicate columns", "data before header"):
            with self.subTest(invalid_case=invalid_case):
                app = self._app_with_config(
                    self._complete_config(offset=7), "current.json"
                )
                if invalid_case == "duplicate columns":
                    app.column_vars["RS_module"].set(
                        app.column_vars["Intf_type"].get()
                    )
                else:
                    app.header_row_var.set("20")
                    app.data_start_row_var.set("20")
                before = self._config_state(app)

                with tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary) / "existing.json"
                    output.write_text("do not replace", encoding="utf-8")
                    with patch(
                        "rscheck.gui.filedialog.asksaveasfilename",
                        return_value=str(output),
                    ):
                        with patch("rscheck.gui.save_config") as save_output:
                            with patch(
                                "rscheck.gui.messagebox.showerror"
                            ) as showerror:
                                app._export_config()

                    save_output.assert_not_called()
                    showerror.assert_called_once()
                    self.assertEqual(
                        output.read_text(encoding="utf-8"), "do not replace"
                    )
                self.assertEqual(self._config_state(app), before)

    def test_export_rejects_current_config_path_even_via_equivalent_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current_path = Path(temporary) / "current.json"
            current_path.write_text("current backing file", encoding="utf-8")
            equivalent_path = current_path.parent / "unused" / ".." / current_path.name
            app = self._app_with_config(
                self._complete_config(offset=8), current_path
            )
            app._module_rules_dirty = True
            before = self._config_state(app)

            with patch(
                "rscheck.gui.filedialog.asksaveasfilename",
                return_value=str(equivalent_path),
            ):
                with patch("rscheck.gui.save_config") as save_output:
                    with patch("rscheck.gui.messagebox.showerror") as showerror:
                        app._export_config()

            save_output.assert_not_called()
            showerror.assert_called_once()
            self.assertEqual(
                current_path.read_text(encoding="utf-8"), "current backing file"
            )
            self.assertEqual(self._config_state(app), before)

    def test_active_config_path_does_not_accept_a_hardlink_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current_path = Path(temporary) / "current.json"
            alias_path = Path(temporary) / "alias.json"
            current_path.write_text("current backing file", encoding="utf-8")
            self._make_hardlink(current_path, alias_path)
            app = self._app_with_config(
                self._complete_config(offset=8), current_path
            )
            app.config_var.set(str(alias_path))
            before = self._config_state(app)

            self.assertFalse(_same_config_path(alias_path, current_path))
            self.assertTrue(
                _same_config_path(
                    alias_path, current_path, include_hardlinks=True
                )
            )
            with patch("rscheck.gui.filedialog.asksaveasfilename") as save_dialog:
                with patch("rscheck.gui.save_config") as save_output:
                    with patch("rscheck.gui.messagebox.showerror") as showerror:
                        app._export_config()

            save_dialog.assert_not_called()
            save_output.assert_not_called()
            showerror.assert_called_once()
            self.assertEqual(self._config_state(app), before)

    def test_export_rejects_a_hardlink_to_current_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current_path = Path(temporary) / "current.json"
            alias_path = Path(temporary) / "export-alias.json"
            current_path.write_text("current backing file", encoding="utf-8")
            self._make_hardlink(current_path, alias_path)
            app = self._app_with_config(
                self._complete_config(offset=8), current_path
            )
            before = self._config_state(app)

            with patch(
                "rscheck.gui.filedialog.asksaveasfilename",
                return_value=str(alias_path),
            ):
                with patch("rscheck.gui.save_config") as save_output:
                    with patch("rscheck.gui.messagebox.showerror") as showerror:
                        app._export_config()

            save_output.assert_not_called()
            showerror.assert_called_once()
            self.assertEqual(
                current_path.read_text(encoding="utf-8"), "current backing file"
            )
            self.assertEqual(self._config_state(app), before)

    def test_database_saves_reject_a_hardlink_config_alias(self) -> None:
        for save_method, dirty_attribute in (
            ("_save_module_rules", "_module_rules_dirty"),
            ("_save_position_mappings", "_position_mappings_dirty"),
        ):
            with self.subTest(save_method=save_method):
                with tempfile.TemporaryDirectory() as temporary:
                    current_path = Path(temporary) / "current.json"
                    alias_path = Path(temporary) / "alias.json"
                    current_path.write_text("current backing file", encoding="utf-8")
                    self._make_hardlink(current_path, alias_path)
                    app = self._app_with_config(
                        self._complete_config(offset=8), current_path
                    )
                    app.config_var.set(str(alias_path))
                    setattr(app, dirty_attribute, True)
                    before = self._config_state(app)

                    with patch("rscheck.gui.load_config") as load_current:
                        with patch("rscheck.gui.save_config") as save_current:
                            with patch(
                                "rscheck.gui.messagebox.showerror"
                            ) as showerror:
                                getattr(app, save_method)()

                    load_current.assert_not_called()
                    save_current.assert_not_called()
                    showerror.assert_called_once()
                    self.assertTrue(current_path.samefile(alias_path))
                    self.assertEqual(
                        current_path.read_text(encoding="utf-8"),
                        "current backing file",
                    )
                    self.assertEqual(self._config_state(app), before)

    def test_export_requires_a_loaded_config(self) -> None:
        app = self._app_with_config(self._complete_config(offset=8), "current.json")
        app._loaded_config = None
        app._loaded_config_path = ""
        before = self._config_state(app)

        with patch("rscheck.gui.filedialog.asksaveasfilename") as save_dialog:
            with patch("rscheck.gui.save_config") as save_output:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
                    app._export_config()

        save_dialog.assert_not_called()
        save_output.assert_not_called()
        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)

    def test_export_write_failure_preserves_current_state(self) -> None:
        app = self._app_with_config(self._complete_config(offset=9), "current.json")
        app._module_rules_dirty = True
        app._position_mappings_dirty = True
        before = self._config_state(app)

        with patch(
            "rscheck.gui.filedialog.asksaveasfilename", return_value="copy.json"
        ):
            with patch(
                "rscheck.gui.save_config", side_effect=ConfigError("disk is full")
            ):
                with patch("rscheck.gui.load_config") as verify_output:
                    with patch("rscheck.gui.messagebox.showerror") as showerror:
                        app._export_config()

        verify_output.assert_not_called()
        showerror.assert_called_once()
        self.assertEqual(self._config_state(app), before)

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

        with patch(
            "rscheck.gui.save_config", return_value=config_path
        ) as save_current:
            with patch(
                "rscheck.gui.load_config",
                side_effect=(current_disk_config, reloaded_config),
            ) as load_current:
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

        with patch(
            "rscheck.gui.save_config", return_value=config_path
        ) as save_current:
            with patch(
                "rscheck.gui.load_config",
                side_effect=(current_disk_config, reloaded_config),
            ) as load_current:
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

        with patch("rscheck.gui.save_config", return_value=config_path):
            with patch(
                "rscheck.gui.load_config", side_effect=(current, reloaded)
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

        with patch("rscheck.gui.save_config", return_value=config_path):
            with patch(
                "rscheck.gui.load_config", side_effect=(current, reloaded)
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

        with patch("rscheck.gui.load_config", return_value=current):
            with patch("rscheck.gui.save_config") as save_config:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
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

        with patch("rscheck.gui.load_config", return_value=current):
            with patch("rscheck.gui.save_config") as save_config:
                with patch("rscheck.gui.messagebox.showerror") as showerror:
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
