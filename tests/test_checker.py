from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs, parameter_value_state
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.inventory import inventory_to_dict, load_inventory
from rscheck.model import ConfigError, InventoryError, ModuleRule, RtlConfig


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
    "RS_CFG_EN": 9,
}


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        from rscheck.model import ExcelConfig

        self.specs = read_spec_rows(
            ROOT / "tests" / "fixtures" / "specs.csv",
            ExcelConfig(sheet=1, columns=COLUMNS),
        )
        self.inventory = load_inventory(ROOT / "tests" / "fixtures" / "inventory.json")
        config = load_config(ROOT / "config" / "rscheck.example.json")
        self.rtl = config.rtl
        self.rules = config.module_rules

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
        report = check_specs(self.specs, self.inventory, self.rtl, self.rules)
        self.assertTrue(report.passed)
        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.rows[0].effective_step, 5)
        self.assertEqual(len(report.rows[0].instances), 6)
        self.assertEqual(
            [item.contribution for item in report.rows[0].step_evaluations],
            [1, 1, 0, 1, 1, 1],
        )

    def test_dynamic_step_mismatch_uses_effective_count(self) -> None:
        spec = replace(self.specs[0], step=6)
        report = check_specs([spec], self.inventory, self.rtl, self.rules)
        self.assertFalse(report.passed)
        mismatch = next(
            item for item in report.rows[0].findings if item.code == "STEP_MISMATCH"
        )
        self.assertEqual(mismatch.actual["physical_instances"], 6)
        self.assertEqual(mismatch.actual["effective_step"], 5)

    def test_all_disabled_instances_allow_zero_step(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"]["rs_mode"] = "0"

        spec = replace(self.specs[0], step=0)
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, self.rules
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 0)

    def test_missing_or_unresolved_step_parameter_fails_closed(self) -> None:
        cases = (
            ("missing", object(), "STEP_PARAMETER_MISSING"),
            ("null", None, "STEP_PARAMETER_VALUE_UNRESOLVED"),
            ("x", "x", "STEP_PARAMETER_VALUE_UNRESOLVED"),
            ("z", "Z", "STEP_PARAMETER_VALUE_UNRESOLVED"),
            ("invalid", "mode_expr", "STEP_PARAMETER_VALUE_UNRESOLVED"),
        )
        for name, value, expected_code in cases:
            with self.subTest(name=name):
                def mutate(raw) -> None:
                    parameters = raw["positions"]["top.u_tile"]["instances"][0][
                        "parameters"
                    ]
                    if name == "missing":
                        parameters.pop("rs_mode")
                    else:
                        parameters["rs_mode"] = value

                report = check_specs(
                    [self.specs[0]],
                    self._mutated_inventory(mutate),
                    self.rtl,
                    self.rules,
                )
                codes = {item.code for item in report.rows[0].findings}
                self.assertIn(expected_code, codes)
                self.assertIn("STEP_CALCULATION_UNRESOLVED", codes)
                self.assertNotIn("STEP_MISMATCH", codes)
                self.assertIsNone(report.rows[0].effective_step)

    def test_parameter_value_state_handles_verilog_and_wide_npi_values(self) -> None:
        cases = {
            None: "unresolved",
            "": "unresolved",
            "'0": "zero",
            "'1": "nonzero",
            "32'b0000_0000": "zero",
            "4'b0010": "nonzero",
            "8'o00": "zero",
            "8'h0f": "nonzero",
            "4'b00x0": "unresolved",
            "2'b02": "unresolved",
            "mode_expr": "unresolved",
            "0" * 10_000: "zero",
            "0" * 9_999 + "1": "nonzero",
        }
        for value, expected in cases.items():
            with self.subTest(value=(value[:32] if isinstance(value, str) else value)):
                self.assertEqual(parameter_value_state(value), expected)

    def test_multiple_step_parameters_use_all_nonzero_semantics(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"]["pipe_enable"] = "1"
            raw["positions"]["top.u_tile"]["instances"][0]["parameters"][
                "pipe_enable"
            ] = "0"

        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=True,
            step_parameters=("rs_mode", "pipe_enable"),
        )
        spec = replace(self.specs[0], step=4)
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, {"rs_pipe": rule}
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 4)

    def test_registered_module_without_step_parameters_counts_every_instance(self) -> None:
        rule = ModuleRule(name="rs_pipe", has_rs_cfg_en=True, step_parameters=())
        spec = replace(self.specs[0], step=6)
        report = check_specs([spec], self.inventory, self.rtl, {"rs_pipe": rule})
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 6)

    def test_unregistered_rs_module_is_a_row_error(self) -> None:
        report = check_specs([self.specs[0]], self.inventory, self.rtl, {})
        self.assertFalse(report.passed)
        self.assertIn(
            "RS_MODULE_RULE_NOT_FOUND",
            {item.code for item in report.rows[0].findings},
        )
        self.assertIsNone(report.rows[0].effective_step)

    def test_rs_cfg_en_truth_matrix(self) -> None:
        missing = object()
        cases = (
            ("zero_false_gate", "0", "假门控", True, set()),
            ("wide_zero_false_gate", "000", "假门控", True, set()),
            (
                "invalid_internal_whitespace",
                "0 0",
                "假门控",
                False,
                {"RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
            ("negative_zero_false_gate", "-0", "假门控", True, set()),
            (
                "missing_blank",
                missing,
                "",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_PARAMETER_MISSING"},
            ),
            (
                "missing_false_gate",
                missing,
                "假门控",
                False,
                {"RS_CFG_EN_PARAMETER_MISSING"},
            ),
            (
                "missing_other_label",
                missing,
                "真门控",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_PARAMETER_MISSING"},
            ),
            (
                "zero_blank",
                "0",
                "",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH"},
            ),
            (
                "zero_other_label",
                "0",
                "真门控",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH"},
            ),
            (
                "one_false_gate",
                "1",
                "假门控",
                False,
                {"RS_CFG_EN_VALUE_MISMATCH"},
            ),
            (
                "one_blank",
                "1",
                "",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_VALUE_MISMATCH"},
            ),
            (
                "one_other_label",
                "1",
                "真门控",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_VALUE_MISMATCH"},
            ),
            (
                "x_false_gate",
                "x",
                "假门控",
                False,
                {"RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
            (
                "z_false_gate",
                "Z",
                "假门控",
                False,
                {"RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
            (
                "unresolved_false_gate",
                None,
                "假门控",
                False,
                {"RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
            (
                "unresolved_blank",
                None,
                "",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
            (
                "unresolved_other_label",
                None,
                "真门控",
                False,
                {"RS_CFG_EN_LABEL_MISMATCH", "RS_CFG_EN_VALUE_UNRESOLVED"},
            ),
        )
        for name, parameter_value, excel_value, passed, expected_codes in cases:
            with self.subTest(name=name):
                def mutate(raw) -> None:
                    instances = raw["positions"]["top.u_tile"]["instances"][:2]
                    for instance in instances:
                        if parameter_value is missing:
                            instance["parameters"].pop("RS_CFG_EN", None)
                        else:
                            instance["parameters"]["RS_CFG_EN"] = parameter_value

                spec = replace(self.specs[0], rs_cfg_en=excel_value)
                report = check_specs(
                    [spec], self._mutated_inventory(mutate), self.rtl, self.rules
                )
                self.assertEqual(report.passed, passed)
                codes = {item.code for item in report.rows[0].findings}
                self.assertEqual(codes, expected_codes)

    def test_rs_cfg_en_checks_every_instance_in_group(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1]["parameters"]["RS_CFG_EN"] = "1"

        report = check_specs(
            [self.specs[0]], self._mutated_inventory(mutate), self.rtl, self.rules
        )
        self.assertFalse(report.passed)
        mismatches = [
            item
            for item in report.rows[0].findings
            if item.code == "RS_CFG_EN_VALUE_MISMATCH"
        ]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].instance, "top.u_tile.AAAA_BBB_C1")

    def test_unrelated_parameters_do_not_change_no_parameter_semantics(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"].pop("RS_CFG_EN")
                    instance["parameters"]["SOME_OTHER_PARAMETER"] = None

        spec = replace(self.specs[0], rs_cfg_en="")
        rule = ModuleRule(
            name="rs_pipe", has_rs_cfg_en=False, step_parameters=("rs_mode",)
        )
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, {"rs_pipe": rule}
        )
        self.assertTrue(report.passed)

    def test_rs_cfg_en_database_presence_mismatch_fails(self) -> None:
        spec = replace(self.specs[0], rs_cfg_en="")
        rule = ModuleRule(
            name="rs_pipe", has_rs_cfg_en=False, step_parameters=("rs_mode",)
        )
        report = check_specs([spec], self.inventory, self.rtl, {"rs_pipe": rule})
        self.assertFalse(report.passed)
        self.assertEqual(
            sum(
                item.code == "RS_CFG_EN_PARAMETER_UNEXPECTED"
                for item in report.rows[0].findings
            ),
            6,
        )

    def test_inventory_parameters_round_trip(self) -> None:
        raw = inventory_to_dict(self.inventory)
        instance = raw["positions"]["top.u_tile"]["instances"][0]
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(instance["parameters"]["RS_CFG_EN"], "0")
        self.assertEqual(instance["parameters"]["WIDTH"], "1")

    def test_old_inventory_schema_is_rejected(self) -> None:
        for schema_version in (1, 2.0, True, "2"):
            with self.subTest(schema_version=schema_version):
                def mutate(raw) -> None:
                    raw["schema_version"] = schema_version

                with self.assertRaisesRegex(InventoryError, "schema_version.*expected 2"):
                    self._mutated_inventory(mutate)

    def test_missing_inventory_warnings_is_rejected(self) -> None:
        def mutate(raw) -> None:
            del raw["warnings"]

        with self.assertRaisesRegex(InventoryError, "warnings.*required"):
            self._mutated_inventory(mutate)

    def test_missing_or_malformed_parameter_inventory_is_rejected(self) -> None:
        mutations = (
            ("missing", lambda instance: instance.pop("parameters")),
            ("array", lambda instance: instance.__setitem__("parameters", [])),
            (
                "numeric_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CFG_EN": 0}),
            ),
            (
                "boolean_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CFG_EN": False}),
            ),
            (
                "object_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CFG_EN": {}}),
            ),
            (
                "empty_name",
                lambda instance: instance.__setitem__("parameters", {"": "0"}),
            ),
        )
        for name, mutation in mutations:
            with self.subTest(name=name):
                def mutate(raw) -> None:
                    mutation(raw["positions"]["top.u_tile"]["instances"][0])

                with self.assertRaisesRegex(InventoryError, "parameters|parameter"):
                    self._mutated_inventory(mutate)

    def test_module_and_clock_mismatches_make_step_unresolved(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1]["module"] = "wrong_pipe"
            instances[1]["ports"]["clk"]["connection"] = "top.u_tile.clk_aux"

        report = check_specs(
            self.specs, self._mutated_inventory(mutate), self.rtl, self.rules
        )
        codes = {finding.code for finding in report.rows[0].findings}
        self.assertIn("STEP_CALCULATION_UNRESOLVED", codes)
        self.assertIn("RS_MODULE_MISMATCH", codes)
        self.assertIn("CLK_CONNECTION_MISMATCH", codes)

    def test_mixed_suffix_tags_are_warning_only(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][1]["name"] = "AAAA_BBB_D1"
            raw["positions"]["top.u_tile"]["instances"][1]["full_name"] = (
                "top.u_tile.AAAA_BBB_D1"
            )

        report = check_specs(
            self.specs, self._mutated_inventory(mutate), self.rtl, self.rules
        )
        self.assertTrue(report.passed)
        self.assertIn("SUFFIX_TAG_MISMATCH", {item.code for item in report.rows[0].findings})

    def test_multiple_clock_sources_fail_closed(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][0]["clk_sources"].append(
                {"instance": "top.u_tile.u_other", "module": "other_crg"}
            )

        report = check_specs(
            self.specs, self._mutated_inventory(mutate), self.rtl, self.rules
        )
        self.assertFalse(report.passed)
        self.assertIn(
            "MULTIPLE_CLK_SOURCES", {item.code for item in report.rows[0].findings}
        )

    def test_overlapping_prefixes_are_ambiguous(self) -> None:
        broad = replace(self.specs[0], row_number=99, rs_inst="AAAA", step=2)
        report = check_specs(
            [self.specs[0], broad], self.inventory, self.rtl, self.rules
        )
        self.assertFalse(report.passed)
        self.assertEqual(
            sum(item.code == "AMBIGUOUS_GROUP_MATCH" for item in report.global_findings),
            6,
        )

    def test_contiguous_indices_can_be_enabled(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][1]["name"] = "AAAA_BBB_C6"
            raw["positions"]["top.u_tile"]["instances"][1]["full_name"] = (
                "top.u_tile.AAAA_BBB_C6"
            )

        report = check_specs(
            self.specs,
            self._mutated_inventory(mutate),
            replace(self.rtl, require_contiguous_indices=True),
            self.rules,
        )
        self.assertIn("STAGE_INDEX_MISMATCH", {item.code for item in report.rows[0].findings})

    def test_missing_group_has_specific_diagnostic(self) -> None:
        missing = replace(self.specs[0], rs_inst="DOES_NOT_EXIST", step=0)
        report = check_specs([missing], self.inventory, self.rtl, self.rules)
        self.assertEqual({item.code for item in report.rows[0].findings}, {"GROUP_NOT_FOUND"})

    def test_missing_position_fails(self) -> None:
        missing = replace(self.specs[0], position="top.no_such_scope")
        report = check_specs([missing], self.inventory, self.rtl, self.rules)
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

        report = check_specs(
            [self.specs[0]], self._mutated_inventory(mutate), self.rtl, self.rules
        )
        codes = {item.code for item in report.rows[0].findings}
        self.assertIn("CLK_UNCONNECTED", codes)
        self.assertIn("UNSUPPORTED_CONNECTION", codes)

    def test_crg_source_mismatch_fails(self) -> None:
        wrong = replace(self.specs[0], crg_source="wrong_crg")
        report = check_specs([wrong], self.inventory, self.rtl, self.rules)
        self.assertFalse(report.passed)
        self.assertIn(
            "CRG_SOURCE_MISMATCH", {item.code for item in report.rows[0].findings}
        )

    def test_collector_warning_fails_closed(self) -> None:
        warned = replace(
            self.inventory,
            warnings=("clock driver traversal exceeded the depth limit",),
        )
        report = check_specs(self.specs, warned, self.rtl, self.rules)
        self.assertFalse(report.passed)
        self.assertEqual(report.global_findings[0].code, "NPI_UNRESOLVED")

    def test_optional_or_non_numeric_index_group_is_controlled_config_error(self) -> None:
        bad = replace(self.rtl, suffix_regex=r".+(?P<index>[A-Z]+)?")
        with self.assertRaises(ConfigError):
            check_specs(self.specs, self.inventory, bad, self.rules)

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
