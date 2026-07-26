from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rscheck.config import (
    config_from_dict,
    config_to_dict,
    load_complete_config,
    load_config,
    save_config,
)
from rscheck.model import ConfigError


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        self.assertEqual(config.excel.columns["RS_inst"], 3)
        self.assertEqual(config.excel.columns["RS_CFG_EN"], 9)
        self.assertFalse(config.excel.validate_headers)
        self.assertEqual(config.rtl.crg_match, "module")
        self.assertFalse(config.rtl.require_contiguous_indices)
        self.assertTrue(config.module_rules["rs_pipe"].has_rs_cfg_en)
        self.assertEqual(config.module_rules["rs_pipe"].step_parameters, ("rs_mode",))
        self.assertEqual(config.module_rules["rs_pipe"].clk_port, "clk")
        self.assertEqual(config.module_rules["rs_pipe"].rst_port, "rst")
        self.assertFalse(config.module_rules["rs_plain"].has_rs_cfg_en)
        self.assertEqual(config.position_mappings, {"tile_core": "top.u_tile"})

    def test_header_validation_is_optional_and_defaults_to_disabled(self) -> None:
        original = json.loads(
            (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
        )
        for name, configured, expected in (
            ("omitted", None, False),
            ("disabled", False, False),
            ("strict_opt_in", True, True),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                raw = json.loads(json.dumps(original))
                if configured is None:
                    raw["excel"].pop("validate_headers", None)
                else:
                    raw["excel"]["validate_headers"] = configured
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                config = load_config(path)
            self.assertEqual(config.excel.validate_headers, expected)
            self.assertEqual(
                config_to_dict(config)["excel"]["validate_headers"],
                expected,
            )

    def test_position_mappings_default_to_empty_when_omitted(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        del raw["position_mappings"]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "legacy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)
            with self.assertRaisesRegex(ConfigError, "missing root sections"):
                load_complete_config(path)
        self.assertEqual(config.position_mappings, {})

    def test_position_mappings_load_and_save_round_trip(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["position_mappings"] = {
            "tile_b": "top.cluster.u_tile_b",
            "tile_a": "top.cluster.u_tile_a",
        }
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "source.json"
            saved = Path(name) / "saved.json"
            source.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(source)
            save_config(config, saved)
            reloaded = load_config(saved)
            saved_raw = json.loads(saved.read_text("utf-8"))
        self.assertEqual(config.position_mappings["tile_a"], "top.cluster.u_tile_a")
        self.assertEqual(reloaded, config)
        self.assertEqual(list(saved_raw["position_mappings"]), ["tile_a", "tile_b"])

    def test_invalid_position_mappings_are_rejected(self) -> None:
        original = json.loads(
            (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
        )
        mutations = (
            ("not_object", [], "JSON object"),
            ("empty_key", {"": "top.u"}, "keys must be non-empty"),
            ("key_whitespace", {" tile": "top.u"}, "key must not have surrounding"),
            ("key_leading_dot", {".tile": "top.u"}, "must not start or end"),
            ("key_trailing_dot", {"tile.": "top.u"}, "must not start or end"),
            ("empty_value", {"tile": ""}, "must be a non-empty string"),
            ("non_string_value", {"tile": 1}, "must be a non-empty string"),
            ("value_whitespace", {"tile": "top.u "}, "value must not have surrounding"),
            ("value_only_dots", {"tile": "..."}, "non-empty RTL path"),
        )
        for name, mappings, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                raw = json.loads(json.dumps(original))
                raw["position_mappings"] = mappings
                path = Path(directory) / "bad.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_duplicate_column_is_rejected(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["columns"]["rst"] = raw["columns"]["clk"]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must be unique"):
                load_config(path)

    def test_rs_cfg_en_column_mapping_is_required(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        del raw["columns"]["RS_CFG_EN"]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "missing_rs_cfg_en.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "missing column mappings: RS_CFG_EN"):
                load_config(path)

    def test_suffix_regex_requires_index_group(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["rtl"]["suffix_regex"] = ".+"
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "named group"):
                load_config(path)

    def test_string_boolean_is_rejected(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["rtl"]["require_contiguous_indices"] = "false"
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "true or false"):
                load_config(path)

    def test_unknown_config_key_is_rejected(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["rtl"]["rest_port"] = "rst_n"
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unknown keys"):
                load_config(path)

    def test_module_rules_are_required_and_strict(self) -> None:
        original = json.loads(
            (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
        )
        mutations = (
            ("missing", lambda raw: raw.pop("module_rules"), "module_rules"),
            (
                "bad_boolean",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "has_rs_cfg_en", "true"
                ),
                "true or false",
            ),
            (
                "bad_parameter_array",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "step_parameters", "rs_mode"
                ),
                "JSON array",
            ),
            (
                "duplicate_parameter",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "step_parameters", ["rs_mode", "rs_mode"]
                ),
                "duplicate step parameter",
            ),
            (
                "parameter_whitespace",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "step_parameters", ["rs mode"]
                ),
                "must not contain whitespace",
            ),
            (
                "rs_crg_en_as_step",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "step_parameters", ["RS_CRG_EN"]
                ),
                "must not include RS_CRG_EN",
            ),
            (
                "empty_clk_port",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "clk_port", ""
                ),
                "must be a non-empty string",
            ),
            (
                "non_string_rst_port",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "rst_port", 1
                ),
                "must be a non-empty string",
            ),
            (
                "unknown_rule_key",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "mode", "nonzero"
                ),
                "unknown keys",
            ),
        )
        for name, mutation, message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                raw = json.loads(json.dumps(original))
                mutation(raw)
                path = Path(directory) / "bad.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_legacy_module_ports_inherit_rtl_ports_and_export_explicitly(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["rtl"]["clk_port"] = "legacy_clock"
        raw["rtl"]["rst_port"] = "legacy_reset"
        for rule in raw["module_rules"].values():
            rule.pop("clk_port")
            rule.pop("rst_port")

        config = config_from_dict(raw)
        exported = config_to_dict(config)

        for rule in config.module_rules.values():
            self.assertEqual(rule.clk_port, "legacy_clock")
            self.assertEqual(rule.rst_port, "legacy_reset")
        for rule in exported["module_rules"].values():
            self.assertEqual(rule["clk_port"], "legacy_clock")
            self.assertEqual(rule["rst_port"], "legacy_reset")

    def test_rs_cfg_en_remains_valid_as_an_ordinary_step_parameter(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["module_rules"]["rs_pipe"]["step_parameters"] = ["RS_CFG_EN"]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "legacy-parameter-name.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)

        self.assertEqual(
            config.module_rules["rs_pipe"].step_parameters,
            ("RS_CFG_EN",),
        )

    def test_config_save_round_trip_preserves_module_rules(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "saved.json"
            save_config(config, path)
            reloaded = load_config(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded, config)
        self.assertEqual(raw, config_to_dict(config))

    def test_in_memory_config_round_trip_preserves_all_root_sections(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")

        normalized = config_from_dict(config_to_dict(config))

        self.assertEqual(normalized, config)
        self.assertEqual(
            set(config_to_dict(normalized)),
            {"excel", "columns", "rtl", "position_mappings", "module_rules"},
        )

    def test_save_rejects_invalid_snapshot_before_replacing_target(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        columns = dict(config.excel.columns)
        columns["RS_module"] = columns["Intf_type"]
        invalid = replace(config, excel=replace(config.excel, columns=columns))

        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "existing.json"
            target.write_text("keep this content", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must be unique"):
                save_config(invalid, target)
            content = target.read_text(encoding="utf-8")

        self.assertEqual(content, "keep this content")

    def test_unicode_digit_is_not_accepted_as_an_integer(self) -> None:
        raw = json.loads(
            (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
        )
        raw["columns"]["Intf_type"] = "²"

        with self.assertRaisesRegex(ConfigError, "positive integer"):
            config_from_dict(raw)

    def test_save_path_resolution_error_is_controlled(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")

        with patch("rscheck.config.Path.resolve", side_effect=OSError("bad path")):
            with self.assertRaisesRegex(ConfigError, "cannot save config file"):
                save_config(config, "output.json")


if __name__ == "__main__":
    unittest.main()
