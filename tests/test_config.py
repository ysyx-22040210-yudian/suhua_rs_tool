from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rscheck.config import load_config
from rscheck.model import ConfigError


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self) -> None:
        config = load_config(ROOT / "config" / "rscheck.example.json")
        self.assertEqual(config.excel.columns["RS_inst"], 3)
        self.assertEqual(config.rtl.crg_match, "module")
        self.assertFalse(config.rtl.require_contiguous_indices)

    def test_duplicate_column_is_rejected(self) -> None:
        raw = json.loads((ROOT / "config" / "rscheck.example.json").read_text("utf-8"))
        raw["columns"]["rst"] = raw["columns"]["clk"]
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must be unique"):
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


if __name__ == "__main__":
    unittest.main()
