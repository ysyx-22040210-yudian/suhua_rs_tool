from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rscheck.cli import main
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
            self.assertTrue(json.loads(json_report.read_text("utf-8"))["summary"]["passed"])

    def test_offline_mismatch_returns_one_and_reports_all_core_fields(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            json_report = Path(name) / "negative.json"
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
                    ]
                )
            self.assertEqual(code, 1)
            result = json.loads(json_report.read_text("utf-8"))
            codes = {item["code"] for item in result["rows"][0]["findings"]}
            self.assertTrue(
                {
                    "STEP_MISMATCH",
                    "RS_MODULE_MISMATCH",
                    "CLK_CONNECTION_MISMATCH",
                    "RST_CONNECTION_MISMATCH",
                    "CRG_SOURCE_MISMATCH",
                }.issubset(codes)
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
                "Wrong,RS_module,RS_inst,position,step,clk,rst,CRG_source\n"
                "OUT,rs_pipe,PIPE,top.u,1,clk,rst,crg\n",
                encoding="utf-8",
            )
            raw_config = json.loads(
                (ROOT / "config" / "rscheck.example.json").read_text("utf-8")
            )
            raw_config["excel"]["validate_headers"] = False
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


if __name__ == "__main__":
    unittest.main()
