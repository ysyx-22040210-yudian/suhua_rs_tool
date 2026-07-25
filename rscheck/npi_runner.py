from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from .inventory import load_inventory
from .model import Inventory, InventoryError, RtlConfig, SpecRow


def _collector_environment(npi_lib_dir: str | Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    library_dir: Path | None = None
    if npi_lib_dir is not None:
        library_dir = Path(npi_lib_dir).expanduser().resolve()
        if not library_dir.is_dir():
            raise InventoryError(f"NPI library directory not found: {library_dir}")
    else:
        verdi_home = environment.get("VERDI_HOME") or environment.get("NOVAS_INST_DIR")
        if verdi_home:
            platform = environment.get("NPI_PLATFORM", "LINUX64")
            candidate = Path(verdi_home) / "share" / "NPI" / "lib" / platform
            if candidate.is_dir():
                library_dir = candidate

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


def collect_inventory(
    collector: str | Path,
    specs: Iterable[SpecRow],
    config: RtlConfig,
    *,
    elab_db: str | Path,
    timeout_seconds: int | None = None,
    keep_inventory: str | Path | None = None,
    npi_lib_dir: str | Path | None = None,
) -> Inventory:
    collector_path = Path(collector).resolve()
    if not collector_path.is_file():
        raise InventoryError(f"NPI collector executable not found: {collector_path}")
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
    positions = sorted({spec.position for spec in specs})
    try:
        with tempfile.TemporaryDirectory(prefix="rtl-rs-check-") as temp_name:
            temp_dir = Path(temp_name)
            positions_path = temp_dir / "positions.txt"
            output_path = temp_dir / "inventory.json"
            positions_path.write_text("\n".join(positions) + "\n", encoding="utf-8")
            command = [
                str(collector_path),
                "--positions",
                str(positions_path),
                "--output",
                str(output_path),
                "--clk-port",
                config.clk_port,
                "--rst-port",
                config.rst_port,
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
                raise InventoryError(
                    f"NPI collector exited with code {completed.returncode}"
                    + (f":\n{detail}" if detail else "")
                )
            if not output_path.is_file():
                raise InventoryError("NPI collector succeeded but did not create its inventory")
            inventory = load_inventory(output_path)
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
