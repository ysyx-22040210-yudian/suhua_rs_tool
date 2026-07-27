from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .inventory import inventory_to_dict, load_inventory
from .model import InventoryError, MAX_CRG_TRACE_DEPTH


SUCCESS = 0
USAGE_ERROR = 2
POSITIONS_ERROR = 3
ELAB_DB_ERROR = 4
TRACE_RULES_ERROR = 5
KDEBUG_EXEC_ERROR = 10
KDEBUG_ACTION_ERROR = 11
OUTPUT_ERROR = 13
INTERNAL_ERROR = 14

API_VERSION = "kdebug.v1"
ACTION = "rscheck.inventory"
DEFAULT_TRACE_MAX_DEPTH = 16
DEFAULT_KDEBUG_HARD_TIMEOUT_SECONDS = 125.0
KDEBUG_TERM_WAIT_SECONDS = 0.2
KDEBUG_KILL_WAIT_SECONDS = 0.2
KDEBUG_DRAIN_WAIT_SECONDS = 0.1
MAX_DIAGNOSTIC_LENGTH = 4000
POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)


@dataclass(frozen=True)
class CollectorFailure(Exception):
    exit_code: int
    code: str
    message: str


def _trace_depth(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError(
            f"must be an ASCII integer from 1 to {MAX_CRG_TRACE_DEPTH}"
        )
    depth = int(value)
    if not 1 <= depth <= MAX_CRG_TRACE_DEPTH:
        raise argparse.ArgumentTypeError(
            f"must be an ASCII integer from 1 to {MAX_CRG_TRACE_DEPTH}"
        )
    return depth


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rs-kdebug-collector",
        description="Collect an rscheck inventory through the external kdebug JSON API.",
    )
    parser.add_argument("--positions", required=True, help="newline-delimited hierarchy paths")
    parser.add_argument("--output", required=True, help="schema v3 inventory output path")
    parser.add_argument("--trace-rules", help="module<TAB>clock_port rules")
    parser.add_argument(
        "--trace-max-depth",
        type=_trace_depth,
        default=DEFAULT_TRACE_MAX_DEPTH,
        help=f"bounded upstream module depth (1..{MAX_CRG_TRACE_DEPTH})",
    )
    parser.add_argument("--clk-port", required=True, help="legacy default clock formal")
    parser.add_argument("--rst-port", required=True, help="legacy default reset formal")
    parser.add_argument("--elab-db", required=True, help="Verdi elaborated KDB directory")
    return parser


def _read_positions(path: str | Path) -> list[str]:
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CollectorFailure(
            POSITIONS_ERROR,
            "POSITIONS",
            f"cannot read positions file {source}: {exc}",
        ) from exc

    positions: list[str] = []
    seen: set[str] = set()
    for line in lines:
        position = line.strip()
        if position and position not in seen:
            positions.append(position)
            seen.add(position)
    if not positions:
        raise CollectorFailure(
            POSITIONS_ERROR,
            "POSITIONS",
            f"positions file contains no non-empty hierarchy paths: {source}",
        )
    return positions


def _read_trace_rules(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CollectorFailure(
            TRACE_RULES_ERROR,
            "TRACE_RULES",
            f"cannot read trace rules file {source}: {exc}",
        ) from exc

    rules: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if line.count("\t") != 1:
            raise CollectorFailure(
                TRACE_RULES_ERROR,
                "TRACE_RULES",
                f"trace rules line {line_number} must contain exactly module<TAB>clock_port",
            )
        module, clock_port = (item.strip() for item in line.split("\t", 1))
        if not module or not clock_port:
            raise CollectorFailure(
                TRACE_RULES_ERROR,
                "TRACE_RULES",
                f"trace rules line {line_number} has an empty module or clock port",
            )
        previous = rules.get(module)
        if previous is not None and previous != clock_port:
            raise CollectorFailure(
                TRACE_RULES_ERROR,
                "TRACE_RULES",
                f"trace rules define conflicting clock ports for module {module}",
            )
        rules[module] = clock_port
    if not rules:
        raise CollectorFailure(
            TRACE_RULES_ERROR,
            "TRACE_RULES",
            f"trace rules file contains no module<TAB>clock_port entries: {source}",
        )
    return rules


def _resolve_elab_db(path: str | Path) -> Path:
    source = Path(path).expanduser()
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise CollectorFailure(
            ELAB_DB_ERROR,
            "ELAB_DB",
            f"Verdi elaborated KDB path is not accessible: {source} ({exc})",
        ) from exc
    if not resolved.is_dir():
        raise CollectorFailure(
            ELAB_DB_ERROR,
            "ELAB_DB",
            f"Verdi elaborated KDB path must be a directory: {resolved}",
        )
    if resolved.name == "work.lib++":
        raise CollectorFailure(
            ELAB_DB_ERROR,
            "ELAB_DB",
            f"work.lib++ is a compiled Verdi library, not an elaborated KDB: {resolved}",
        )
    return resolved


def _resolve_kdebug() -> str:
    configured = os.environ.get("KDEBUG_BIN", "").strip()
    candidate = configured or "kdebug"
    resolved = shutil.which(candidate)
    if resolved is None:
        source = "KDEBUG_BIN" if configured else "PATH"
        raise CollectorFailure(
            KDEBUG_EXEC_ERROR,
            "KDEBUG_EXEC",
            f"kdebug executable not found via {source}: {candidate}",
        )
    return str(Path(resolved).resolve())


def _kdebug_environment(runtime_directory: Path) -> Mapping[str, str]:
    environment = os.environ.copy()
    environment["TMPDIR"] = str(runtime_directory)
    configured = os.environ.get("RSCHECK_KDEBUG_HOME", "").strip()
    if not configured:
        return environment
    source = Path(configured).expanduser()
    if not source.is_absolute():
        raise CollectorFailure(
            USAGE_ERROR,
            "KDEBUG_HOME",
            "RSCHECK_KDEBUG_HOME must be an absolute path",
        )
    try:
        source.mkdir(parents=True, exist_ok=True)
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise CollectorFailure(
            USAGE_ERROR,
            "KDEBUG_HOME",
            f"cannot create or access RSCHECK_KDEBUG_HOME {source}: {exc}",
        ) from exc
    if not resolved.is_dir():
        raise CollectorFailure(
            USAGE_ERROR,
            "KDEBUG_HOME",
            f"RSCHECK_KDEBUG_HOME must be a directory: {resolved}",
        )
    environment["KDEBUG_HOME"] = str(resolved)
    return environment


def _request(
    *,
    elab_db: Path,
    positions: list[str],
    trace_rules: Mapping[str, str],
    trace_max_depth: int,
    clk_port: str,
    rst_port: str,
    timeout_milliseconds: int | None,
) -> dict[str, Any]:
    request = {
        "api_version": API_VERSION,
        "action": ACTION,
        "target": {"elab_db": str(elab_db)},
        "args": {
            "positions": positions,
            "trace_rules": dict(trace_rules),
            "trace_max_depth": trace_max_depth,
            "clk_port": clk_port,
            "rst_port": rst_port,
        },
        "output": {"format": "json"},
    }
    if timeout_milliseconds is not None:
        request["limits"] = {"timeout_ms": timeout_milliseconds}
    return request


def _kdebug_timeout_policy() -> tuple[int | None, float]:
    raw = os.environ.get("RSCHECK_COLLECTOR_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None, DEFAULT_KDEBUG_HARD_TIMEOUT_SECONDS
    if not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        raise CollectorFailure(
            USAGE_ERROR,
            "TIMEOUT",
            "RSCHECK_COLLECTOR_TIMEOUT_SECONDS must be a positive ASCII integer",
        )
    outer_milliseconds = int(raw) * 1000
    outer_reserve = max(600, min(5000, outer_milliseconds // 10))
    hard_milliseconds = max(300, outer_milliseconds - outer_reserve)
    # kdebug gives its internal engine up to one additional second to clean up.
    # Keep the frontend deadline strictly before this adapter's hard deadline.
    engine_reserve = max(1200, min(2000, hard_milliseconds // 10))
    inner_milliseconds = max(100, hard_milliseconds - engine_reserve)
    return inner_milliseconds, hard_milliseconds / 1000.0


def _terminate_kdebug(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + KDEBUG_TERM_WAIT_SECONDS
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, OSError):
            pass
    if os.name == "posix":
        # The kdebug leader can exit before its process-group descendants. It
        # also owns an engine in a separate group which receives PDEATHSIG only
        # after the leader exits, so retain the full graceful-cleanup window.
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
    try:
        if os.name == "posix":
            # Always sweep the group even when the leader has already exited.
            os.killpg(process.pid, POSIX_SIGKILL)
        elif process.poll() is None:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=KDEBUG_KILL_WAIT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            pass


def _invoke_kdebug(
    command: list[str],
    payload: bytes,
    hard_timeout_seconds: float,
    child_environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    if child_environment is not None:
        popen_kwargs["env"] = dict(child_environment)
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        raise CollectorFailure(
            KDEBUG_EXEC_ERROR,
            "KDEBUG_EXEC",
            f"failed to start kdebug: {exc}",
        ) from exc

    previous_handlers: dict[int, Any] = {}

    def terminate_on_signal(signum: int, _frame: Any) -> None:
        _terminate_kdebug(process)
        raise SystemExit(128 + signum)

    try:
        if os.name == "posix":
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, terminate_on_signal)
        stdout, stderr = process.communicate(input=payload, timeout=hard_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output or b""
        stderr = exc.stderr or b""
        _terminate_kdebug(process)
        try:
            drained_stdout, drained_stderr = process.communicate(
                timeout=KDEBUG_DRAIN_WAIT_SECONDS
            )
            stdout = drained_stdout or stdout
            stderr = drained_stderr or stderr
        except subprocess.TimeoutExpired as drain_exc:
            stdout = drain_exc.output or stdout
            stderr = drain_exc.stderr or stderr
        detail = _bounded(stderr) or _bounded(stdout)
        raise CollectorFailure(
            KDEBUG_EXEC_ERROR,
            "KDEBUG_TIMEOUT",
            f"kdebug exceeded hard timeout {hard_timeout_seconds:g} second(s)"
            + (f": {detail}" if detail else ""),
        ) from exc
    except BaseException:
        _terminate_kdebug(process)
        raise
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _bounded(value: bytes | str | None) -> str:
    text = _decode(value).strip()
    if len(text) <= MAX_DIAGNOSTIC_LENGTH:
        return text
    half = MAX_DIAGNOSTIC_LENGTH // 2
    return text[:half] + "\n... kdebug output truncated ...\n" + text[-half:]


def _emit_child_stderr(value: bytes | str | None) -> None:
    text = _decode(value)
    if not text:
        return
    print(text, file=sys.stderr, end="" if text.endswith("\n") else "\n")


def _parse_response(
    completed: subprocess.CompletedProcess[bytes],
) -> Mapping[str, Any]:
    stdout = _decode(completed.stdout)
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = _bounded(completed.stdout) or "<empty stdout>"
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            f"kdebug did not return one valid JSON object: {exc}; stdout: {detail}",
        ) from exc
    if not isinstance(response, Mapping):
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "kdebug response root must be a JSON object",
        )
    if response.get("api_version") != API_VERSION:
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "kdebug response api_version must be kdebug.v1",
        )
    if response.get("action") != ACTION:
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "kdebug response action must be rscheck.inventory",
        )

    ok = response.get("ok")
    if ok is False:
        error = response.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code", "KDEBUG_ACTION_FAILED"))
            message = str(error.get("message", "kdebug action failed"))
        else:
            code = "KDEBUG_ACTION_FAILED"
            message = str(error or "kdebug action failed")
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_ACTION",
            f"{code}: {message}",
        )
    if ok is not True:
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "kdebug response 'ok' must be true or false",
        )
    if completed.returncode != 0:
        raise CollectorFailure(
            KDEBUG_EXEC_ERROR,
            "KDEBUG_EXEC",
            f"kdebug exited with code {completed.returncode} despite ok=true",
        )

    data = response.get("data")
    if not isinstance(data, Mapping):
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "successful kdebug response requires a 'data' object",
        )
    inventory = data.get("inventory")
    if not isinstance(inventory, Mapping):
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "successful kdebug response requires a 'data.inventory' object",
        )
    return inventory


def _validate_inventory(raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        with tempfile.TemporaryDirectory(prefix="rs-kdebug-inventory-") as name:
            candidate = Path(name) / "inventory.json"
            candidate.write_text(
                json.dumps(raw, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            inventory = load_inventory(candidate)
    except InventoryError as exc:
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            f"kdebug returned an invalid inventory: {exc}",
        ) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise CollectorFailure(
            INTERNAL_ERROR,
            "INTERNAL",
            f"cannot validate kdebug inventory: {exc}",
        ) from exc
    if inventory.schema_version != 3:
        raise CollectorFailure(
            KDEBUG_ACTION_ERROR,
            "KDEBUG_RESPONSE",
            "kdebug returned legacy inventory schema_version "
            f"{inventory.schema_version}; expected 3",
        )
    return inventory_to_dict(inventory)


def _emit_inventory_notices(inventory: Mapping[str, Any]) -> None:
    notices = inventory.get("notices", [])
    if not isinstance(notices, list):
        return
    for notice in notices:
        detail = str(notice)
        marker = (
            "NPI_LOAD_PARTIAL"
            if "NPI_LOAD_PARTIAL" in detail
            else "KDEBUG_NOTICE"
        )
        print(f"warning[{marker}]: {detail}", file=sys.stderr)


def _write_inventory(path: str | Path, inventory: Mapping[str, Any]) -> None:
    destination = Path(path).expanduser().resolve()
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(inventory, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(destination))
        temporary = None
    except OSError as exc:
        raise CollectorFailure(
            OUTPUT_ERROR,
            "OUTPUT",
            f"cannot write inventory {destination}: {exc}",
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def run(args: argparse.Namespace) -> int:
    positions = _read_positions(args.positions)
    trace_rules = _read_trace_rules(args.trace_rules)
    elab_db = _resolve_elab_db(args.elab_db)
    timeout_milliseconds, hard_timeout_seconds = _kdebug_timeout_policy()
    request = _request(
        elab_db=elab_db,
        positions=positions,
        trace_rules=trace_rules,
        trace_max_depth=args.trace_max_depth,
        clk_port=args.clk_port,
        rst_port=args.rst_port,
        timeout_milliseconds=timeout_milliseconds,
    )
    payload = (json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    with tempfile.TemporaryDirectory(prefix="rs-kdebug-runtime-") as runtime_name:
        kdebug_environment = _kdebug_environment(Path(runtime_name))
        kdebug = _resolve_kdebug()
        completed = _invoke_kdebug(
            [kdebug, "--json", "-"],
            payload,
            hard_timeout_seconds,
            kdebug_environment,
        )

    _emit_child_stderr(completed.stderr)
    raw_inventory = _parse_response(completed)
    inventory = _validate_inventory(raw_inventory)
    _emit_inventory_notices(inventory)
    _write_inventory(args.output, inventory)
    return SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except CollectorFailure as exc:
        print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - final executable guard
        print(f"error[INTERNAL]: {exc}", file=sys.stderr)
        return INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
