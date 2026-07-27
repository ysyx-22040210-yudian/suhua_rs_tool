from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

from .inventory import load_inventory
from .model import (
    MAX_CRG_TRACE_DEPTH,
    Inventory,
    InventoryError,
    ModuleRule,
    RtlConfig,
    SpecRow,
    resolve_module_rule,
)


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


def _atomic_copy(source: Path, destination: Path) -> None:
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temporary = Path(temporary_name)
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            descriptor = None
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(str(temporary), str(destination))
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


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
    collector_value = os.fspath(collector)
    collector_candidate = Path(collector_value).expanduser()
    if (
        collector_candidate.is_absolute()
        or collector_candidate.parent != Path(".")
        or collector_candidate.is_file()
    ):
        collector_path = collector_candidate.resolve()
    else:
        collector_command = shutil.which(collector_value)
        collector_path = Path(collector_command or collector_value).resolve()
    if not collector_path.is_file():
        raise InventoryError(f"RTL collector executable not found: {collector_path}")
    elab_db_path = Path(elab_db).expanduser().resolve()
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
            output_path = temp_dir / "inventory.json"
            positions_path.write_text("\n".join(positions) + "\n", encoding="utf-8")
            trace_rules_path.write_text(
                "".join(
                    f"{rule.name}\t{rule.clk_port}\n"
                    for _, rule in sorted(trace_rules.items())
                ),
                encoding="utf-8",
            )
            collector_prefix = [str(collector_path)]
            if collector_path.suffix.lower() == ".py":
                collector_prefix = [sys.executable, str(collector_path)]
            command = collector_prefix + [
                "--positions",
                str(positions_path),
                "--output",
                str(output_path),
                "--trace-rules",
                str(trace_rules_path),
                "--trace-max-depth",
                str(config.crg_trace_max_depth),
                "--clk-port",
                config.clk_port,
                "--rst-port",
                config.rst_port,
                "--elab-db",
                str(elab_db_path),
            ]
            collector_environment = _collector_environment(npi_lib_dir)
            if timeout_seconds is not None:
                collector_environment["RSCHECK_COLLECTOR_TIMEOUT_SECONDS"] = str(
                    timeout_seconds
                )
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env=collector_environment,
                    cwd=str(temp_dir),
                )
            except subprocess.TimeoutExpired as exc:
                raise InventoryError(
                    f"RTL collector timed out after {timeout_seconds} second(s)"
                ) from exc
            except OSError as exc:
                raise InventoryError(f"failed to start RTL collector: {exc}") from exc
            except UnicodeError as exc:
                raise InventoryError(
                    f"failed to decode RTL collector output: {exc}"
                ) from exc
            if completed.returncode != 0:
                detail = _collector_diagnostics(completed, temp_dir)
                raise InventoryError(
                    f"RTL collector exited with code {completed.returncode}"
                    + (f":\n{detail}" if detail else "")
                )
            if not output_path.is_file():
                raise InventoryError(
                    "RTL collector succeeded but did not create its inventory"
                )
            inventory = load_inventory(output_path)
            if inventory.schema_version != 3:
                raise InventoryError(
                    "RTL collector returned legacy inventory schema_version "
                    f"{inventory.schema_version}; use a schema v3 collector for "
                    "recursive CRG tracing"
                )
            if inventory.notices:
                detail = _collector_diagnostics(completed, temp_dir)
                if detail:
                    print(
                        "RTL collector partial-load diagnostics:\n" + detail,
                        file=sys.stderr,
                    )
            if keep_inventory is not None:
                destination = Path(keep_inventory).resolve()
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_copy(output_path, destination)
                except OSError as exc:
                    raise InventoryError(
                        f"cannot keep RTL inventory at {destination}: {exc}"
                    ) from exc
            return inventory
    except OSError as exc:
        raise InventoryError(
            f"cannot create or write temporary collector files: {exc}"
        ) from exc
