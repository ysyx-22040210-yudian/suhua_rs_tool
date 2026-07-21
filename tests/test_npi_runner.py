from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rscheck.model import InventoryError, RtlConfig, SpecRow
from rscheck.npi_runner import _collector_environment, collect_inventory


class NpiRunnerTests(unittest.TestCase):
    def _spec(self) -> SpecRow:
        return SpecRow(
            source=Path("specs.csv"),
            sheet="CSV",
            row_number=2,
            intf_type="OUT_IF",
            rs_module="rs_pipe",
            rs_inst="PIPE",
            position="top.u",
            step=1,
            clk="clk",
            rst="rst",
            crg_source="crg",
        )

    def test_verdi_home_npi_library_is_prepended(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            verdi_home = Path(name)
            library = verdi_home / "share" / "NPI" / "lib" / "LINUX64"
            library.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {
                    "VERDI_HOME": str(verdi_home),
                    "NPI_PLATFORM": "LINUX64",
                    "LD_LIBRARY_PATH": "/existing/lib",
                },
                clear=True,
            ):
                environment = _collector_environment()
            self.assertEqual(
                environment["LD_LIBRARY_PATH"].split(os.pathsep),
                [str(library), "/existing/lib"],
            )

    def test_explicit_missing_npi_library_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            missing = Path(name) / "missing"
            with self.assertRaisesRegex(InventoryError, "not found"):
                _collector_environment(missing)

    def test_missing_elaborated_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "database not found"):
                collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=root / "missing",
                )

    def test_elaborated_database_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "must be a directory"):
                collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=elab_db,
                )

    def test_existing_elaborated_database_is_the_only_design_argument(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "custom_kdb_name"
            elab_db.mkdir()

            def completed(command, **kwargs):
                self.assertEqual(Path(command[2]).read_text("utf-8"), "top.u\n")
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "positions": {
                                "top.u": {"found": False, "instances": []}
                            },
                            "warnings": [],
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("rscheck.npi_runner.subprocess.run", side_effect=completed) as run:
                inventory = collect_inventory(
                    collector,
                    [self._spec()],
                    RtlConfig(),
                    elab_db=elab_db,
                )

            command = run.call_args[0][0]
            self.assertEqual(command[0], str(collector.resolve()))
            self.assertEqual(command[1], "--positions")
            self.assertEqual(command[3], "--output")
            self.assertEqual(
                command[5:],
                [
                    "--clk-port",
                    "clk",
                    "--rst-port",
                    "rst",
                    "--elab-db",
                    str(elab_db.resolve()),
                ],
            )
            self.assertNotIn("--", command)
            self.assertFalse(inventory.positions["top.u"].found)

    def test_non_utf8_collector_output_is_safely_decoded(self) -> None:
        for stream_name in ("stdout", "stderr"):
            with self.subTest(stream=stream_name):
                with tempfile.TemporaryDirectory() as name:
                    root = Path(name)
                    collector = root / "collector"
                    collector.write_text("", encoding="utf-8")
                    elab_db = root / "kdb.elab++"
                    elab_db.mkdir()
                    invalid = b"collector failure: \xff"
                    completed = subprocess.CompletedProcess(
                        [],
                        11,
                        stdout=invalid if stream_name == "stdout" else b"",
                        stderr=invalid if stream_name == "stderr" else b"",
                    )

                    def run_collector(command, **kwargs):
                        self.assertNotIn("text", kwargs)
                        self.assertNotIn("encoding", kwargs)
                        return completed

                    with patch(
                        "rscheck.npi_runner.subprocess.run",
                        side_effect=run_collector,
                    ):
                        with self.assertRaises(InventoryError) as raised:
                            collect_inventory(
                                collector,
                                [self._spec()],
                                RtlConfig(),
                                elab_db=elab_db,
                            )

                    message = str(raised.exception)
                    self.assertIn("exited with code 11", message)
                    self.assertIn("collector failure: \ufffd", message)

    def test_subprocess_unicode_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()
            decode_error = UnicodeDecodeError(
                "utf-8", b"\xff", 0, 1, "invalid start byte"
            )

            with patch(
                "rscheck.npi_runner.subprocess.run",
                side_effect=decode_error,
            ):
                with self.assertRaisesRegex(InventoryError, "failed to decode"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

    def test_temporary_directory_creation_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            with patch(
                "rscheck.npi_runner.tempfile.TemporaryDirectory",
                side_effect=OSError("temporary storage unavailable"),
            ):
                with self.assertRaisesRegex(InventoryError, "temporary NPI files"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )

    def test_positions_write_error_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            collector = root / "collector"
            collector.write_text("", encoding="utf-8")
            elab_db = root / "kdb.elab++"
            elab_db.mkdir()

            with patch(
                "rscheck.npi_runner.Path.write_text",
                side_effect=OSError("temporary storage is full"),
            ), patch("rscheck.npi_runner.subprocess.run") as run:
                with self.assertRaisesRegex(InventoryError, "temporary NPI files"):
                    collect_inventory(
                        collector,
                        [self._spec()],
                        RtlConfig(),
                        elab_db=elab_db,
                    )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
