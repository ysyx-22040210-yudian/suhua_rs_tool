from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .inventory import load_inventory
from .position_bounds import SUBTREE_SCOPE
from .model import (
    MAX_CRG_TRACE_DEPTH,
    Inventory,
    InventoryError,
    ModuleRule,
    RtlConfig,
    SpecRow,
    resolve_module_rule,
)


HIERARCHY_SCHEMA = "rscheck.hierarchy"
HIERARCHY_SEARCH_SCHEMA = "rscheck.hierarchy-search"
DEFAULT_HIERARCHY_SEARCH_LIMIT = 500
MAX_HIERARCHY_SEARCH_LIMIT = 5000
MAX_HIERARCHY_REGEX_LENGTH = 256
HIERARCHY_SCOPE_KINDS = frozenset({"module", "interface", "program", "gen_scope"})


@dataclass(frozen=True)
class HierarchyScope:
    path: str
    name: str
    definition: str
    kind: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], label: str) -> "HierarchyScope":
        expected = {"path", "name", "definition", "kind"}
        if set(value) != expected:
            raise InventoryError(
                f"{label} fields must be exactly: {', '.join(sorted(expected))}"
            )
        path = value.get("path")
        name = value.get("name")
        definition = value.get("definition")
        kind = value.get("kind")
        if not isinstance(path, str) or not path:
            raise InventoryError(f"{label}.path must be a non-empty string")
        if not isinstance(name, str):
            raise InventoryError(f"{label}.name must be a string")
        if not isinstance(definition, str):
            raise InventoryError(f"{label}.definition must be a string")
        if kind not in HIERARCHY_SCOPE_KINDS:
            raise InventoryError(
                f"{label}.kind must be one of: "
                + ", ".join(sorted(HIERARCHY_SCOPE_KINDS))
            )
        return cls(path=path, name=name, definition=definition, kind=kind)

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "name": self.name,
            "definition": self.definition,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class HierarchyIndex:
    scopes: tuple[HierarchyScope, ...]
    warnings: tuple[str, ...]
    notices: tuple[str, ...]
    truncated: bool
    visited_scopes: int


@dataclass(frozen=True)
class HierarchySearchResult:
    query: str
    limit: int
    matches: tuple[HierarchyScope, ...]
    total_matches: int
    truncated: bool
    index_truncated: bool
    visited_scopes: int
    warnings: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], label: str = "hierarchy search result"
    ) -> "HierarchySearchResult":
        expected = {
            "schema",
            "schema_version",
            "query",
            "limit",
            "matches",
            "total_matches",
            "truncated",
            "index_truncated",
            "visited_scopes",
            "warnings",
            "notices",
        }
        if set(value) != expected:
            raise InventoryError(
                f"{label} fields must be exactly: {', '.join(sorted(expected))}"
            )
        if value.get("schema") != HIERARCHY_SEARCH_SCHEMA:
            raise InventoryError(
                f"{label}.schema must be {HIERARCHY_SEARCH_SCHEMA!r}"
            )
        if type(value.get("schema_version")) is not int or value.get(
            "schema_version"
        ) != 1:
            raise InventoryError(f"{label}.schema_version must be integer 1")
        query = value.get("query")
        if not isinstance(query, str):
            raise InventoryError(f"{label}.query must be a string")
        normalized_query = normalize_hierarchy_query(query)
        limit = validate_hierarchy_search_limit(value.get("limit"))
        raw_matches = value.get("matches")
        if not isinstance(raw_matches, list) or not all(
            isinstance(item, Mapping) for item in raw_matches
        ):
            raise InventoryError(f"{label}.matches must be an array of objects")
        matches = tuple(
            HierarchyScope.from_mapping(item, f"{label}.matches[{index}]")
            for index, item in enumerate(raw_matches)
        )
        paths = [item.path for item in matches]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise InventoryError(
                f"{label}.matches must be sorted by unique hierarchy path"
            )
        if len(matches) > limit:
            raise InventoryError(f"{label}.matches exceeds its declared limit")
        total_matches = value.get("total_matches")
        if type(total_matches) is not int or total_matches < len(matches):
            raise InventoryError(
                f"{label}.total_matches must be an integer >= returned matches"
            )
        if len(matches) != min(total_matches, limit):
            raise InventoryError(
                f"{label}.matches length is inconsistent with total_matches and limit"
            )
        truncated = value.get("truncated")
        index_truncated = value.get("index_truncated")
        if type(truncated) is not bool or type(index_truncated) is not bool:
            raise InventoryError(
                f"{label}.truncated and index_truncated must be booleans"
            )
        if truncated != (index_truncated or total_matches > len(matches)):
            raise InventoryError(f"{label}.truncated is inconsistent with its evidence")
        visited_scopes = value.get("visited_scopes")
        if type(visited_scopes) is not int or visited_scopes < total_matches:
            raise InventoryError(
                f"{label}.visited_scopes must be an integer >= total_matches"
            )
        warnings = _string_array(value.get("warnings"), f"{label}.warnings")
        notices = _string_array(value.get("notices"), f"{label}.notices")
        return cls(
            query=normalized_query,
            limit=limit,
            matches=matches,
            total_matches=total_matches,
            truncated=truncated,
            index_truncated=index_truncated,
            visited_scopes=visited_scopes,
            warnings=warnings,
            notices=notices,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": HIERARCHY_SEARCH_SCHEMA,
            "schema_version": 1,
            "query": self.query,
            "limit": self.limit,
            "matches": [match.as_dict() for match in self.matches],
            "total_matches": self.total_matches,
            "truncated": self.truncated,
            "index_truncated": self.index_truncated,
            "visited_scopes": self.visited_scopes,
            "warnings": list(self.warnings),
            "notices": list(self.notices),
        }


def _string_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InventoryError(f"{label} must be an array of strings")
    return tuple(value)


def normalize_hierarchy_query(value: str) -> str:
    if not isinstance(value, str) or not value or value.isspace():
        raise InventoryError("hierarchy regex must not be empty")
    if any(character in value for character in "\x00\r\n"):
        raise InventoryError("hierarchy regex must not contain NUL or newlines")
    if len(value) > MAX_HIERARCHY_REGEX_LENGTH:
        raise InventoryError(
            "hierarchy regex must not exceed "
            f"{MAX_HIERARCHY_REGEX_LENGTH} characters"
        )
    try:
        re.compile(value)
    except (re.error, OverflowError, RecursionError) as exc:
        raise InventoryError(f"invalid hierarchy regex: {exc}") from exc
    return value


def validate_hierarchy_search_limit(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= MAX_HIERARCHY_SEARCH_LIMIT:
        raise InventoryError(
            "hierarchy search limit must be an integer between 1 and "
            f"{MAX_HIERARCHY_SEARCH_LIMIT}"
        )
    return value


def _verdi_npi_library_dir(environment: dict[str, str]) -> Path | None:
    verdi_home = environment.get("VERDI_HOME") or environment.get("NOVAS_INST_DIR")
    if not verdi_home:
        return None
    library_root = Path(verdi_home) / "share" / "NPI" / "lib"
    platform = environment.get("NPI_PLATFORM", "LINUX64")
    candidates = [library_root / platform]
    lowercase = library_root / platform.lower()
    if lowercase not in candidates:
        candidates.append(lowercase)
    for candidate in candidates:
        if (candidate / "libNPI.so").is_file():
            return candidate
    for library in sorted(library_root.glob("*/libNPI.so")):
        if library.is_file():
            return library.parent
    return None


def _collector_environment(npi_lib_dir: str | Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    library_dir: Path | None = None
    if npi_lib_dir is not None:
        library_dir = Path(npi_lib_dir).expanduser().resolve()
        if not library_dir.is_dir():
            raise InventoryError(f"NPI library directory not found: {library_dir}")
    else:
        library_dir = _verdi_npi_library_dir(environment)

    if library_dir is not None:
        current = environment.get("LD_LIBRARY_PATH", "")
        entries = [item for item in current.split(os.pathsep) if item]
        value = str(library_dir)
        if value not in entries:
            entries.insert(0, value)
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(entries)
    return environment


def _decode_collector_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _bounded_collector_output(value: bytes | str | None, limit: int = 4000) -> str:
    text = _decode_collector_output(value).strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n... collector output truncated ...\n" + text[-half:]


def _collector_diagnostics(
    completed: subprocess.CompletedProcess, temp_dir: Path
) -> str:
    streams = []
    for name, value in (("stdout", completed.stdout), ("stderr", completed.stderr)):
        text = _bounded_collector_output(value)
        if text:
            streams.append(f"{name}:\n{text}")
    for log_path in sorted(temp_dir.glob("*Log/compiler.log")):
        try:
            text = _bounded_collector_output(log_path.read_bytes())
        except OSError:
            continue
        if text:
            streams.append(f"{log_path.relative_to(temp_dir)}:\n{text}")
    return "\n".join(streams)


def _resolve_live_paths(
    collector: str | Path, elab_db: str | Path
) -> tuple[Path, Path]:
    try:
        collector_path = Path(collector).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise InventoryError(f"cannot resolve NPI collector path: {exc}") from exc
    if not collector_path.is_file():
        raise InventoryError(f"NPI collector executable not found: {collector_path}")
    try:
        elab_db_path = Path(elab_db).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise InventoryError(
            f"cannot resolve Verdi elaborated database path: {exc}"
        ) from exc
    if not elab_db_path.exists():
        raise InventoryError(f"Verdi elaborated database not found: {elab_db_path}")
    if not elab_db_path.is_dir():
        raise InventoryError(
            f"Verdi elaborated database must be a directory: {elab_db_path}"
        )
    if elab_db_path.name == "work.lib++":
        raise InventoryError(
            "work.lib++ is a compiled Verdi library, not an elaborated KDB"
        )
    return collector_path, elab_db_path


def load_hierarchy_index(path: str | Path) -> HierarchyIndex:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read NPI hierarchy index: {exc}") from exc
    if not isinstance(value, Mapping):
        raise InventoryError("NPI hierarchy index root must be an object")
    expected = {
        "schema",
        "schema_version",
        "scopes",
        "warnings",
        "notices",
        "truncated",
        "visited_scopes",
    }
    if set(value) != expected:
        raise InventoryError(
            "NPI hierarchy index fields must be exactly: "
            + ", ".join(sorted(expected))
        )
    if value.get("schema") != HIERARCHY_SCHEMA:
        raise InventoryError(f"NPI hierarchy index schema must be {HIERARCHY_SCHEMA!r}")
    if type(value.get("schema_version")) is not int or value.get(
        "schema_version"
    ) != 1:
        raise InventoryError("NPI hierarchy index schema_version must be integer 1")
    raw_scopes = value.get("scopes")
    if not isinstance(raw_scopes, list) or not all(
        isinstance(item, Mapping) for item in raw_scopes
    ):
        raise InventoryError("NPI hierarchy index scopes must be an array of objects")
    scopes = tuple(
        HierarchyScope.from_mapping(item, f"NPI hierarchy index scopes[{index}]")
        for index, item in enumerate(raw_scopes)
    )
    paths = [scope.path for scope in scopes]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise InventoryError(
            "NPI hierarchy index scopes must be sorted by unique hierarchy path"
        )
    warnings = _string_array(value.get("warnings"), "NPI hierarchy index warnings")
    notices = _string_array(value.get("notices"), "NPI hierarchy index notices")
    truncated = value.get("truncated")
    if type(truncated) is not bool:
        raise InventoryError("NPI hierarchy index truncated must be a boolean")
    visited_scopes = value.get("visited_scopes")
    if type(visited_scopes) is not int or visited_scopes < len(scopes):
        raise InventoryError(
            "NPI hierarchy index visited_scopes must be an integer >= scopes length"
        )
    return HierarchyIndex(
        scopes=scopes,
        warnings=warnings,
        notices=notices,
        truncated=truncated,
        visited_scopes=visited_scopes,
    )


def collect_hierarchy(
    collector: str | Path,
    *,
    elab_db: str | Path,
    timeout_seconds: int | None = None,
    npi_lib_dir: str | Path | None = None,
    index_output: Path | None = None,
) -> HierarchyIndex:
    collector_path, elab_db_path = _resolve_live_paths(collector, elab_db)
    if timeout_seconds is not None and (
        type(timeout_seconds) is not int or timeout_seconds < 1
    ):
        raise InventoryError("NPI hierarchy timeout must be a positive integer")
    try:
        with tempfile.TemporaryDirectory(prefix="rtl-rs-hierarchy-") as temp_name:
            temp_dir = Path(temp_name)
            output_path = temp_dir / "hierarchy.json"
            command = [
                str(collector_path),
                "--list-hierarchy",
                "--output",
                str(output_path),
                "--elab-db",
                str(elab_db_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env=_collector_environment(npi_lib_dir),
                    cwd=str(temp_dir),
                )
            except subprocess.TimeoutExpired as exc:
                raise InventoryError(
                    "NPI hierarchy collector timed out after "
                    f"{timeout_seconds} second(s)"
                ) from exc
            except OSError as exc:
                raise InventoryError(
                    f"failed to start NPI hierarchy collector: {exc}"
                ) from exc
            except UnicodeError as exc:
                raise InventoryError(
                    f"failed to decode NPI hierarchy collector output: {exc}"
                ) from exc
            if completed.returncode != 0:
                detail = _collector_diagnostics(completed, temp_dir)
                raise InventoryError(
                    "NPI hierarchy collector exited with code "
                    f"{completed.returncode}"
                    + (f":\n{detail}" if detail else "")
                )
            if not output_path.is_file():
                raise InventoryError(
                    "NPI hierarchy collector succeeded but did not create its output"
                )
            hierarchy = load_hierarchy_index(output_path)
            if hierarchy.notices:
                detail = _collector_diagnostics(completed, temp_dir)
                if detail:
                    print(
                        "NPI hierarchy partial-load diagnostics:\n" + detail,
                        file=sys.stderr,
                    )
            if index_output is not None:
                # Publish only a successfully parsed snapshot. Never expose a
                # partially copied index, including when the GUI cancels us.
                with tempfile.NamedTemporaryFile(dir=index_output.parent, delete=False) as staging:
                    staging_path = Path(staging.name)
                try:
                    shutil.copyfile(output_path, staging_path)
                    staging_path.replace(index_output)
                finally:
                    if staging_path.exists():
                        staging_path.unlink()
            return hierarchy
    except OSError as exc:
        raise InventoryError(
            f"cannot create or write temporary NPI hierarchy files: {exc}"
        ) from exc


def search_hierarchy(
    collector: str | Path,
    query: str,
    *,
    elab_db: str | Path,
    limit: int = DEFAULT_HIERARCHY_SEARCH_LIMIT,
    timeout_seconds: int | None = None,
    npi_lib_dir: str | Path | None = None,
    cached_index: Path | None = None,
) -> HierarchySearchResult:
    normalized_query = normalize_hierarchy_query(query)
    normalized_limit = validate_hierarchy_search_limit(limit)
    pattern = re.compile(normalized_query)
    if cached_index is None:
        hierarchy = collect_hierarchy(collector, elab_db=elab_db,
                                      timeout_seconds=timeout_seconds, npi_lib_dir=npi_lib_dir)
    else:
        # The GUI binds this private session snapshot to one verified KDB revision.
        hierarchy = (load_hierarchy_index(cached_index) if cached_index.exists() else
                     collect_hierarchy(collector, elab_db=elab_db, timeout_seconds=timeout_seconds,
                                       npi_lib_dir=npi_lib_dir, index_output=cached_index))
    returned = []
    count = 0
    for scope in hierarchy.scopes:
        if pattern.search(scope.path) is not None:
            count += 1
            if len(returned) < normalized_limit:
                returned.append(scope)
    truncated = hierarchy.truncated or count > len(returned)
    return HierarchySearchResult(
        query=normalized_query,
        limit=normalized_limit,
        matches=tuple(returned),
        total_matches=count,
        truncated=truncated,
        index_truncated=hierarchy.truncated,
        visited_scopes=hierarchy.visited_scopes,
        warnings=hierarchy.warnings,
        notices=hierarchy.notices,
    )


def collect_inventory(
    collector: str | Path,
    specs: Iterable[SpecRow],
    config: RtlConfig,
    module_rules: Mapping[str, ModuleRule] | None = None,
    *,
    elab_db: str | Path,
    timeout_seconds: int | None = None,
    keep_inventory: str | Path | None = None,
    npi_lib_dir: str | Path | None = None,
) -> Inventory:
    collector_path, elab_db_path = _resolve_live_paths(collector, elab_db)
    if (
        type(config.crg_trace_max_depth) is not int
        or not 1 <= config.crg_trace_max_depth <= MAX_CRG_TRACE_DEPTH
    ):
        raise InventoryError(
            "CRG trace max depth must be between 1 and "
            f"{MAX_CRG_TRACE_DEPTH}"
        )
    spec_list = list(specs)
    positions = sorted({spec.position for spec in spec_list})
    configured_rules = module_rules or {}
    trace_rules = {
        spec.rs_module: resolve_module_rule(spec.rs_module, configured_rules)
        for spec in spec_list
    }
    for rule in trace_rules.values():
        for label, value in (
            ("module name", rule.name),
            ("clock port", rule.clk_port),
            ("reset port", rule.rst_port),
        ):
            if any(character in value for character in "\t\r\n"):
                raise InventoryError(
                    f"trace rule {label} must not contain TAB or newline characters"
                )
    try:
        with tempfile.TemporaryDirectory(prefix="rtl-rs-check-") as temp_name:
            temp_dir = Path(temp_name)
            positions_path = temp_dir / "positions.txt"
            trace_rules_path = temp_dir / "trace_rules.tsv"
            driver_plan = temp_dir / "driver_plan.tsv"
            records = [
                ("R", rule.kind, rule.module, rule.output, name)
                for rule in config.trace_cells for name in rule.inputs
            ]
            records.extend(sorted({
                ("T", spec.position, spec.rs_module, spec.crg_source.strip("."))
                for spec in spec_list if "CRG_source" not in spec.ignored_fields
            }))
            if any(not v or any(c in v for c in "\t\r\n\0") for record in records for v in record):
                raise InventoryError("driver plan fields must be nonempty and cannot contain TAB, NUL or newline")
            driver_plan.write_text("".join("\t".join(row) + "\n" for row in records), encoding="utf-8")
            output_path = temp_dir / "inventory.json"
            positions_path.write_text("\n".join(positions) + "\n", encoding="utf-8")
            trace_rules_path.write_text(
                "".join(
                    f"{rule.name}\t{rule.clk_port}\t{rule.rst_port}\n"
                    for _, rule in sorted(trace_rules.items())
                ),
                encoding="utf-8",
            )
            command = [
                str(collector_path),
                "--positions",
                str(positions_path),
                "--output",
                str(output_path),
                "--trace-rules",
                str(trace_rules_path),
                "--driver-plan",
                str(driver_plan),
                "--trace-max-depth",
                str(config.crg_trace_max_depth),
                "--clk-port",
                config.clk_port,
                "--rst-port",
                config.rst_port,
                "--elab-db",
                str(elab_db_path),
            ]
            if any(s.step_sum_check and s.step_sum_check.merged for s in spec_list):
                command.append("--interconnect")
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env=_collector_environment(npi_lib_dir),
                    cwd=str(temp_dir),
                )
            except subprocess.TimeoutExpired as exc:
                raise InventoryError(
                    f"NPI collector timed out after {timeout_seconds} second(s)"
                ) from exc
            except OSError as exc:
                raise InventoryError(f"failed to start NPI collector: {exc}") from exc
            except UnicodeError as exc:
                raise InventoryError(
                    f"failed to decode NPI collector output: {exc}"
                ) from exc
            if completed.returncode != 0:
                detail = _collector_diagnostics(completed, temp_dir)
                if "exactly module<TAB>clock_port" in detail:
                    detail += "\nOld collector: rebuild rs_npi_collector for clock and reset source tracing (inventory v4)."
                if "--interconnect" in command and "unexpected argument '--interconnect'" in detail:
                    detail += "\n该 collector 不支持互联证据，请重新编译本版本 rs_npi_collector。"
                if "unexpected argument '--driver-plan'" in detail:
                    detail += "\n该 collector 使用旧的反追策略，请重新编译本版本 rs_npi_collector。"
                raise InventoryError(
                    f"NPI collector exited with code {completed.returncode}"
                    + (f":\n{detail}" if detail else "")
                )
            if not output_path.is_file():
                raise InventoryError("NPI collector succeeded but did not create its inventory")
            inventory = load_inventory(output_path)
            if inventory.schema_version != 4:
                raise InventoryError(
                    "NPI collector returned legacy inventory schema_version "
                    f"{inventory.schema_version}; rebuild rs_npi_collector for "
                    "clock and reset source-connection tracing (inventory v4)"
                )
            if inventory.position_scope != SUBTREE_SCOPE:
                raise InventoryError("NPI collector did not enumerate position subtrees; rebuild this version of rs_npi_collector")
            if inventory.trace_cells != config.trace_cells:
                raise InventoryError("NPI collector did not confirm the configured driver port mappings; rebuild rs_npi_collector")
            if inventory.notices:
                detail = _collector_diagnostics(completed, temp_dir)
                if detail:
                    print(
                        "NPI collector partial-load diagnostics:\n" + detail,
                        file=sys.stderr,
                    )
            if keep_inventory is not None:
                destination = Path(keep_inventory).resolve()
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(output_path, destination)
                except OSError as exc:
                    raise InventoryError(
                        f"cannot keep NPI inventory at {destination}: {exc}"
                    ) from exc
            return inventory
    except OSError as exc:
        raise InventoryError(f"cannot create or write temporary NPI files: {exc}") from exc
