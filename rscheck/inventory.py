from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .config import parse_trace_cells
from .position_bounds import SUBTREE_SCOPE, boundary_forest
from .model import ActualInstance, ConfigError, Inventory, InventoryError, PositionInventory


def load_inventory(path: str | Path) -> Inventory:
    inventory_path = Path(path)
    try:
        raw = json.loads(inventory_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryError(f"inventory file not found: {inventory_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InventoryError(f"cannot read inventory {inventory_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InventoryError(
            f"invalid inventory JSON in {inventory_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise InventoryError("inventory root must be a JSON object")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3, 4}:
        raise InventoryError(
            f"unsupported inventory schema_version {schema_version!r}; expected 2, 3 or 4"
        )

    raw_positions = raw.get("positions")
    scope = raw.get("position_scope")
    if "position_scope" in raw and scope != SUBTREE_SCOPE:
        raise InventoryError(f"unsupported inventory position_scope: {scope!r}")
    if not isinstance(raw_positions, Mapping):
        raise InventoryError("inventory 'positions' must be a JSON object")
    positions: dict[str, PositionInventory] = {}
    subtree_instances: set[str] = set()
    for position, raw_position in raw_positions.items():
        if not isinstance(raw_position, Mapping):
            raise InventoryError(f"position {position!r} must be a JSON object")
        raw_instances = raw_position.get("instances", [])
        if not isinstance(raw_instances, list):
            raise InventoryError(f"position {position!r} 'instances' must be an array")
        instances = []
        full_names: set[str] = set()
        for index, raw_instance in enumerate(raw_instances):
            if not isinstance(raw_instance, Mapping):
                raise InventoryError(
                    f"position {position!r} instance {index} must be a JSON object"
                )
            instance = ActualInstance.from_mapping(
                raw_instance, schema_version=schema_version
            )
            if not instance.name or not instance.full_name:
                raise InventoryError(
                    f"position {position!r} instance {index} requires name and full_name"
                )
            if instance.full_name != instance.name and not instance.full_name.endswith(
                "." + instance.name
            ):
                raise InventoryError(
                    f"position {position!r} instance {index} has inconsistent name "
                    f"{instance.name!r} and full_name {instance.full_name!r}"
                )
            if instance.full_name in full_names:
                raise InventoryError(
                    f"position {position!r} contains duplicate instance full_name "
                    f"{instance.full_name!r}"
                )
            full_names.add(instance.full_name)
            if scope == SUBTREE_SCOPE:
                if instance.full_name in subtree_instances:
                    raise InventoryError(f"physical instance repeated across position buckets: {instance.full_name}")
                subtree_instances.add(instance.full_name)
            instances.append(instance)
        position_name = str(position).strip(".")
        if position_name in positions:
            raise InventoryError(f"duplicate normalized position in inventory: {position_name}")
        found = raw_position.get("found", False)
        if not isinstance(found, bool):
            raise InventoryError(f"position {position!r} 'found' must be true or false")
        children = raw_position.get("child_positions", [] if scope is None else None)
        if not isinstance(children, list) or not all(isinstance(p, str) and p for p in children):
            raise InventoryError(f"position {position!r} requires an array of child_positions")
        if children and scope != SUBTREE_SCOPE:
            raise InventoryError("child_positions requires a partitioned subtree inventory")
        positions[position_name] = PositionInventory(
            found=found,
            instances=tuple(instances),
            child_positions=tuple(children),
        )

    if "warnings" not in raw:
        raise InventoryError(
            f"inventory 'warnings' is required by schema_version {schema_version}"
        )
    raw_warnings = raw.get("warnings")
    if not isinstance(raw_warnings, list):
        raise InventoryError("inventory 'warnings' must be an array")
    raw_notices = raw.get("notices", [])
    if not isinstance(raw_notices, list):
        raise InventoryError("inventory 'notices' must be an array")
    if scope == SUBTREE_SCOPE:
        boundary_forest(positions, scope, ())
    try:
        cells = parse_trace_cells(raw["trace_cells"]) if "trace_cells" in raw else None
    except ConfigError as exc:
        raise InventoryError(str(exc)) from exc
    return Inventory(
        position_scope=scope,
        trace_cells=cells,
        positions=positions,
        warnings=tuple(str(item) for item in raw_warnings),
        notices=tuple(str(item) for item in raw_notices),
        schema_version=schema_version,
    )


def inventory_to_dict(inventory: Inventory) -> dict[str, Any]:
    if inventory.schema_version not in {2, 3, 4}:
        raise InventoryError(
            f"unsupported inventory schema_version {inventory.schema_version!r}; "
            "expected 2, 3 or 4"
        )
    return {
        "schema_version": inventory.schema_version,
        **({"position_scope": inventory.position_scope} if inventory.position_scope else {}),
        **({"trace_cells": [c.as_dict() for c in inventory.trace_cells]} if inventory.trace_cells is not None else {}),
        "positions": {
            position: {
                "found": value.found,
                **({"child_positions": list(value.child_positions)} if inventory.position_scope else {}),
                "instances": [
                    _instance_to_dict(instance, inventory.schema_version)
                    for instance in value.instances
                ],
            }
            for position, value in inventory.positions.items()
        },
        "warnings": list(inventory.warnings),
        "notices": list(inventory.notices),
    }


def _instance_to_dict(instance: ActualInstance, schema_version: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": instance.name,
        "full_name": instance.full_name,
        "module": instance.module,
        "file": instance.file,
        "line": instance.line,
        "parameters": dict(instance.parameters),
        "ports": {
            name: port.as_dict()
            for name, port in instance.ports.items()
        },
        "clk_sources": [
            {"instance": source.instance, "module": source.module}
            for source in instance.clk_sources
        ],
    }
    if schema_version >= 3:
        result["clock_trace"] = (
            instance.clock_trace.as_dict()
            if instance.clock_trace is not None
            else None
        )
    if schema_version >= 4:
        if instance.target_traces:
            result["target_traces"] = {
                target: {"clk": clk.as_dict(), "rst": rst.as_dict()}
                for target, (clk, rst) in instance.target_traces.items()
            }
        if result["clock_trace"] is not None:
            result["clock_trace"].setdefault("endpoints", [])
        result["reset_trace"] = instance.reset_trace.as_dict() if instance.reset_trace else None
    return result
