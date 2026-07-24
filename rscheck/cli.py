from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from typing import Sequence

from .checker import check_specs
from .config import load_config
from .excel_reader import read_spec_rows
from .inventory import load_inventory
from .model import ConfigError, FIELD_NAMES, RsCheckError, ToolConfig
from .npi_runner import collect_inventory
from .reporting import format_console_report, write_csv_report, write_json_report


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
        help="override a 1-based column mapping; may be repeated",
    )
    parser.add_argument("--header-row", type=int, help="override header row")
    parser.add_argument("--data-start-row", type=int, help="override first data row")
    header_check = parser.add_mutually_exclusive_group()
    header_check.add_argument(
        "--header-check",
        dest="header_check",
        action="store_true",
        help="require mapped header cells to equal field names",
    )
    header_check.add_argument(
        "--no-header-check",
        dest="header_check",
        action="store_false",
        help="do not require mapped header cells to equal field names",
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

    check = subparsers.add_parser(
        "check",
        parents=[_common_parser()],
        help="run checks with an existing inventory or the NPI collector",
    )
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory", help="existing schema_version=1 NPI inventory JSON")
    source.add_argument("--collector", help="NPI collector executable")
    check.add_argument(
        "--elab-db",
        help="existing Verdi elaborated database directory (required with --collector)",
    )
    check.add_argument("--json-report", help="write a structured JSON report")
    check.add_argument("--csv-report", help="write an Excel-friendly UTF-8 CSV report")
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
    return parser


def _column_overrides(values: Sequence[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise ConfigError(f"invalid --column {value!r}; expected FIELD=INDEX")
        field_name, raw_index = value.split("=", 1)
        if field_name not in FIELD_NAMES:
            raise ConfigError(
                f"unknown --column field {field_name!r}; expected one of {', '.join(FIELD_NAMES)}"
            )
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
    columns.update(_column_overrides(args.column))
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
        sheet = int(args.sheet) if args.sheet.isdigit() else args.sheet
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


def _validate_command(args: argparse.Namespace, config: ToolConfig) -> int:
    specs = read_spec_rows(args.excel, config.excel)
    if args.json:
        print(json.dumps([spec.as_dict() for spec in specs], ensure_ascii=False, indent=2))
    else:
        print(f"VALID: {len(specs)} specification row(s)")
        for spec in specs:
            print(
                f"row {spec.row_number}: {spec.position} / {spec.rs_inst} "
                f"module={spec.rs_module} step={spec.step}"
            )
    return 0


def _check_command(
    args: argparse.Namespace,
    config: ToolConfig,
) -> int:
    if args.npi_timeout is not None and args.npi_timeout < 1:
        raise ConfigError("--npi-timeout must be >= 1")
    specs = read_spec_rows(args.excel, config.excel)
    if args.inventory:
        if args.elab_db:
            raise ConfigError("--elab-db requires --collector")
        if args.keep_inventory:
            raise ConfigError("--keep-inventory requires --collector")
        if args.npi_lib_dir:
            raise ConfigError("--npi-lib-dir requires --collector")
        inventory = load_inventory(args.inventory)
    else:
        if not args.elab_db:
            raise ConfigError("--collector requires --elab-db")
        inventory = collect_inventory(
            args.collector,
            specs,
            config.rtl,
            elab_db=args.elab_db,
            timeout_seconds=args.npi_timeout,
            keep_inventory=args.keep_inventory,
            npi_lib_dir=args.npi_lib_dir,
        )
    report = check_specs(specs, inventory, config.rtl)
    print(format_console_report(report))
    if args.json_report:
        output = write_json_report(report, args.json_report)
        print(f"JSON report: {output}")
    if args.csv_report:
        output = write_csv_report(report, args.csv_report)
        print(f"CSV report: {output}")
    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    if args.command == "gui":
        from .gui import main as gui_main

        return gui_main()
    try:
        config = _apply_overrides(load_config(args.config), args)
        if args.command == "validate":
            return _validate_command(args, config)
        return _check_command(args, config)
    except RsCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
