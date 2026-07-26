from __future__ import annotations

import re
import unittest
from pathlib import Path

from rscheck import __version__


ROOT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_runtime_version_matches_project_metadata(self) -> None:
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project = re.search(
            r"(?ms)^\[project\]\s*$.*?(?=^\[|\Z)",
            metadata,
        )
        self.assertIsNotNone(project)
        version = re.search(
            r'^version\s*=\s*"([^"]+)"\s*$',
            project.group(0),
            re.MULTILINE,
        )
        self.assertIsNotNone(version)
        self.assertEqual(__version__, version.group(1))


if __name__ == "__main__":
    unittest.main()
