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
from rscheck.reporting import report_to_dict


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

    def _default_rule_inventory(self, mutate=lambda raw: None) -> object:
        def use_default_ports(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                ports = instance["ports"]
                if "rst" in ports:
                    ports["rst_n"] = ports.pop("rst")
            mutate(raw)

        return self._mutated_inventory(use_default_ports)

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
            rst_port="rst",
        )
        spec = replace(self.specs[0], step=4)
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, {"rs_pipe": rule}
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 4)

    def test_rs_cfg_en_can_be_an_ordinary_step_parameter(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            for instance in instances:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"]["RS_CFG_EN"] = "1"
            instances[2]["parameters"]["RS_CFG_EN"] = "0"

        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=True,
            step_parameters=("RS_CFG_EN",),
            rst_port="rst",
        )
        report = check_specs(
            [self.specs[0]],
            self._mutated_inventory(mutate),
            self.rtl,
            {"rs_pipe": rule},
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 5)
        self.assertTrue(
            all(
                "RS_CRG_EN" in instance.parameters
                for instance in report.rows[0].instances
            )
        )

    def test_registered_module_without_step_parameters_counts_every_instance(self) -> None:
        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=True,
            step_parameters=(),
            rst_port="rst",
        )
        spec = replace(self.specs[0], step=6)
        report = check_specs([spec], self.inventory, self.rtl, {"rs_pipe": rule})
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 6)

    def test_unregistered_rs_module_uses_default_rule(self) -> None:
        spec = replace(self.specs[0], step=6)
        report = check_specs([spec], self._default_rule_inventory(), self.rtl, {})

        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].findings, ())
        self.assertEqual(
            report.rows[0].module_rule,
            ModuleRule(
                name="rs_pipe",
                has_rs_cfg_en=True,
                step_parameters=(),
            ),
        )
        self.assertEqual(report.rows[0].effective_step, 6)
        self.assertEqual(report.rows[0].module_rule.clk_port, "clk")
        self.assertEqual(report.rows[0].module_rule.rst_port, "rst_n")
        self.assertEqual(
            [item.contribution for item in report.rows[0].step_evaluations],
            [1, 1, 1, 1, 1, 1],
        )

    def test_default_rule_reports_step_mismatch_for_six_physical_instances(self) -> None:
        report = check_specs(
            [self.specs[0]], self._default_rule_inventory(), self.rtl, {}
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"STEP_MISMATCH"},
        )
        mismatch = report.rows[0].findings[0]
        self.assertEqual(mismatch.expected, 5)
        self.assertEqual(mismatch.actual["physical_instances"], 6)
        self.assertEqual(mismatch.actual["effective_step"], 6)

    def test_default_rule_requires_rs_crg_en_on_every_instance(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][0]["parameters"].pop(
                "RS_CRG_EN"
            )

        spec = replace(self.specs[0], step=6)
        report = check_specs(
            [spec], self._default_rule_inventory(mutate), self.rtl, {}
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"RS_CFG_EN_PARAMETER_MISSING"},
        )
        missing = report.rows[0].findings[0]
        self.assertEqual(missing.instance, "top.u_tile.AAAA_BBB_C0")

    def test_legacy_rtl_rs_cfg_en_does_not_alias_rs_crg_en(self) -> None:
        def mutate(raw) -> None:
            parameters = raw["positions"]["top.u_tile"]["instances"][0][
                "parameters"
            ]
            parameters["RS_CFG_EN"] = parameters.pop("RS_CRG_EN")

        report = check_specs(
            [self.specs[0]], self._mutated_inventory(mutate), self.rtl, self.rules
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"RS_CFG_EN_PARAMETER_MISSING"},
        )
        missing = report.rows[0].findings[0]
        self.assertEqual(missing.instance, "top.u_tile.AAAA_BBB_C0")

    def test_default_rule_rejects_nonzero_rs_crg_en(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][0]["parameters"][
                "RS_CRG_EN"
            ] = "1"

        spec = replace(self.specs[0], step=6)
        report = check_specs(
            [spec], self._default_rule_inventory(mutate), self.rtl, {}
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"RS_CFG_EN_VALUE_MISMATCH"},
        )
        mismatch = report.rows[0].findings[0]
        self.assertEqual(mismatch.instance, "top.u_tile.AAAA_BBB_C0")
        self.assertEqual(mismatch.actual, "1")

    def test_default_rule_requires_fake_gating_excel_label(self) -> None:
        spec = replace(self.specs[0], step=6, rs_cfg_en="")
        report = check_specs([spec], self._default_rule_inventory(), self.rtl, {})

        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"RS_CFG_EN_LABEL_MISMATCH"},
        )
        mismatch = report.rows[0].findings[0]
        self.assertEqual(mismatch.expected, "假门控")
        self.assertEqual(mismatch.actual, "")

    def test_explicit_module_rule_takes_priority_over_default(self) -> None:
        report = check_specs(
            [self.specs[0]], self.inventory, self.rtl, self.rules
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].module_rule, self.rules["rs_pipe"])
        self.assertEqual(report.rows[0].effective_step, 5)

    def test_rs_crg_en_truth_matrix(self) -> None:
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
                            instance["parameters"].pop("RS_CRG_EN", None)
                        else:
                            instance["parameters"]["RS_CRG_EN"] = parameter_value

                spec = replace(self.specs[0], rs_cfg_en=excel_value)
                report = check_specs(
                    [spec], self._mutated_inventory(mutate), self.rtl, self.rules
                )
                self.assertEqual(report.passed, passed)
                codes = {item.code for item in report.rows[0].findings}
                self.assertEqual(codes, expected_codes)

    def test_na_skips_all_rs_crg_en_checks_for_module_with_parameter(self) -> None:
        missing = object()
        for name, parameter_value in (
            ("zero", "0"),
            ("nonzero", "1"),
            ("unresolved", None),
            ("missing", missing),
        ):
            with self.subTest(name=name):
                def mutate(raw) -> None:
                    for instance in raw["positions"]["top.u_tile"]["instances"]:
                        if not instance["name"].startswith("AAAA_BBB"):
                            continue
                        if parameter_value is missing:
                            instance["parameters"].pop("RS_CRG_EN", None)
                        else:
                            instance["parameters"]["RS_CRG_EN"] = parameter_value

                spec = replace(self.specs[0], rs_cfg_en="NA")
                report = check_specs(
                    [spec], self._mutated_inventory(mutate), self.rtl, self.rules
                )

                self.assertTrue(report.passed)
                self.assertEqual(report.rows[0].findings, ())
                self.assertEqual(
                    report_to_dict(report)["rows"][0]["spec"]["RS_CFG_EN"],
                    "NA",
                )

    def test_na_skips_rs_crg_en_presence_check_for_module_without_parameter(self) -> None:
        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=False,
            step_parameters=("rs_mode",),
            rst_port="rst",
        )
        for parameter_exists in (True, False):
            with self.subTest(parameter_exists=parameter_exists):
                def mutate(raw) -> None:
                    if parameter_exists:
                        return
                    for instance in raw["positions"]["top.u_tile"]["instances"]:
                        if instance["name"].startswith("AAAA_BBB"):
                            instance["parameters"].pop("RS_CRG_EN", None)

                spec = replace(self.specs[0], rs_cfg_en="NA")
                report = check_specs(
                    [spec],
                    self._mutated_inventory(mutate),
                    self.rtl,
                    {"rs_pipe": rule},
                )

                self.assertTrue(report.passed)
                self.assertEqual(report.rows[0].findings, ())

    def test_rs_cfg_en_na_marker_is_case_sensitive(self) -> None:
        for excel_value in ("na", "N/A"):
            with self.subTest(excel_value=excel_value):
                spec = replace(self.specs[0], rs_cfg_en=excel_value)
                report = check_specs(
                    [spec], self.inventory, self.rtl, self.rules
                )

                self.assertFalse(report.passed)
                self.assertEqual(
                    {item.code for item in report.rows[0].findings},
                    {"RS_CFG_EN_LABEL_MISMATCH"},
                )

    def test_na_only_skips_rs_crg_en_checks(self) -> None:
        def mutate(raw) -> None:
            instance = raw["positions"]["top.u_tile"]["instances"][0]
            instance["parameters"]["RS_CRG_EN"] = "1"
            instance["ports"]["clk"]["connection"] = "top.u_tile.wrong_clk"
            instance["ports"]["rst"]["connection"] = "top.u_tile.wrong_rst"

        spec = replace(self.specs[0], rs_cfg_en="NA", step=6)
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, self.rules
        )

        self.assertFalse(report.passed)
        self.assertEqual(len(report.rows[0].instances), 6)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"STEP_MISMATCH", "CLK_CONNECTION_MISMATCH", "RST_CONNECTION_MISMATCH"},
        )
        self.assertFalse(
            any(item.code.startswith("RS_CFG_EN_") for item in report.rows[0].findings)
        )

    def test_rs_crg_en_checks_every_instance_in_group(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1]["parameters"]["RS_CRG_EN"] = "1"

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
                    instance["parameters"].pop("RS_CRG_EN")
                    instance["parameters"]["SOME_OTHER_PARAMETER"] = None

        spec = replace(self.specs[0], rs_cfg_en="")
        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=False,
            step_parameters=("rs_mode",),
            rst_port="rst",
        )
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, {"rs_pipe": rule}
        )
        self.assertTrue(report.passed)

    def test_module_without_rs_crg_en_ignores_excel_rs_cfg_en_value(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"].pop("RS_CRG_EN")

        inventory = self._mutated_inventory(mutate)
        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=False,
            step_parameters=("rs_mode",),
            rst_port="rst",
        )
        for excel_value in ("", "假门控", "真门控", "任意文本", "0"):
            with self.subTest(excel_value=excel_value):
                spec = replace(self.specs[0], rs_cfg_en=excel_value)
                report = check_specs(
                    [spec], inventory, self.rtl, {"rs_pipe": rule}
                )

                self.assertTrue(report.passed)
                self.assertEqual(report.rows[0].findings, ())
                self.assertEqual(
                    report_to_dict(report)["rows"][0]["spec"]["RS_CFG_EN"],
                    excel_value,
                )

    def test_rs_crg_en_database_presence_mismatch_fails(self) -> None:
        spec = replace(self.specs[0], rs_cfg_en="任意非空值")
        rule = ModuleRule(
            name="rs_pipe",
            has_rs_cfg_en=False,
            step_parameters=("rs_mode",),
            rst_port="rst",
        )
        report = check_specs([spec], self.inventory, self.rtl, {"rs_pipe": rule})
        self.assertFalse(report.passed)
        self.assertEqual(
            {item.code for item in report.rows[0].findings},
            {"RS_CFG_EN_PARAMETER_UNEXPECTED"},
        )
        self.assertEqual(
            sum(
                item.code == "RS_CFG_EN_PARAMETER_UNEXPECTED"
                for item in report.rows[0].findings
            ),
            6,
        )
        self.assertEqual(
            report_to_dict(report)["rows"][0]["spec"]["RS_CFG_EN"],
            "任意非空值",
        )

    def test_inventory_parameters_round_trip(self) -> None:
        raw = inventory_to_dict(
            replace(self.inventory, notices=("partial load remains queryable",))
        )
        instance = raw["positions"]["top.u_tile"]["instances"][0]
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(raw["notices"], ["partial load remains queryable"])
        self.assertEqual(instance["parameters"]["RS_CRG_EN"], "0")
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

    def test_inventory_notices_are_optional_but_must_be_an_array(self) -> None:
        def omit(raw) -> None:
            raw.pop("notices", None)

        self.assertEqual(self._mutated_inventory(omit).notices, ())

        def malformed(raw) -> None:
            raw["notices"] = "not-an-array"

        with self.assertRaisesRegex(InventoryError, "notices.*array"):
            self._mutated_inventory(malformed)

    def test_missing_or_malformed_parameter_inventory_is_rejected(self) -> None:
        mutations = (
            ("missing", lambda instance: instance.pop("parameters")),
            ("array", lambda instance: instance.__setitem__("parameters", [])),
            (
                "numeric_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CRG_EN": 0}),
            ),
            (
                "boolean_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CRG_EN": False}),
            ),
            (
                "object_value",
                lambda instance: instance.__setitem__("parameters", {"RS_CRG_EN": {}}),
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

    def test_module_mismatch_makes_step_unresolved_without_port_noise(self) -> None:
        def mutate(raw) -> None:
            instances = raw["positions"]["top.u_tile"]["instances"]
            instances[1]["module"] = "wrong_pipe"
            instances[1]["ports"] = {}

        report = check_specs(
            self.specs, self._mutated_inventory(mutate), self.rtl, self.rules
        )
        codes = {finding.code for finding in report.rows[0].findings}
        self.assertEqual(
            codes,
            {"RS_MODULE_MISMATCH", "STEP_CALCULATION_UNRESOLVED"},
        )

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

    def test_full_instance_name_matches_with_empty_suffix(self) -> None:
        spec = replace(self.specs[1], rs_inst="CTRL_RS_D0")
        report = check_specs([spec], self.inventory, self.rtl, self.rules)

        self.assertTrue(report.passed)
        self.assertEqual(
            [instance.name for instance in report.rows[0].instances],
            ["CTRL_RS_D0"],
        )
        self.assertEqual(report.rows[0].effective_step, 1)
        self.assertNotIn(
            "INSTANCE_SUFFIX_INVALID",
            {finding.code for finding in report.rows[0].findings},
        )

    def test_full_instance_name_needs_no_numeric_suffix(self) -> None:
        def mutate(raw) -> None:
            instance = raw["positions"]["top.u_tile"]["instances"][6]
            instance["name"] = "CONTROL_SINGLE"
            instance["full_name"] = "top.u_tile.CONTROL_SINGLE"

        spec = replace(self.specs[1], rs_inst="CONTROL_SINGLE")
        report = check_specs(
            [spec], self._mutated_inventory(mutate), self.rtl, self.rules
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].instances[0].name, "CONTROL_SINGLE")

    def test_empty_suffix_instance_obeys_step_parameter(self) -> None:
        spec = replace(self.specs[0], rs_inst="AAAA_BBB_C2", step=0)
        report = check_specs([spec], self.inventory, self.rtl, self.rules)

        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].effective_step, 0)
        self.assertEqual(
            [item.contribution for item in report.rows[0].step_evaluations],
            [0],
        )

    def test_empty_and_indexed_suffix_instances_share_prefix_group(self) -> None:
        def mutate(raw) -> None:
            instance = dict(raw["positions"]["top.u_tile"]["instances"][0])
            instance["name"] = "AAAA_BBB"
            instance["full_name"] = "top.u_tile.AAAA_BBB"
            raw["positions"]["top.u_tile"]["instances"].append(instance)

        spec = replace(self.specs[0], step=6)
        report = check_specs(
            [spec],
            self._mutated_inventory(mutate),
            replace(self.rtl, require_contiguous_indices=True),
            self.rules,
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.rows[0].instances), 7)
        self.assertEqual(report.rows[0].instances[0].name, "AAAA_BBB")
        self.assertEqual(report.rows[0].effective_step, 6)

    def test_full_name_and_broad_prefix_overlap_is_ambiguous(self) -> None:
        exact = replace(
            self.specs[0], row_number=98, rs_inst="AAAA_BBB_C0", step=1
        )
        broad = replace(self.specs[0], row_number=99, rs_inst="AAAA", step=5)
        report = check_specs([exact, broad], self.inventory, self.rtl, self.rules)

        ambiguous = [
            finding
            for finding in report.global_findings
            if finding.code == "AMBIGUOUS_GROUP_MATCH"
        ]
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0].instance, "top.u_tile.AAAA_BBB_C0")

    def test_multiple_clock_sources_are_retained_without_validation(self) -> None:
        def mutate(raw) -> None:
            raw["positions"]["top.u_tile"]["instances"][0]["clk_sources"].append(
                {"instance": "top.u_tile.u_other", "module": "other_crg"}
            )

        report = check_specs(
            self.specs, self._mutated_inventory(mutate), self.rtl, self.rules
        )
        self.assertTrue(report.passed)
        self.assertEqual(len(report.rows[0].instances[0].clk_sources), 2)
        self.assertNotIn(
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

    def test_module_rule_selects_custom_clk_and_rst_formal_names(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if not instance["name"].startswith("AAAA_BBB"):
                    continue
                ports = instance["ports"]
                ports["pipe_clock"] = ports.pop("clk")
                ports["pipe_reset_n"] = ports.pop("rst")

        rule = replace(
            self.rules["rs_pipe"],
            clk_port="pipe_clock",
            rst_port="pipe_reset_n",
        )
        report = check_specs(
            [self.specs[0]],
            self._mutated_inventory(mutate),
            replace(self.rtl, clk_port="wrong_clk", rst_port="wrong_rst"),
            {"rs_pipe": rule},
        )

        self.assertTrue(report.passed)

    def test_missing_custom_formal_name_reports_the_configured_name(self) -> None:
        rule = replace(self.rules["rs_pipe"], clk_port="pipe_clock")
        report = check_specs(
            [self.specs[0]], self.inventory, self.rtl, {"rs_pipe": rule}
        )

        missing = [
            finding
            for finding in report.rows[0].findings
            if finding.code == "CLK_PORT_MISSING"
        ]
        self.assertEqual(len(missing), 6)
        self.assertTrue(all("'pipe_clock'" in finding.message for finding in missing))

    def test_present_clk_and_missing_rst_reports_only_rst_missing(self) -> None:
        def mutate(raw) -> None:
            instance = next(
                item
                for item in raw["positions"]["top.u_tile"]["instances"]
                if item["name"] == "CTRL_RS_D0"
            )
            instance["ports"].pop("rst")

        report = check_specs(
            [self.specs[1]], self._mutated_inventory(mutate), self.rtl, self.rules
        )

        findings = report.rows[0].findings
        self.assertFalse(report.passed)
        self.assertEqual([finding.code for finding in findings], ["RST_PORT_MISSING"])
        self.assertEqual(findings[0].instance, "top.u_tile.CTRL_RS_D0")
        self.assertNotIn("CLK_PORT_MISSING", {finding.code for finding in findings})
        self.assertNotIn("CLK_UNCONNECTED", {finding.code for finding in findings})

    def test_crg_source_mismatch_is_not_judged(self) -> None:
        wrong = replace(self.specs[0], crg_source="wrong_crg")
        report = check_specs([wrong], self.inventory, self.rtl, self.rules)
        self.assertTrue(report.passed)
        self.assertEqual(report.rows[0].spec.crg_source, "wrong_crg")
        self.assertNotIn(
            "CRG_SOURCE_MISMATCH", {item.code for item in report.rows[0].findings}
        )

    def test_unresolved_crg_source_is_not_judged(self) -> None:
        def mutate(raw) -> None:
            for instance in raw["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["clk_sources"] = []

        report = check_specs(
            [self.specs[0]], self._mutated_inventory(mutate), self.rtl, self.rules
        )

        self.assertTrue(report.passed)
        self.assertTrue(
            all(not instance.clk_sources for instance in report.rows[0].instances)
        )
        self.assertNotIn(
            "CRG_SOURCE_UNRESOLVED",
            {item.code for item in report.rows[0].findings},
        )

    def test_crg_collector_warnings_are_not_judged(self) -> None:
        crg_warnings = (
            "NPI Netlist module driver is missing instance or definition name "
            "for clock connection: top.u_tile.clk_rs",
            "clock driver traversal exceeded the depth limit for "
            "top.u_tile.clk_rs",
            "NPI Netlist could not resolve clock connection: "
            "top.u_tile.clk_rs",
        )
        warned = replace(
            self.inventory,
            warnings=crg_warnings,
        )
        report = check_specs(self.specs, warned, self.rtl, self.rules)

        self.assertTrue(report.passed)
        self.assertEqual(report.global_findings, ())
        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)

    def test_non_crg_collector_warning_fails_closed(self) -> None:
        warned = replace(
            self.inventory,
            warnings=(
                "language hierarchy traversal exceeded the depth limit at "
                "top.u_tile.generated_scope",
            ),
        )
        report = check_specs(self.specs, warned, self.rtl, self.rules)

        self.assertFalse(report.passed)
        self.assertEqual(report.global_findings[0].code, "NPI_UNRESOLVED")

    def test_only_crg_warnings_are_removed_from_mixed_collector_warnings(self) -> None:
        generic_warning = (
            "NPI parameter is missing its name in module instance: top.u_tile.u_rs"
        )
        warned = replace(
            self.inventory,
            warnings=(
                "clock driver traversal exceeded the depth limit for "
                "top.u_tile.clk_rs",
                generic_warning,
            ),
        )
        report = check_specs(self.specs, warned, self.rtl, self.rules)

        self.assertFalse(report.passed)
        self.assertEqual(len(report.global_findings), 1)
        self.assertEqual(report.global_findings[0].code, "NPI_UNRESOLVED")
        self.assertEqual(report.global_findings[0].message, generic_warning)

    def test_partial_load_notice_is_visible_but_nonfatal(self) -> None:
        noticed = replace(
            self.inventory,
            notices=(
                "npi_load_design reported elaboration errors, but top remains queryable",
            ),
        )
        report = check_specs(self.specs, noticed, self.rtl, self.rules)

        self.assertTrue(report.passed)
        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.global_findings[0].severity, "warning")
        self.assertEqual(report.global_findings[0].code, "NPI_LOAD_PARTIAL")

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
