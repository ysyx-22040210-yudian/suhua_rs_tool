from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rscheck.hierarchy_cache import CacheLoadStopped, HierarchyCache, KdbRevision
from rscheck.model import InventoryError
from tests.test_hierarchy_tree import payload, scope


class KdbCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.db = self.base / "A.elab++"
        self.db.mkdir()
        self.session = self.base / "session.json"
        self.root = self.base / "private"
        self.cache = HierarchyCache(self.root)
        self.calls = []

    def collect(self, path):
        self.calls.append(path)
        path.write_text(json.dumps(payload([scope("top"), scope("top.u")])), encoding="utf-8")

    def get(self, cache=None, path=None, collect=None, **kwargs):
        return (cache or self.cache).load(str(path or self.db), self.base, self.session,
                                         collect or self.collect, lambda: False, **kwargs)

    def test_memory_disk_cross_session_manual_clear_and_filter_settings_not_in_key(self):
        model, source, _ = self.get()
        self.assertEqual(source, "collector")
        with patch("rscheck.hierarchy_cache.load_hierarchy_index", side_effect=AssertionError("hot tree reparsed")):
            same, source, _ = self.get(path=Path("./A.elab++"))
        self.assertIs(model, same)
        self.assertEqual(source, "memory")
        other = HierarchyCache(self.root)
        second_session = self.base / "other.json"
        args = (str(self.db), self.base, second_session, self.collect, lambda: False)
        self.assertEqual(other.load(*args)[1], "disk")
        self.assertEqual(self.get()[1], "memory")  # another GUI linked the disk file
        self.assertEqual(other.load(*args, clear=True)[1], "collector")
        self.assertEqual(self.get()[1], "disk")  # don't keep the other GUI's purged generation
        self.assertEqual(len(self.calls), 2)

    def test_same_path_rebuild_symlink_and_different_kdb_never_mix(self):
        marker = self.db / "_stnElabDB"
        marker.write_bytes(b"original")
        self.get()
        os.utime(self.db, None)  # Verdi touches the root on a browse
        self.assertEqual(self.get()[1], "memory")
        marker.write_bytes(b"rebuilt design")
        self.assertEqual(self.get()[1], "collector")
        b = self.base / "B.elab++"
        b.mkdir()
        def collect_b(path):
            path.write_text(json.dumps(payload([scope("different_top")])), encoding="utf-8")
        model, source, _ = self.get(path=b, collect=collect_b)
        self.assertEqual((list(model.by_path), source), (["different_top"], "collector"))
        self.assertEqual(self.get()[1], "disk")
        self.db.rename(self.base / "old.elab++")
        self.db.mkdir()
        (self.db / marker.name).write_bytes(b"rebuilt design")
        self.assertEqual(self.get()[1], "collector")
        self.get(clear=True)
        self.assertEqual(self.get(path=b)[1], "disk")  # clear only the requested KDB

    def test_nested_alternate_layout_and_retargeted_symlink(self):
        sub = self.db / "db"
        sub.mkdir()
        obj = sub / "netlist"
        obj.write_bytes(b"old")
        self.get()
        obj.write_bytes(b"re-elaborated")
        self.assertEqual(self.get()[1], "collector")
        link = self.base / "current.elab++"
        try:
            link.symlink_to(self.db, target_is_directory=True)
        except OSError:
            return  # Windows may disallow unprivileged links; rest of the test still ran.
        self.assertEqual(self.get(path=link)[1], "memory")
        b = self.base / "B.elab++"
        b.mkdir()
        link.unlink()
        link.symlink_to(b, target_is_directory=True)
        self.assertEqual(self.get(path=link)[1], "collector")
        # A linked work library may itself hold hierarchy tables. Ignore cycles
        # without missing a nested change when the top-level _stnElabDB is intact.
        (self.db / "_stnElabDB").write_bytes(b"elaboration")
        (self.db / "work.lib++").symlink_to(b, target_is_directory=True)
        (b / "back").symlink_to(self.db, target_is_directory=True)
        obj = b / "libfile"
        obj.write_bytes(b"one")
        self.get()
        obj.write_bytes(b"new lib")
        self.assertEqual(self.get()[1], "collector")

    def test_invalid_disk_failure_cancel_and_rebuild_during_collection_not_published(self):
        self.get()
        disk, = self.root.rglob("*.json")
        disk.write_text("not json", encoding="utf-8")
        model, source, warning = self.get()
        self.assertEqual((len(model.by_path), source), (2, "collector"))
        self.assertIn("缓存不可用", warning)
        for cause in ("cancel", "error", "change"):
            def interrupt(path):
                self.collect(path)
                if cause == "change":
                    (self.db / "new-design").write_text("changed", encoding="utf-8")
                else:
                    raise CacheLoadStopped() if cause == "cancel" else InventoryError("bad KDB")
            with self.subTest(cause=cause):
                with self.assertRaises((CacheLoadStopped, InventoryError)):
                    self.get(clear=True, collect=interrupt)
                self.assertFalse(list(self.root.rglob("*.json")))
                self.assertIsNone(self.cache.hot)
        self.assertEqual(self.get()[1], "collector")

    def test_only_real_elab_directories_and_readonly_cache_degrades_to_memory(self):
        for name in ("work.lib++", "filelist.f"):
            bad = self.base / name
            bad.mkdir() if name.endswith("++") else bad.touch()
            with self.assertRaises(InventoryError):
                KdbRevision.read(str(bad), self.base)
        self.assertEqual(self.get(path=self.db)[1], "collector")
        custom = self.base / "custom-elab-output"
        custom.mkdir()
        self.assertEqual(self.get(path=custom)[1], "collector")
        with self.assertRaisesRegex(InventoryError, "不能位于 KDB 内"):
            self.get(HierarchyCache(self.db / "cache"))
        self.assertFalse((self.db / "cache").exists())
        self.root = self.base / "unwritable"
        self.cache = HierarchyCache(self.root)
        self.root.write_bytes(b"not a directory")
        first, source, note = self.get()
        self.assertEqual(source, "collector")
        self.assertIn("无法保存", note)
        self.assertIs(self.get()[0], first)
        self.assertEqual(len(self.calls), 3)
