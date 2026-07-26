from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock

from rscheck.inventory import load_inventory
from scripts.test_rscheck_gui_smoke import (
    _current_sample_inventory_path,
    _install_callback_fail_fast,
    _single_csv_crg_trace_evaluation,
)


ROOT = Path(__file__).resolve().parents[1]


class GuiSmokeHelperTests(unittest.TestCase):
    def test_common_smoke_uses_current_v3_inventory_and_keeps_legacy_v2(self) -> None:
        sample_path = _current_sample_inventory_path(ROOT)
        sample_raw = json.loads(sample_path.read_text(encoding="utf-8"))
        sample = load_inventory(sample_path)
        legacy_path = ROOT / "tests" / "fixtures" / "inventory.json"
        legacy_raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        legacy = load_inventory(legacy_path)

        self.assertEqual(sample_path, ROOT / "examples" / "inventory.json")
        self.assertEqual(sample.schema_version, 3)
        self.assertEqual(sample_raw["schema_version"], 3)
        self.assertTrue(
            all(
                "clock_trace" in instance
                for position in sample_raw["positions"].values()
                for instance in position["instances"]
            )
        )
        self.assertEqual(legacy.schema_version, 2)
        self.assertEqual(legacy_raw["schema_version"], 2)
        self.assertTrue(
            all(
                "clock_trace" not in instance
                for position in legacy_raw["positions"].values()
                for instance in position["instances"]
            )
        )

        instances = {
            instance.name: instance
            for instance in sample.positions["top.u_tile"].instances
        }
        expected_branched_nodes = {
            (
                "top.u_tile.u_occ",
                "clk_occ",
                1,
                ("top.u_tile.u_occ",),
            ),
            (
                "top.u_tile.u_clk_mux",
                "clk_mux",
                2,
                ("top.u_tile.u_occ", "top.u_tile.u_clk_mux"),
            ),
            (
                "top.u_tile.u_crg",
                "crg_core",
                3,
                (
                    "top.u_tile.u_occ",
                    "top.u_tile.u_clk_mux",
                    "top.u_tile.u_crg",
                ),
            ),
            (
                "top.u_tile.u_aux_crg",
                "crg_aux",
                3,
                (
                    "top.u_tile.u_occ",
                    "top.u_tile.u_clk_mux",
                    "top.u_tile.u_aux_crg",
                ),
            ),
        }
        for index in range(6):
            with self.subTest(instance=f"AAAA_BBB_C{index}"):
                trace = instances[f"AAAA_BBB_C{index}"].clock_trace
                self.assertIsNotNone(trace)
                assert trace is not None
                self.assertEqual(trace.clock_port, "clk")
                self.assertEqual(trace.status, "complete")
                self.assertEqual(
                    {
                        (node.instance, node.module, node.depth, node.path)
                        for node in trace.modules
                    },
                    expected_branched_nodes,
                )

        ctrl_trace = instances["CTRL_RS_D0"].clock_trace
        self.assertIsNotNone(ctrl_trace)
        assert ctrl_trace is not None
        self.assertEqual(
            [
                (node.instance, node.module, node.depth, node.path)
                for node in ctrl_trace.modules
            ],
            [
                (
                    "top.u_tile.u_aux_crg",
                    "crg_aux",
                    1,
                    ("top.u_tile.u_aux_crg",),
                )
            ],
        )

    def test_single_csv_crg_trace_evaluation_decodes_mapping_evidence(self) -> None:
        expected = {
            "status": "matched",
            "matched": {
                "instance": "top.u_soc.u_crg_core",
                "depth": 3,
            },
        }
        rows = [{"crg_trace_evidence": json.dumps([expected])}]

        self.assertEqual(_single_csv_crg_trace_evaluation(rows), expected)
        self.assertEqual(_single_csv_crg_trace_evaluation([]), {})
        self.assertEqual(
            _single_csv_crg_trace_evaluation(
                [{"crg_trace_evidence": "not-json"}]
            ),
            {},
        )

    def test_callback_exception_is_fail_fast(self) -> None:
        root = Mock()
        marked_failed = Mock()
        _install_callback_fail_fast(root, marked_failed)
        callback = root.report_callback_exception
        stderr = io.StringIO()

        try:
            raise RuntimeError("deterministic callback failure")
        except RuntimeError as exc:
            with redirect_stderr(stderr):
                callback(type(exc), exc, exc.__traceback__)

        marked_failed.assert_called_once_with()
        root.destroy.assert_called_once_with()
        output = stderr.getvalue()
        self.assertIn("GUI_SMOKE_FAIL: unhandled Tk callback exception", output)
        self.assertIn("RuntimeError: deterministic callback failure", output)


if __name__ == "__main__":
    unittest.main()
