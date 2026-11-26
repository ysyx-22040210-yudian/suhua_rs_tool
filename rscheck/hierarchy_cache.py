"""User-local DUT indices. No design files, raw RTL, or mapping databases are written."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .hierarchy_tree import ScopeTree
from .model import InventoryError
from .npi_runner import load_hierarchy_index


def _cache_root() -> Path:
    override = os.environ.get("RSCHECK_HIERARCHY_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "rscheck" / "dut-hierarchy-v1"


def _stamp(path: Path) -> tuple:
    st = path.stat()
    # Verdi itself creates/removes transient entries on a read-only browse.
    # A directory's mtime (and a file's atime) is not a design revision.
    if stat.S_ISDIR(st.st_mode):
        return ("dir", st.st_dev, st.st_ino)
    return ("file", st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _digest(value: str) -> str:
    return hashlib.sha256(os.fsencode(value)).hexdigest()


@dataclass(frozen=True)
class KdbRevision:
    path: Path
    key: str
    version: str

    @classmethod
    def read(cls, value: str, base: Path) -> KdbRevision:
        path = (base / Path(value).expanduser()).resolve(strict=True)
        # elabcom also allows user-named output directories; the collector
        # validates the actual design on a miss, just as in an online check.
        if not path.is_dir() or path.name == "work.lib++":
            raise InventoryError(f"请选择 Verdi elaborated KDB 目录（例如 top.elab++）：{path}")
        records = [(".", _stamp(path))]
        # Walk KDB *file metadata*, not its RTL scopes or file contents. This
        # also detects changes to nested tables / linked compiled libraries
        # in a rebuild that preserves the root inode and its marker files.
        seen_dirs = set()
        def walk_error(error: OSError) -> None:
            raise error
        for folder, dirs, files in os.walk(path, followlinks=True, onerror=walk_error):
            folder_id = _stamp(Path(folder))
            if folder_id in seen_dirs:
                dirs[:] = []  # directory symlink cycles and shared libraries
                continue
            seen_dirs.add(folder_id)
            dirs.sort()
            for name in dirs + files:
                item = Path(folder) / name
                try:
                    stamp = _stamp(item)
                except FileNotFoundError:
                    if not item.is_symlink():
                        raise
                    stamp = ("dangling", os.readlink(item))
                records.append((str(item.relative_to(path)), stamp))
        return cls(path, _digest(os.path.normcase(str(path))), _digest(repr(sorted(records))))


class CacheLoadStopped(Exception):
    """The collector failed or the picker was cancelled. Never publish this attempt."""


@dataclass
class CachedTree:
    revision: KdbRevision
    tree: ScopeTree
    disk_stamp: tuple | None


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _disk_stamp(file: Path) -> tuple:
    st = file.stat()
    # Linking an immutable snapshot into another GUI session changes ctime, not its content.
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


class HierarchyCache:
    """One hot tree per GUI, plus atomic, revision-keyed JSON snapshots across launches.

    Called only by the GUI's serialized background worker. A private session copy lets
    cancellable regex subprocesses read the same snapshot even if another GUI clears it.
    """
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else _cache_root()
        self.hot: CachedTree | None = None

    def load(self, kdb: str, base: Path, session_index: Path, collect: Callable[[Path], None],
             cancelled: Callable[[], bool], *, clear: bool = False) -> tuple[ScopeTree, str, str]:
        def check_cancel() -> None:
            if cancelled():
                raise CacheLoadStopped()

        check_cancel()
        rev = KdbRevision.read(kdb, base)
        cache_root = self.root.expanduser().resolve()
        if rev.path == cache_root or rev.path in cache_root.parents:
            raise InventoryError("层次缓存目录不能位于 KDB 内，请调整 RSCHECK_HIERARCHY_CACHE_DIR。")
        folder = cache_root / rev.key
        disk = folder / (rev.version + ".json")
        if clear:
            self.hot = None
            # Only our own snapshot names for this KDB; never remove a user's directory.
            if folder.is_dir():
                for item in folder.glob("*.json"):
                    if len(item.stem) == 64 and all(c in "0123456789abcdef" for c in item.stem):
                        item.unlink(missing_ok=True)
            session_index.unlink(missing_ok=True)
        try:
            disk_stamp = _disk_stamp(disk)
        except OSError:
            disk_stamp = None
        if self.hot and self.hot.revision == rev and self.hot.disk_stamp == disk_stamp and session_index.is_file():
            check_cancel()
            return self.hot.tree, "memory", ""
        tree = None
        note = ""
        with tempfile.TemporaryDirectory(dir=session_index.parent) as work:
            staged = Path(work) / "index.json"
            if disk_stamp is not None:
                try:
                    _link_or_copy(disk, staged)
                    tree = ScopeTree.from_index(load_hierarchy_index(staged))
                except (InventoryError, OSError, UnicodeError) as exc:
                    note = f"本地层次缓存不可用，重新读取 KDB：{exc}"
                    staged.unlink(missing_ok=True)
            source = "disk" if tree is not None else "collector"
            check_cancel()
            if tree is None:
                collect(staged)
                check_cancel()
                tree = ScopeTree.from_index(load_hierarchy_index(staged))
            check_cancel()
            if KdbRevision.read(kdb, base) != rev:
                raise InventoryError("加载期间 KDB 已更换或重新生成。结果未缓存，请重新打开 DUT 层次。")
            if source == "collector":
                try:
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=folder) as publication:
                        publish = Path(publication) / "index.json"
                        _link_or_copy(staged, publish)
                        check_cancel()
                        publish.replace(disk)
                    disk_stamp = _disk_stamp(disk)
                except OSError as exc:
                    disk_stamp = None
                    note = f"无法保存持久层次缓存，本次运行仍可复用：{exc}"
            # A failed load for B never overwrites the private snapshot for A.
            staged.replace(session_index)
            self.hot = CachedTree(rev, tree, disk_stamp)
        return tree, source, note
