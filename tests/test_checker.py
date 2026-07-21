from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.excel_reader import read_spec_rows
from rscheck.inventory import load_inventory
from rscheck.model import ConfigError, InventoryError, RtlConfig


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = {
    "Intf_type": 1,
    "RS_module": 2,
    "RS_inst": 3,
    "position": 4,
    "step": 5,
    "clk": 6,
    "rst": 7,
    "CRG_source": 8,
}


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        from rscheck.model import ExcelConfig

        self.specs = read_spec_rows(
            ROOT / "tests" / "fixtures" / "specs.csv",
            ExcelConfig(sheet=1, columns=COLUMNS),
        )
        self.inventory = load_inventory(ROOT / "tests" / "fixtures" / "inventory.json")
        self.rtl = RtlConfig()

    def _mutated_inventory(self, mutate) -> object:
        raw = json.loads((ROOT / "tests" / "fixtures" / "inventory.json").read_text("utf-8"))
        mutate(raw)
        temp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(raw, temp)
            temp.close()
            return load_inventory(temp.name)
        finally:
            Path(temp.name).unlink(missing_ok=True)

    def test_passing_inventory(self) -> None:
        report = check_specs(self.specs, self.inventory, self.rtl)
        self.assertTrue(report.passed)
        self.assertEqual(report.error_count, 0)

    def test_step_module_and_clock_mismatches_are_all_reported(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1]["module"] = "wrong_pipe"
            instances[1]["ports"]["clk"]["connection"] = "top.u_tile.clk_aux"
            instances.append(
                {
                    **instances[0],
                    "name": "AAAA_BBB_C2",
                    "full_name": "top.u_tile.AAAA_BBB_C2",
                }
            )

        report = check_specs(self.specs, self._mutated_inventory(mutate), self.rtl)
        codes = {finding.code for finding in report.rows[0].findings}
        self.assertIn("STEP_MISMATCH", codes)
        self.assertIn("RS_MODULE_MISMATCH", codes)
        self.assertIn("CLK_CONNECTION_MISMATCH", codes)

    def test_mixed_suffix_tags_are_warning_only(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][1]["name"] = "AAAA_BBB_D1"
            raw["positions"]["top.u_tile"]["instances"][1]["full_name"] = (
                "top.u_tile.AAAA_BBB_D1"
            )

        report = check_specs(self.specs, self._mutated_inventory(mutate), self.rtl)
        self.assertTrue(report.passed)
        self.assertIn("SUFFIX_TAG_MISMATCH", {item.code for item in report.rows[0].findings})

    def test_multiple_clock_sources_fail_closed(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][0]["clk_sources"].append(
                {"instance": "top.u_tile.u_other", "module": "other_crg"}
            )

        report = check_specs(self.specs, self._mutated_inventory(mutate), self.rtl)
        self.assertFalse(report.passed)
        self.assertIn(
            "MULTIPLE_CLK_SOURCES", {item.code for item in report.rows[0].findings}
        )

    def test_overlapping_prefixes_are_ambiguous(self) -> None:
        broad = replace(self.specs[0], row_number=99, rs_inst="AAAA", step=2)
        report = check_specs([self.specs[0], broad], self.inventory, self.rtl)
        self.assertFalse(report.passed)
        self.assertEqual(
            sum(item.code == "AMBIGUOUS_GROUP_MATCH" for item in report.global_findings),
            2,
        )

    def test_contiguous_indices_can_be_enabled(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][1]["name"] = "AAAA_BBB_C3"
            raw["positions"]["top.u_tile"]["instances"][1]["full_name"] = (
                "top.u_tile.AAAA_BBB_C3"
            )

        report = check_specs(
            self.specs,
            self._mutated_inventory(mutate),
            replace(self.rtl, require_contiguous_indices=True),
        )
        self.assertIn("STAGE_INDEX_MISMATCH", {item.code for item in report.rows[0].findings})

    def test_missing_group_has_specific_diagnostic(self) -> None:
        missing = replace(self.specs[0], rs_inst="DOES_NOT_EXIST")
        report = check_specs([missing], self.inventory, self.rtl)
        self.assertEqual({item.code for item in report.rows[0].findings}, {"GROUP_NOT_FOUND"})

    def test_missing_position_fails(self) -> None:
        missing = replace(self.specs[0], position="top.no_such_scope")
        report = check_specs([missing], self.inventory, self.rtl)
        self.assertEqual(
            {item.code for item in report.rows[0].findings}, {"POSITION_NOT_FOUND"}
        )

    def test_unconnected_port_and_unsupported_expression_fail(self) -> None:
        def mutate(raw) -> None:
            instance = raw["positions"]["top.u_tile"]["instances"][0]
            instance["ports"]["clk"] = {
                "connection": "",
                "type": "",
            }
            instance["ports"]["rst"] = {
                "connection": "top.u_tile.rst_n & top.u_tile.enable",
                "type": "npiOperation",
            }

        report = check_specs([self.specs[0]], self._mutated_inventory(mutate), self.rtl)
        codes = {item.code for item in report.rows[0].findings}
        self.assertIn("CLK_UNCONNECTED", codes)
        self.assertIn("UNSUPPORTED_CONNECTION", codes)

    def test_crg_source_mismatch_fails(self) -> None:
        wrong = replace(self.specs[0], crg_source="wrong_crg")
        report = check_specs([wrong], self.inventory, self.rtl)
        self.assertFalse(report.passed)
        self.assertIn(
            "CRG_SOURCE_MISMATCH", {item.code for item in report.rows[0].findings}
        )

    def test_collector_warning_fails_closed(self) -> None:
        warned = replace(
            self.inventory,
            warnings=("clock driver traversal exceeded the depth limit",),
        )
        report = check_specs(self.specs, warned, self.rtl)
        self.assertFalse(report.passed)
        self.assertEqual(report.global_findings[0].code, "NPI_UNRESOLVED")

    def test_optional_or_non_numeric_index_group_is_controlled_config_error(self) -> None:
        bad = replace(self.rtl, suffix_regex=r".+(?P<index>[A-Z]+)?")
        with self.assertRaises(ConfigError):
            check_specs(self.specs, self.inventory, bad)

    def test_duplicate_instance_full_name_is_rejected(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1] = dict(instances[0])

        with self.assertRaisesRegex(InventoryError, "duplicate instance full_name"):
            self._mutated_inventory(mutate)

    def test_instance_name_must_match_full_name_leaf(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][1]["name"] = "AAAA_BBB_C0"

        with self.assertRaisesRegex(InventoryError, "inconsistent name"):
            self._mutated_inventory(mutate)


if __name__ == "__main__":
    unittest.main()
