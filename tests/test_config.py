from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rscheck.config import config_to_dict, load_config, save_config
from rscheck.model import ConfigError


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        self.assertEqual(config.excel.columns["RS_inst"], 3)
        self.assertEqual(config.excel.columns["RS_CFG_EN"], 9)
        self.assertEqual(config.rtl.crg_match, "module")
        self.assertFalse(config.rtl.require_contiguous_indices)
        self.assertTrue(config.module_rules["rs_pipe"].has_rs_cfg_en)
        self.assertEqual(config.module_rules["rs_pipe"].step_parameters, ("rs_mode",))
        self.assertFalse(config.module_rules["rs_plain"].has_rs_cfg_en)

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
                "rs_cfg_en_as_step",
                lambda raw: raw["module_rules"]["rs_pipe"].__setitem__(
                    "step_parameters", ["RS_CFG_EN"]
                ),
                "must not include RS_CFG_EN",
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

    def test_config_save_round_trip_preserves_module_rules(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "saved.json"
            save_config(config, path)
            reloaded = load_config(path)
            raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded, config)
        self.assertEqual(raw, config_to_dict(config))


if __name__ == "__main__":
    unittest.main()
