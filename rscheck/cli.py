from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .checker import check_specs
from .config import (
    load_config,
    make_crg_source_mappings,
    make_position_mappings,
    save_config,
)
from .excel_reader import read_spec_rows
from .inventory import load_inventory
from .model import (
    MAX_CRG_TRACE_DEPTH,
    ConfigError,
    FIELD_NAMES,
    Inventory,
    RsCheckError,
    SkippedRow,
    SpecRow,
    ToolConfig,
)
from .npi_runner import (
    DEFAULT_HIERARCHY_SEARCH_LIMIT,
    MAX_HIERARCHY_SEARCH_LIMIT,
    collect_hierarchy,
    collect_inventory,
    search_hierarchy,
)
from .reporting import format_console_report, write_csv_report, write_json_report, write_match_details


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--excel", required=True, help="input .xlsx/.xlsm/.csv/.tsv file")
    parser.add_argument("--config", required=True, help="JSON configuration file")
    parser.add_argument("--sheet", help="override sheet name or 1-based sheet index")
    parser.add_argument(
        "--column",
        action="append",
        default=[],
        metavar="FIELD=INDEX",
        help="map an internal field to a 1-based column; RS_route= disables the optional mapping",
    )
    parser.add_argument("--header-row", type=int, help="override header row")
    parser.add_argument("--data-start-row", type=int, help="override first data row")
    header_check = parser.add_mutually_exclusive_group()
    header_check.add_argument(
        "--header-check",
        dest="header_check",
        action="store_true",
        help="opt in to requiring mapped header cells to equal internal field names",
    )
    header_check.add_argument(
        "--no-header-check",
        dest="header_check",
        action="store_false",
        help="use mapped column numbers regardless of header text (default)",
    )
    parser.set_defaults(header_check=None)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtl-rs-check",
        description="Check register-slice RTL instances against an Excel specification.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("gui", help="open the desktop graphical interface")

    validate = subparsers.add_parser(
        "validate",
        parents=[_common_parser()],
        help="validate and display normalized Excel rows without loading RTL",
    )
    validate.add_argument("--json", action="store_true", help="print normalized rows as JSON")
    validate.add_argument("--include-skipped", action="store_true",
                          help="with --json, return an object containing rows and skipped_rows")

    check = subparsers.add_parser(
        "check",
        parents=[_common_parser()],
        help="run checks with an existing inventory or the NPI collector",
    )
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--inventory", help="existing schema_version=2 or 3 NPI inventory JSON"
    )
    source.add_argument("--collector", help="NPI collector executable")
    check.add_argument(
        "--elab-db",
        help="existing Verdi elaborated database directory (required with --collector)",
    )
    check.add_argument("--json-report", help="write a structured JSON report")
    check.add_argument("--csv-report", help="write an Excel-friendly UTF-8 CSV report")
    check.add_argument("--match-details", action="store_true", help="print human-readable instance match details")
    check.add_argument("--match-report", metavar="FILE", help="write the same match details as a UTF-8 text file")
    check.add_argument(
        "--keep-inventory",
        help="copy live NPI inventory to this path (only with --collector)",
    )
    check.add_argument(
        "--npi-timeout",
        type=int,
        help="live NPI collection timeout in seconds",
    )
    check.add_argument(
        "--npi-lib-dir",
        help=(
            "directory containing libNPI.so; defaults to "
            "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM"
        ),
    )
    check.add_argument(
        "--crg-trace-max-depth",
        type=int,
        help=(
            "override the maximum upstream CRG module trace depth "
            f"(1..{MAX_CRG_TRACE_DEPTH})"
        ),
    )

    hierarchy_search = subparsers.add_parser(
        "hierarchy-search",
        help="search DUT scope paths in a Verdi elaborated KDB",
    )
    hierarchy_search.add_argument(
        "--collector", required=True, help="NPI collector executable"
    )
    hierarchy_search.add_argument(
        "--elab-db", required=True, help="Verdi elaborated database directory"
    )
    hierarchy_mode = hierarchy_search.add_mutually_exclusive_group(required=True)
    hierarchy_mode.add_argument(
        "--regex",
        help="Python regular expression matched against full hierarchy paths",
    )
    # The GUI owns the temporary directory for exactly one dialog and one KDB.
    hierarchy_mode.add_argument("--browse", action="store_true", help=argparse.SUPPRESS)
    hierarchy_search.add_argument("--session-index", type=Path, help=argparse.SUPPRESS)
    hierarchy_search.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_HIERARCHY_SEARCH_LIMIT,
        help=(
            "maximum displayed matches "
            f"(1..{MAX_HIERARCHY_SEARCH_LIMIT}, default "
            f"{DEFAULT_HIERARCHY_SEARCH_LIMIT})"
        ),
    )
    hierarchy_search.add_argument(
        "--npi-timeout", type=int, help="hierarchy collection timeout in seconds"
    )
    hierarchy_search.add_argument(
        "--npi-lib-dir",
        help=(
            "directory containing libNPI.so; defaults to "
            "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM"
        ),
    )
    hierarchy_search.add_argument(
        "--json", action="store_true", help="print structured search results"
    )

    position_db = subparsers.add_parser(
        "position-db",
        help="manage Excel position shorthand to RTL hierarchy mappings",
    )
    position_commands = position_db.add_subparsers(
        dest="position_db_command", required=True
    )
    position_list = position_commands.add_parser("list", help="list position mappings")
    position_list.add_argument("--config", required=True, help="JSON configuration file")
    position_list.add_argument("--json", action="store_true", help="print JSON")
    position_set = position_commands.add_parser("set", help="add or replace a mapping")
    position_set.add_argument("--config", required=True, help="JSON configuration file")
    position_set.add_argument("--alias", required=True, help="Excel position shorthand")
    position_set.add_argument("--rtl-path", required=True, help="full RTL hierarchy path")
    position_delete = position_commands.add_parser("delete", help="delete a mapping")
    position_delete.add_argument("--config", required=True, help="JSON configuration file")
    position_delete.add_argument("--alias", required=True, help="Excel position shorthand")
    position_resolve = position_commands.add_parser("resolve", help="resolve one position value")
    position_resolve.add_argument("--config", required=True, help="JSON configuration file")
    position_resolve.add_argument("--position", required=True, help="Excel position value")
    position_resolve.add_argument("--json", action="store_true", help="print JSON")

    crg_source_db = subparsers.add_parser(
        "crg-source-db",
        help="manage Excel CRG_source shorthand to RTL hierarchy mappings",
    )
    crg_source_commands = crg_source_db.add_subparsers(
        dest="crg_source_db_command", required=True
    )
    crg_source_list = crg_source_commands.add_parser(
        "list", help="list CRG_source mappings"
    )
    crg_source_list.add_argument(
        "--config", required=True, help="JSON configuration file"
    )
    crg_source_list.add_argument("--json", action="store_true", help="print JSON")
    crg_source_set = crg_source_commands.add_parser(
        "set", help="add or replace a mapping"
    )
    crg_source_set.add_argument(
        "--config", required=True, help="JSON configuration file"
    )
    crg_source_set.add_argument(
        "--alias", required=True, help="Excel CRG_source shorthand"
    )
    crg_source_set.add_argument(
        "--rtl-path", required=True, help="full RTL hierarchy path"
    )
    crg_source_delete = crg_source_commands.add_parser(
        "delete", help="delete a mapping"
    )
    crg_source_delete.add_argument(
        "--config", required=True, help="JSON configuration file"
    )
    crg_source_delete.add_argument(
        "--alias", required=True, help="Excel CRG_source shorthand"
    )
    crg_source_resolve = crg_source_commands.add_parser(
        "resolve", help="resolve one CRG_source value"
    )
    crg_source_resolve.add_argument(
        "--config", required=True, help="JSON configuration file"
    )
    crg_source_resolve.add_argument(
        "--crg-source", required=True, help="Excel CRG_source value"
    )
    crg_source_resolve.add_argument(
        "--json", action="store_true", help="print JSON"
    )

    return parser


def _column_overrides(values: Sequence[str]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for value in values:
        if "=" not in value:
            raise ConfigError(f"invalid --column {value!r}; expected FIELD=INDEX")
        field_name, raw_index = value.split("=", 1)
        if field_name not in FIELD_NAMES:
            raise ConfigError(
                f"unknown --column field {field_name!r}; expected one of {', '.join(FIELD_NAMES)}"
            )
        if field_name == "RS_route" and not raw_index:
            result[field_name] = None
            continue
        try:
            index = int(raw_index)
        except ValueError as exc:
            raise ConfigError(f"invalid column index in --column {value!r}") from exc
        if index < 1:
            raise ConfigError(f"column index must be >= 1 in --column {value!r}")
        result[field_name] = index
    return result


def _apply_overrides(config: ToolConfig, args: argparse.Namespace) -> ToolConfig:
    columns = dict(config.excel.columns)
    for name, index in _column_overrides(args.column).items():
        if index is None:
            columns.pop(name, None)
        else:
            columns[name] = index
    reverse: dict[int, list[str]] = {}
    for field_name, column in columns.items():
        reverse.setdefault(column, []).append(field_name)
    collisions = {column: names for column, names in reverse.items() if len(names) > 1}
    if collisions:
        detail = "; ".join(
            f"column {column}: {', '.join(names)}" for column, names in collisions.items()
        )
        raise ConfigError("column mappings must be unique after overrides; " + detail)

    sheet = config.excel.sheet
    if args.sheet is not None:
        sheet = (
            int(args.sheet)
            if args.sheet.isascii() and args.sheet.isdecimal()
            else args.sheet
        )
    header_row = args.header_row if args.header_row is not None else config.excel.header_row
    data_start_row = (
        args.data_start_row
        if args.data_start_row is not None
        else config.excel.data_start_row
    )
    if header_row < 1 or data_start_row < 1:
        raise ConfigError("header and data row numbers must be >= 1")
    if data_start_row <= header_row:
        raise ConfigError("data start row must be after the header row")
    excel = replace(
        config.excel,
        sheet=sheet,
        header_row=header_row,
        data_start_row=data_start_row,
        validate_headers=(
            config.excel.validate_headers
            if args.header_check is None
            else args.header_check
        ),
        columns=columns,
    )
    return replace(config, excel=excel)


def _read_workbook(args: argparse.Namespace, config: ToolConfig) -> tuple[list[SpecRow], list[SkippedRow]]:
    skipped: list[SkippedRow] = []
    specs = read_spec_rows(
        args.excel,
        config.excel,
        config.position_mappings,
        config.crg_source_mappings,
        on_notice=lambda message: print(message, file=sys.stderr),
        on_skip=skipped.append,
    )
    if skipped:
        print(f"info[ROWS_SKIPPED]: 整行跳过 {len(skipped)} 行，参与检查 {len(specs)} 行。", file=sys.stderr)
        for item in skipped[:10]:
            print(f"  {item.describe()}", file=sys.stderr)
        if len(skipped) > 10:
            print("  仅预览前 10 行；完整清单见 JSON / CSV 报告或 validate --json --include-skipped。", file=sys.stderr)
    elif not specs:
        print("info[NO_ACTIVE_ROWS]: 规格数据区为空，未执行检查。", file=sys.stderr)
    return specs, skipped


def _validate_command(args: argparse.Namespace, config: ToolConfig) -> int:
    specs, skipped = _read_workbook(args, config)
    for spec in specs:
        for finding in spec.blank_findings:
            print(f"warning[{finding.code}]: {finding.message}", file=sys.stderr)
    if args.json:
        rows = [spec.as_dict() for spec in specs]
        print(json.dumps({"rows": rows, "skipped_rows": [s.as_dict() for s in skipped]} if args.include_skipped
                         else rows, ensure_ascii=False, indent=2))
    else:
        print(f"VALID: {len(specs)} specification row(s)")
        for spec in specs:
            position = spec.position
            if spec.position_alias:
                position = f"{spec.position_alias} -> {spec.position}"
            crg_source = spec.crg_source
            if spec.crg_source_alias:
                crg_source = f"{spec.crg_source_alias} -> {spec.crg_source}"
            print(
                f"row {spec.row_number}: {position} / {spec.selector_label} "
                f"module={spec.rs_module} step={spec.step} "
                f"CRG_source={crg_source}"
            )
            if spec.rs_inst_formula_cell:
                print(f"  RS_inst 公式结果（{spec.sheet}!{spec.rs_inst_formula_cell}）："
                      f"{json.dumps(spec.rs_inst, ensure_ascii=False)}")
            if len(spec.rs_inst_items) > 1:
                print("  RS_inst items: " + "; ".join(spec.rs_inst_items))
    return 0


def _check_command(
    args: argparse.Namespace,
    config: ToolConfig,
) -> int:
    if args.match_report:
        dest = Path(args.match_report).expanduser().resolve()
        for name in ("excel", "config", "inventory", "collector", "keep_inventory", "json_report", "csv_report"):
            path = getattr(args, name, None)
            if path:
                other = Path(path).expanduser().resolve()
                if dest == other or (dest.exists() and other.exists() and dest.samefile(other)):
                    raise ConfigError(f"--match-report must not overwrite --{name.replace('_', '-')}")
        if args.elab_db and Path(args.elab_db).expanduser().resolve() in (dest, *dest.parents):
            raise ConfigError("--match-report must not be written inside --elab-db")
    if args.npi_timeout is not None and args.npi_timeout < 1:
        raise ConfigError("--npi-timeout must be >= 1")
    if args.crg_trace_max_depth is not None:
        if not 1 <= args.crg_trace_max_depth <= MAX_CRG_TRACE_DEPTH:
            raise ConfigError(
                "--crg-trace-max-depth must be between 1 and "
                f"{MAX_CRG_TRACE_DEPTH}"
            )
        config = replace(
            config,
            rtl=replace(
                config.rtl,
                crg_trace_max_depth=args.crg_trace_max_depth,
            ),
        )
    specs, skipped = _read_workbook(args, config)
    if args.inventory:
        if args.elab_db:
            raise ConfigError("--elab-db requires --collector")
        if args.keep_inventory:
            raise ConfigError("--keep-inventory requires --collector")
        if args.npi_lib_dir:
            raise ConfigError("--npi-lib-dir requires --collector")
        inventory = load_inventory(args.inventory) if specs else Inventory({}, schema_version=4)
    else:
        if not args.elab_db:
            raise ConfigError("--collector requires --elab-db")
        inventory = collect_inventory(
            args.collector,
            specs,
            config.rtl,
            config.module_rules,
            elab_db=args.elab_db,
            timeout_seconds=args.npi_timeout,
            keep_inventory=args.keep_inventory,
            npi_lib_dir=args.npi_lib_dir,
        ) if specs else Inventory({}, schema_version=4)
        if not specs and args.keep_inventory:
            print("info[NO_COLLECTION]: 没有需检查的行，未启动 NPI，也未写入 --keep-inventory。", file=sys.stderr)
    report = replace(check_specs(specs, inventory, config.rtl, config.module_rules), skipped_rows=tuple(skipped))
    print(format_console_report(report, match_details=args.match_details))
    if args.json_report:
        output = write_json_report(report, args.json_report)
        print(f"JSON report: {output}")
    if args.csv_report:
        output = write_csv_report(report, args.csv_report)
        print(f"CSV report: {output}")
    if args.match_report:
        output = write_match_details(report, args.match_report)
        print(f"Match details: {output}")
    return 0 if report.passed else 1


def _hierarchy_search_command(args: argparse.Namespace) -> int:
    if args.npi_timeout is not None and args.npi_timeout < 1:
        raise ConfigError("--npi-timeout must be >= 1")
    if args.browse:
        if not args.session_index:
            raise ConfigError("--browse requires a session index")
        collect_hierarchy(args.collector, elab_db=args.elab_db, timeout_seconds=args.npi_timeout,
                          npi_lib_dir=args.npi_lib_dir, index_output=args.session_index)
        return 0
    result = search_hierarchy(
        args.collector,
        args.regex,
        elab_db=args.elab_db,
        limit=args.limit,
        timeout_seconds=args.npi_timeout,
        npi_lib_dir=args.npi_lib_dir,
        **({"cached_index": args.session_index} if args.session_index else {}),
    )
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0
    suffix = " (results truncated)" if result.truncated else ""
    print(
        f"HIERARCHY_SEARCH: {result.total_matches} match(es), "
        f"showing {len(result.matches)}{suffix}"
    )
    for match in result.matches:
        definition = f" [{match.definition}]" if match.definition else ""
        print(f"{match.path} | {match.kind}{definition}")
    return 0


def _position_db_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    mappings = dict(config.position_mappings)
    command = args.position_db_command
    if command == "list":
        payload = {"mappings": dict(sorted(mappings.items()))}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"POSITION_DB: {len(mappings)} mapping(s)")
            for alias, rtl_path in sorted(mappings.items()):
                print(f"{alias} -> {rtl_path}")
        return 0
    if command == "resolve":
        position_input = args.position.strip().strip(".")
        if not position_input:
            raise ConfigError("--position must not be empty")
        mapped = position_input in mappings
        resolved = mappings.get(position_input, position_input).strip(".")
        payload = {
            "position": resolved,
            "position_alias": position_input if mapped else "",
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"{position_input} -> {resolved} "
                f"({'mapped' if mapped else 'direct'})"
            )
        return 0

    if command == "set":
        mappings[args.alias] = args.rtl_path
    elif command == "delete":
        if args.alias not in mappings:
            raise ConfigError(f"position alias {args.alias!r} is not registered")
        del mappings[args.alias]
    else:  # pragma: no cover - argparse limits the command set
        raise ConfigError(f"unsupported position-db command: {command}")

    updated_mappings = make_position_mappings(mappings)
    output = save_config(
        replace(config, position_mappings=updated_mappings), args.config
    )
    if command == "set":
        print(
            f"POSITION_DB SAVED: {args.alias} -> "
            f"{updated_mappings[args.alias]} | {output}"
        )
    else:
        print(f"POSITION_DB SAVED: deleted {args.alias} | {output}")
    return 0


def _crg_source_db_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    mappings = dict(config.crg_source_mappings)
    command = args.crg_source_db_command
    if command == "list":
        payload = {"mappings": dict(sorted(mappings.items()))}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"CRG_SOURCE_DB: {len(mappings)} mapping(s)")
            for alias, rtl_path in sorted(mappings.items()):
                print(f"{alias} -> {rtl_path}")
        return 0
    if command == "resolve":
        crg_source_input = args.crg_source.strip().strip(".")
        if not crg_source_input:
            raise ConfigError("--crg-source must not be empty")
        mapped = crg_source_input in mappings
        resolved = mappings.get(crg_source_input, crg_source_input).strip(".")
        payload = {
            "CRG_source": resolved,
            "crg_source_alias": crg_source_input if mapped else "",
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"{crg_source_input} -> {resolved} "
                f"({'mapped' if mapped else 'direct'})"
            )
        return 0

    if command == "set":
        mappings[args.alias] = args.rtl_path
    elif command == "delete":
        if args.alias not in mappings:
            raise ConfigError(f"CRG source alias {args.alias!r} is not registered")
        del mappings[args.alias]
    else:  # pragma: no cover - argparse limits the command set
        raise ConfigError(f"unsupported crg-source-db command: {command}")

    updated_mappings = make_crg_source_mappings(mappings)
    output = save_config(
        replace(config, crg_source_mappings=updated_mappings), args.config
    )
    if command == "set":
        print(
            f"CRG_SOURCE_DB SAVED: {args.alias} -> "
            f"{updated_mappings[args.alias]} | {output}"
        )
    else:
        print(f"CRG_SOURCE_DB SAVED: deleted {args.alias} | {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    if args.command == "gui":
        from .gui import main as gui_main

        return gui_main()
    try:
        if args.command == "hierarchy-search":
            return _hierarchy_search_command(args)
        if args.command == "position-db":
            return _position_db_command(args)
        if args.command == "crg-source-db":
            return _crg_source_db_command(args)
        config = _apply_overrides(load_config(args.config), args)
        if args.command == "validate":
            return _validate_command(args, config)
        return _check_command(args, config)
    except RsCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
