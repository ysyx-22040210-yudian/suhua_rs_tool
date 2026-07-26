from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock

from scripts.test_rscheck_gui_smoke import (
    _install_callback_fail_fast,
    _single_csv_crg_trace_evaluation,
)


class GuiSmokeHelperTests(unittest.TestCase):
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
