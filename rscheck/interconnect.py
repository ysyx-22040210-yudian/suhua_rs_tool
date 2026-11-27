"""Connectivity inside one physical Excel merge; no edge is inferred from row order."""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .model import PortConnection


def _is_seed(selector: str, name: str, link: Mapping[str, str] | None) -> bool:
    if selector == name:
        return True
    if not link or link["kind"] != "interface":
        return False
    base, dot, view = selector.rpartition(".")
    return (link["interface_type"] == selector if not dot else
            base in (name, link["interface_type"]) and view == link["modport"] and bool(view))


def build_group_graphs(rows: Iterable[Mapping[str, Any]], *, partial_design: bool = False) -> list[dict[str, Any]]:
    rows = tuple(rows)
    owners = [r for r in rows if (group := r["spec"].get("step_sum_check"))
              and ":" in group["range"].rpartition("!")[2]]
    if not owners:
        return []
    by_row = {(r["spec"]["source"], r["spec"]["sheet"], r["spec"]["row"]): r for r in rows}
    by_sheet = defaultdict(list)
    for r in rows:
        s = r["spec"]
        by_sheet[(s["source"], s["sheet"])].append(s["row"])
    for keys in by_sheet.values():
        keys.sort()
    graphs = []
    for owner in owners:
        spec = owner["spec"]
        group = spec.get("step_sum_check")
        members = [by_row[(spec["source"], spec["sheet"], m["row"])] for m in group["members"]]
        # Also validate persisted evidence against the actual active report rows.
        first, last = map(int, re.findall(r"[0-9]+", group["range"].rpartition("!")[2]))
        ordered = by_sheet[(spec["source"], spec["sheet"])]
        if ([m["row"] for m in group["members"]] != ordered[bisect_left(ordered, first):bisect_right(ordered, last)] or
                any(m["step"] != r["spec"]["step"] for m, r in zip(group["members"], members))):
            raise ValueError("merge members do not match active specification rows and steps")
        graphs.append(_build(group["range"], members, partial_design))
    return graphs


def _build(cell_range, rows, partial_design):
    nodes, issues, networks, seeds = {}, [], defaultdict(dict), set()
    member_evidence = defaultdict(lambda: defaultdict(dict))
    if partial_design:
        issues.append({"row": None, "instance": "", "message": "KDB 部分加载；未显示的端点不能视为未连接。"})
    for row in rows:
        spec = row["spec"]
        number, selector = spec["row"], spec["Intf_type"]
        def issue(instance, message):
            issues.append({"row": number, "instance": instance, "message": message})
        if not row["matched_instances"]:
            issue("", f"{spec['RS_inst'] or '未指定 RS_inst'}：没有选中实例。")
        elif any(i["status"] != "matched" for i in row["instance_match"].get("items", [])):
            issue("", "RS_inst 仅部分匹配；图中仅包括已定位的实例。")
        for inst in row["matched_instances"]:
            path, module = inst["full_name"], inst["module"]
            if module != spec["RS_module"]:
                issue(path, f"实际模块 {module} 与预期 {spec['RS_module']} 不同，未用作互联证据。")
                continue
            node = nodes.setdefault(path, {"instance": path, "module": module, "rows": []})
            node["rows"].append(number)
            ports = {n: PortConnection.from_value(p) for n, p in inst["ports"].items()}
            if not any(p.link is not None for p in ports.values()):
                issue(path, "无端口互联证据：请用本版本 collector 重新采集，或检查 KDB 是否可查询端口。")
                continue
            found = False
            for name, port in ports.items():
                link = port.link
                seed = _is_seed(selector, name, link)
                found |= seed
                if not link or not link["identity"]:
                    if seed:
                        issue(path, f"{name}：无法证明连接对象（{port.connection or '无实际连接'}）。")
                    continue
                key = link["kind"], link["identity"]
                # A physical port may be encountered through multiple spec rows.
                pin = networks[key].setdefault((path, name), {
                    "instance": path, "port": name, "direction": link["direction"],
                    "interface_type": link["interface_type"], "modport": link["modport"],
                    "connection": port.connection, "selected_by": []})
                if seed:
                    seeds.add(key)
                    pin["selected_by"].append(number)
                for pair in port.member_pairs or ():
                    member_evidence[pair][link["identity"]][(path,name)] = pin
            if not found:
                issue(path, f"Intf_type={selector!r} 未匹配到端口名、interface 类型或指定的 modport。")

    nets = []
    for kind, identity in sorted(seeds):
        pins = networks[(kind, identity)]
        ordered = [pins[k] for k in sorted(pins)]
        # Fanout and shared inputs are a *single* bus/net, not a clique or a
        # fabricated chain of adjacent instances. Modports have no bus direction.
        nets.append({"kind": kind, "identity": identity, "pins": ordered,
                     "shared": len({p["instance"] for p in ordered}) >= 2})
    # Whole-bundle bindings above are independent from individual assignments
    # across DIFFERENT interface instances. Only join reciprocal end evidence
    # inside this Excel merge; never manufacture a full-interface edge.
    for pair, sides in sorted(member_evidence.items(), key=lambda item: item[0].path):
        src, dst = (p.rpartition(".")[0] for p in (pair.source,pair.target))
        if src not in sides or dst not in sides or not any(("interface",i) in seeds for i in (src,dst)):
            continue
        pins = []
        for bundle,full,direction in ((src,pair.source,"output"),(dst,pair.target,"input")):
            for _,base in sorted(sides[bundle].items()):
                pins.append({**base, "formal":base["port"], "port":base["port"]+"."+full.rpartition(".")[2],
                             "direction":direction, "connection":full})
        if len({p["instance"] for p in pins})>1:
            nets.append({"kind":"member","identity":pair.source+" → "+pair.target,
                         "path":list(pair.path),"pins":pins,"shared":True})
    return {"range": cell_range, "sheet": rows[0]["spec"]["sheet"],
            "rows": [r["spec"]["row"] for r in rows], "nodes": list(nodes.values()),
            "nets": nets, "diagnostics": issues,
            "shared_count": sum(n["shared"] for n in nets)}


@dataclass(frozen=True)
class Wire:
    """One drawable RS-to-RS connection, or one physical multi-terminal binding."""
    kind: str
    identity: str
    mode: str  # directed, binding, multidrop
    pins: tuple
    path: tuple = ()

    @property
    def label(self):
        if self.kind == "member":
            if self.mode == "directed":
                return " → ".join(".".join(p["connection"].rsplit(".",2)[-2:]) for p in self.pins)
            return self.identity
        # An equivalent scalar net's canonical name can be an internal alias
        # of the sink. Show the wire the engineer sees on the driving port.
        if self.mode == "directed":
            return self.pins[0]["connection"] or self.identity
        return self.identity


def instance_wires(nets):
    """Project the original KDB evidence onto RS ports. Never generate a clique."""
    wires, notes = [], []
    for net in sorted(nets, key=lambda n: (n["kind"] != "signal", n["identity"])):
        pins = net["pins"]
        if not net["shared"]:
            continue  # one physical RS, even if several of its ports are present
        kind, identity = net["kind"], net["identity"]
        anchor = None
        mode = "binding"
        if kind in ("signal","member"):
            outputs = [p for p in pins if p["direction"] == "output"]
            inputs = sum(p["direction"] == "input" for p in pins)
            if inputs == len(pins):
                notes.append(f"{identity}：组内只有输入端共享此信号，未找到组内驱动；不画输入到输入的连线。")
                continue
            if len(outputs) == 1 and inputs == len(pins) - 1:
                anchor, mode = outputs[0], "directed"
            else:
                if kind == "signal":
                    notes.append(f"{identity}：多驱动、双向或端口方向不明；仅显示同网绑定，不指定驱动者。")
                else:
                    # Several RS may expose the same source-side bundle: the
                    # assign direction is known, the individual driver is not.
                    notes.append(f"{identity}：多个 RS 共绑源侧接口；成员赋值方向已知，不能唯一判定驱动 RS。")
        else:
            # A unique master is a useful drawing anchor, not a bus-wide driver.
            masters = [p for p in pins if p["modport"].lower() in ("mst", "master")]
            if len(masters) == 1:
                anchor = masters[0]
        if anchor is None and len(pins) == 2:
            anchor = pins[0]  # unordered two-terminal binding
        if anchor is None:
            wires.append(Wire(kind, identity, "multidrop", tuple(pins), tuple(net.get("path",()))))
        else:
            for peer in pins:
                if peer["instance"] != anchor["instance"]:
                    wires.append(Wire(kind, identity, mode, (anchor, peer), tuple(net.get("path",()))))
    return wires, notes


def schematic_columns(wires):
    """Place chains left to right. Feedback wires stay real and take a return lane."""
    neighbors = {}
    for wire in wires:
        left, right = (p["instance"] for p in wire.pins)
        neighbors.setdefault(left, {})[right] = None
        neighbors.setdefault(right, {})
    visited, finished = set(), []
    for root in neighbors:
        if root in visited:
            continue
        stack = [(root, iter(neighbors[root]))]
        visited.add(root)
        while stack:
            current, remaining = stack[-1]
            other = next(remaining, None)
            if other is None:
                finished.append(current)
                stack.pop()
            elif other not in visited:
                visited.add(other)
                stack.append((other, iter(neighbors[other])))
    order = {name: i for i, name in enumerate(reversed(finished))}
    level = dict.fromkeys(neighbors, 0)
    for name in order:
        for peer in neighbors[name]:
            if order[peer] > order[name]:
                level[peer] = max(level[peer], level[name] + 1)
    columns = defaultdict(list)
    for name in neighbors:
        columns[level[name]].append(name)
    return [columns[i] for i in range(len(columns))]
