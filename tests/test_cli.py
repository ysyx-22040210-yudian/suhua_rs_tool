from __future__ import annotations

import csv
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rscheck.cli import main
from rscheck.config import load_config
from rscheck.inventory import load_inventory


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def _check_args(self) -> list[str]:
        return [
            "check",
            "--excel",
            str(ROOT / "tests" / "fixtures" / "specs.csv"),
            "--config",
            str(ROOT / "config" / "rscheck.example.json"),
            "--sheet",
            "1",
        ]

    def test_offline_check_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            json_report = Path(name) / "report.json"
            csv_report = Path(name) / "report.csv"
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(ROOT / "tests" / "fixtures" / "specs.csv"),
                        "--config",
                        str(ROOT / "config" / "rscheck.example.json"),
                        "--sheet",
                        "1",
                        "--inventory",
                        str(ROOT / "tests" / "fixtures" / "inventory.json"),
                        "--json-report",
                        str(json_report),
                        "--csv-report",
                        str(csv_report),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("RESULT: PASS", output.getvalue())
            self.assertTrue(json_report.is_file())
            self.assertTrue(csv_report.is_file())
            report = json.loads(json_report.read_text("utf-8"))
            self.assertEqual(report["schema_version"], 3)
            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(report["rows"][0]["spec"]["RS_CFG_EN"], "假门控")
            self.assertEqual(
                report["rows"][0]["matched_instances"][0]["parameters"]["RS_CRG_EN"],
                "0",
            )
            self.assertEqual(report["rows"][0]["step_check"]["physical_instances"], 6)
            self.assertEqual(report["rows"][0]["step_check"]["effective_step"], 5)
            self.assertEqual(
                [
                    item["contribution"]
                    for item in report["rows"][0]["step_check"]["contributions"]
                ],
                [1, 1, 0, 1, 1, 1],
            )
            with csv_report.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            self.assertEqual(csv_rows[0]["status"], "PASS")
            self.assertEqual(csv_rows[0]["RS_CFG_EN"], "假门控")
            self.assertEqual(csv_rows[0]["physical_instances"], "6")
            self.assertEqual(csv_rows[0]["effective_step"], "5")

    def test_module_without_rs_crg_en_ignores_excel_value_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "specs.csv"
            config = directory / "config.json"
            inventory = directory / "inventory.json"
            json_report = directory / "report.json"
            csv_report = directory / "report.csv"

            specs.write_text(
                (
                    "Intf_type,RS_module,RS_inst,position,step,clk,rst,"
                    "CRG_source,RS_CFG_EN\n"
                    "OUT_IF,rs_pipe,AAAA_BBB,top.u_tile,5,clk_rs,rst_n,"
                    "not_checked,用户任意填写\n"
                ),
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["module_rules"]["rs_pipe"]["has_rs_cfg_en"] = False
            config.write_text(
                json.dumps(raw_config, ensure_ascii=False), encoding="utf-8"
            )

            raw_inventory = json.loads(
                (ROOT / "tests" / "fixtures" / "inventory.json").read_text("utf-8")
            )
            for instance in raw_inventory["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    instance["parameters"].pop("RS_CRG_EN")
            inventory.write_text(
                json.dumps(raw_inventory, ensure_ascii=False), encoding="utf-8"
            )

            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--inventory",
                        str(inventory),
                        "--json-report",
                        str(json_report),
                        "--csv-report",
                        str(csv_report),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("RESULT: PASS", output.getvalue())
            report = json.loads(json_report.read_text("utf-8"))
            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(report["rows"][0]["findings"], [])
            self.assertEqual(report["rows"][0]["spec"]["RS_CFG_EN"], "用户任意填写")
            with csv_report.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            self.assertEqual(csv_rows[0]["status"], "PASS")
            self.assertEqual(csv_rows[0]["RS_CFG_EN"], "用户任意填写")

    def test_offline_mismatch_reports_effective_step_and_label(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            json_report = Path(name) / "negative.json"
            csv_report = Path(name) / "negative.csv"
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(ROOT / "tests" / "fixtures" / "specs_negative.csv"),
                        "--config",
                        str(ROOT / "config" / "rscheck.example.json"),
                        "--sheet",
                        "1",
                        "--inventory",
                        str(ROOT / "tests" / "fixtures" / "inventory.json"),
                        "--json-report",
                        str(json_report),
                        "--csv-report",
                        str(csv_report),
                    ]
                )
            self.assertEqual(code, 1)
            result = json.loads(json_report.read_text("utf-8"))
            codes = {item["code"] for item in result["rows"][0]["findings"]}
            self.assertEqual(codes, {"STEP_MISMATCH", "RS_CFG_EN_LABEL_MISMATCH"})
            self.assertEqual(result["rows"][0]["step_check"]["effective_step"], 5)
            with csv_report.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            self.assertTrue(csv_rows)
            self.assertTrue(all(row["RS_CFG_EN"] == "真门控" for row in csv_rows))
            self.assertTrue(all(row["effective_step"] == "5" for row in csv_rows))

    def test_validate_json_contains_optional_rs_cfg_en_value(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "validate",
                    "--excel",
                    str(ROOT / "tests" / "fixtures" / "specs.csv"),
                    "--config",
                    str(ROOT / "config" / "rscheck.example.json"),
                    "--json",
                ]
            )
        self.assertEqual(code, 0)
        rows = json.loads(output.getvalue())
        self.assertEqual(rows[0]["RS_CFG_EN"], "假门控")

    def test_validate_json_contains_resolved_position_and_alias(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "specs.csv"
            specs.write_text(
                (ROOT / "tests" / "fixtures" / "specs.csv")
                .read_text("utf-8")
                .replace("top.u_tile", "tile_alias", 1),
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["position_mappings"] = {"tile_alias": "top.u_tile"}
            config = directory / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "validate",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--json",
                    ]
                )
            rows = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(rows[0]["position"], "top.u_tile")
        self.assertEqual(rows[0]["position_alias"], "tile_alias")

    def test_position_database_cli_crud_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            config = Path(name) / "config.json"
            raw = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw["position_mappings"] = {}
            config.write_text(json.dumps(raw), encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "position-db",
                        "set",
                        "--config",
                        str(config),
                        "--alias",
                        "core0_lsu",
                        "--rtl-path",
                        "tb_top.dut.u_core0.u_lsu",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("POSITION_DB SAVED", output.getvalue())
            self.assertEqual(
                load_config(config).position_mappings["core0_lsu"],
                "tb_top.dut.u_core0.u_lsu",
            )

            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "position-db",
                        "resolve",
                        "--config",
                        str(config),
                        "--position",
                        "core0_lsu",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "position": "tb_top.dut.u_core0.u_lsu",
                    "position_alias": "core0_lsu",
                },
            )

            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "position-db",
                        "list",
                        "--config",
                        str(config),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["mappings"],
                {"core0_lsu": "tb_top.dut.u_core0.u_lsu"},
            )

            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "position-db",
                        "delete",
                        "--config",
                        str(config),
                        "--alias",
                        "core0_lsu",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(load_config(config).position_mappings, {})

    def test_position_database_resolve_rejects_empty_normalized_value(self) -> None:
        for value in ("", "   ", "..."):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                config = Path(name) / "config.json"
                config.write_text(
                    (ROOT / "config" / "rscheck.example.json").read_text("utf-8"),
                    encoding="utf-8",
                )
                error = StringIO()
                with redirect_stderr(error):
                    code = main(
                        [
                            "position-db",
                            "resolve",
                            "--config",
                            str(config),
                            "--position",
                            value,
                        ]
                    )
            self.assertEqual(code, 2)
            self.assertIn("must not be empty", error.getvalue())

    def test_position_database_rejected_updates_leave_config_unchanged(self) -> None:
        cases = (
            (
                "invalid_set",
                ["set", "--alias", " bad_alias", "--rtl-path", "top.u"],
                "surrounding whitespace",
            ),
            (
                "missing_delete",
                ["delete", "--alias", "missing_alias"],
                "is not registered",
            ),
        )
        for case, command, expected_error in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as name:
                config = Path(name) / "config.json"
                config.write_bytes(
                    (ROOT / "config" / "rscheck.example.json").read_bytes()
                )
                original = config.read_bytes()
                error = StringIO()
                with redirect_stderr(error):
                    code = main(
                        ["position-db", command[0], "--config", str(config), *command[1:]]
                    )
                self.assertEqual(code, 2)
                self.assertIn(expected_error, error.getvalue())
                self.assertEqual(config.read_bytes(), original)

    def test_position_database_replace_failure_leaves_config_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            config = directory / "config.json"
            config.write_bytes(
                (ROOT / "config" / "rscheck.example.json").read_bytes()
            )
            original = config.read_bytes()
            error = StringIO()
            with patch(
                "rscheck.config.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with redirect_stderr(error):
                    code = main(
                        [
                            "position-db",
                            "set",
                            "--config",
                            str(config),
                            "--alias",
                            "new_alias",
                            "--rtl-path",
                            "top.u_new",
                        ]
                    )
            self.assertEqual(code, 2)
            self.assertIn("cannot save config file", error.getvalue())
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual(list(directory.glob(".config.json.*.tmp")), [])

    def test_offline_check_uses_resolved_position_for_inventory_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "specs.csv"
            specs.write_text(
                (ROOT / "tests" / "fixtures" / "specs.csv")
                .read_text("utf-8")
                .replace("top.u_tile", "tile_alias", 1),
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["position_mappings"] = {"tile_alias": "top.u_tile"}
            config = directory / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")
            json_report = directory / "report.json"
            csv_report = directory / "report.csv"
            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--inventory",
                        str(ROOT / "tests" / "fixtures" / "inventory.json"),
                        "--json-report",
                        str(json_report),
                        "--csv-report",
                        str(csv_report),
                    ]
                )
            report = json.loads(json_report.read_text("utf-8"))
            with csv_report.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
        self.assertEqual(code, 0)
        self.assertEqual(report["rows"][0]["spec"]["position"], "top.u_tile")
        self.assertEqual(report["rows"][0]["spec"]["position_alias"], "tile_alias")
        self.assertEqual(csv_rows[0]["position"], "top.u_tile")
        self.assertEqual(csv_rows[0]["position_alias"], "tile_alias")

    def test_failed_mapped_row_csv_preserves_alias_for_every_finding(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "negative.csv"
            specs.write_text(
                (ROOT / "tests" / "fixtures" / "specs_negative.csv")
                .read_text("utf-8")
                .replace("top.u_tile", "tile_alias"),
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["position_mappings"] = {"tile_alias": "top.u_tile"}
            config = directory / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")
            csv_report = directory / "negative.csv.report.csv"
            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--inventory",
                        str(ROOT / "tests" / "fixtures" / "inventory.json"),
                        "--csv-report",
                        str(csv_report),
                    ]
                )
            with csv_report.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
        self.assertEqual(code, 1)
        self.assertGreaterEqual(len(csv_rows), 2)
        self.assertTrue(all(row["position"] == "top.u_tile" for row in csv_rows))
        self.assertTrue(all(row["position_alias"] == "tile_alias" for row in csv_rows))

    def test_validate_accepts_unregistered_rs_module(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            specs = Path(name) / "unknown.csv"
            specs.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "OUT,unknown_pipe,PFX,top.u,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "validate",
                        "--excel",
                        str(specs),
                        "--config",
                        str(ROOT / "config" / "rscheck.example.json"),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("VALID: 1 specification row(s)", output.getvalue())

    def test_offline_check_uses_default_rule_for_unregistered_module(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "default_rule.csv"
            specs.write_text(
                "Intf_type,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "OUT_IF,rs_pipe,AAAA_BBB,top.u_tile,6,clk_rs,rst_n,crg_core,假门控\n",
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            del raw_config["module_rules"]["rs_pipe"]
            config = directory / "default_rule.json"
            config.write_text(
                json.dumps(raw_config, ensure_ascii=False), encoding="utf-8"
            )
            raw_inventory = json.loads(
                (ROOT / "tests" / "fixtures" / "inventory.json").read_text("utf-8")
            )
            for instance in raw_inventory["positions"]["top.u_tile"]["instances"]:
                if instance["name"].startswith("AAAA_BBB"):
                    ports = instance["ports"]
                    ports["rst_n"] = ports.pop("rst")
            inventory = directory / "default_rule_inventory.json"
            inventory.write_text(json.dumps(raw_inventory), encoding="utf-8")
            json_report = directory / "default_rule_report.json"

            with redirect_stdout(StringIO()):
                code = main(
                    [
                        "check",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--sheet",
                        "1",
                        "--inventory",
                        str(inventory),
                        "--json-report",
                        str(json_report),
                    ]
                )

            report = json.loads(json_report.read_text("utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(report["rows"][0]["findings"], [])
        self.assertEqual(
            report["rows"][0]["module_rule"],
            {
                "name": "rs_pipe",
                "has_rs_cfg_en": True,
                "step_parameters": [],
                "clk_port": "clk",
                "rst_port": "rst_n",
            },
        )
        self.assertEqual(report["rows"][0]["step_check"]["effective_step"], 6)
        self.assertEqual(
            [
                item["contribution"]
                for item in report["rows"][0]["step_check"]["contributions"]
            ],
            [1, 1, 1, 1, 1, 1],
        )

    def test_collector_requires_elaborated_database(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            code = main(self._check_args() + ["--collector", "collector"])
        self.assertEqual(code, 2)
        self.assertIn("--collector requires --elab-db", error.getvalue())

    def test_inventory_rejects_elaborated_database(self) -> None:
        error = StringIO()
        with redirect_stderr(error):
            code = main(
                self._check_args()
                + [
                    "--inventory",
                    str(ROOT / "tests" / "fixtures" / "inventory.json"),
                    "--elab-db",
                    "kdb",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("--elab-db requires --collector", error.getvalue())

    def test_old_or_incomplete_inventory_fails_closed(self) -> None:
        source = json.loads(
            (ROOT / "tests" / "fixtures" / "inventory.json").read_text("utf-8")
        )
        mutations = (
            ("old_schema", lambda raw: raw.__setitem__("schema_version", 1), "expected 2"),
            (
                "missing_parameters",
                lambda raw: raw["positions"]["top.u_tile"]["instances"][0].pop(
                    "parameters"
                ),
                "parameters",
            ),
        )
        for name, mutation, expected_message in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                raw = json.loads(json.dumps(source))
                mutation(raw)
                inventory = Path(directory) / "inventory.json"
                inventory.write_text(json.dumps(raw), encoding="utf-8")
                error = StringIO()
                with redirect_stderr(error):
                    code = main(self._check_args() + ["--inventory", str(inventory)])
                self.assertEqual(code, 2)
                self.assertIn(expected_message, error.getvalue())

    def test_collector_forwards_elaborated_database(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            elab_db = Path(name) / "custom_kdb"
            elab_db.mkdir()
            inventory = load_inventory(ROOT / "tests" / "fixtures" / "inventory.json")
            with patch("rscheck.cli.collect_inventory", return_value=inventory) as collect:
                with redirect_stdout(StringIO()):
                    code = main(
                        self._check_args()
                        + [
                            "--collector",
                            "collector",
                            "--elab-db",
                            str(elab_db),
                        ]
                    )
        self.assertEqual(code, 0)
        self.assertEqual(collect.call_args[1]["elab_db"], str(elab_db))

    def test_collector_receives_resolved_position_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            specs = directory / "specs.csv"
            specs.write_text(
                (ROOT / "tests" / "fixtures" / "specs.csv")
                .read_text("utf-8")
                .replace("top.u_tile", "tile_alias", 1),
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["position_mappings"] = {"tile_alias": "top.u_tile"}
            config = directory / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")
            elab_db = directory / "kdb"
            elab_db.mkdir()
            inventory = load_inventory(ROOT / "tests" / "fixtures" / "inventory.json")
            with patch("rscheck.cli.collect_inventory", return_value=inventory) as collect:
                with redirect_stdout(StringIO()):
                    code = main(
                        [
                            "check",
                            "--excel",
                            str(specs),
                            "--config",
                            str(config),
                            "--collector",
                            "collector",
                            "--elab-db",
                            str(elab_db),
                        ]
                    )
            collected_specs = collect.call_args.args[1]
        self.assertEqual(code, 0)
        self.assertEqual(collected_specs[0].position, "top.u_tile")
        self.assertEqual(collected_specs[0].position_alias, "tile_alias")

    def test_legacy_design_passthrough_is_rejected(self) -> None:
        for option in ("-f", "-sv", "-lib"):
            with self.subTest(option=option):
                error = StringIO()
                with redirect_stderr(error), self.assertRaises(SystemExit) as raised:
                    main(
                        self._check_args()
                        + [
                            "--collector",
                            "collector",
                            "--elab-db",
                            "kdb",
                            "--",
                            option,
                            "design-input",
                        ]
                    )
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("unrecognized arguments", error.getvalue())

    def test_header_check_can_enable_validation_disabled_by_config(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            specs = root / "specs.csv"
            specs.write_text(
                "Wrong,RS_module,RS_inst,position,step,clk,rst,CRG_source,RS_CFG_EN\n"
                "OUT,rs_pipe,PIPE,top.u,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            self.assertFalse(raw_config["excel"]["validate_headers"])
            config = root / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")

            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(["validate", "--excel", str(specs), "--config", str(config)]),
                    0,
                )
            error = StringIO()
            with redirect_stderr(error):
                code = main(
                    [
                        "validate",
                        "--excel",
                        str(specs),
                        "--config",
                        str(config),
                        "--header-check",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("header validation failed", error.getvalue())

    def test_no_header_check_can_disable_strict_config(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            specs = root / "specs.csv"
            specs.write_text(
                "接口列,模块列,实例列,位置列,拍数列,时钟列,复位列,时钟源列,门控列\n"
                "OUT,rs_pipe,PIPE,top.u,1,clk,rst,crg,\n",
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["excel"]["validate_headers"] = True
            config = root / "config.json"
            config.write_text(json.dumps(raw_config), encoding="utf-8")

            error = StringIO()
            with redirect_stderr(error):
                self.assertEqual(
                    main(["validate", "--excel", str(specs), "--config", str(config)]),
                    2,
                )
            self.assertIn("header validation failed", error.getvalue())
            with redirect_stdout(StringIO()):
                self.assertEqual(
                    main(
                        [
                            "validate",
                            "--excel",
                            str(specs),
                            "--config",
                            str(config),
                            "--no-header-check",
                        ]
                    ),
                    0,
                )

if __name__ == "__main__":
    unittest.main()
