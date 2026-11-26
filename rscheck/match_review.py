"""Human-readable evidence from the checker report. Never re-run instance selection here."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .model import ROUTE_FIELDS, SELECTOR_POLICY, RS_INST_FUZZY_RULE, RS_INST_LEGACY_SPLIT_RULE, RS_INST_LIST_RULE, RS_INST_SPLIT_RULE


def _quote(value: str) -> str:
    # Distinguish a literal backslash/n from a newline and keep repeated spaces visible.
    return json.dumps(value, ensure_ascii=False)


def _ranking(item: Mapping[str, Any]) -> list[str]:
    count, ties, distance = item["candidate_count"], item["tied_best_count"], item["best_distance"]
    candidates = [*item["top_candidates"], *item.get("extra_tied_candidates", ())]
    lines = [f"同层同模块候选 {count} 个。按区分大小写的编辑距离比较整串本地名；增、删、改一个字符各计 1。"]
    if not candidates:
        return lines + ["未选中：没有同模块候选。"]
    if item["status"] == "matched":
        lines.append(f"唯一最近：{item['selected']['full_name']}（距离 {distance}）。")
    else:
        shown_ties = sum(c["distance"] == distance for c in candidates)
        lines.append(f"未选中：最小距离 {distance}，{ties} 个并列（显示 {shown_ties}/{ties}）。")
    lines.append(f"候选排名（显示 {len(candidates)}/{count}）：")
    for rank, candidate in enumerate(candidates, 1):
        flag = ("选中" if item["status"] == "matched" else "并列未选") if candidate["distance"] == distance else "备选"
        lines.append(f"  {rank}. [{flag}] 距离 {candidate['distance']}  {candidate['full_name']}")
    if len(candidates) < count:
        lines.append(f"  另有 {count - len(candidates)} 个候选未展示。")
    return lines


def format_match_details(record: Mapping[str, Any] | None) -> str:
    if not record:
        return "请先在检查结果中选择一条 Excel 规格。"
    spec = record["spec"]
    if spec.get("Intf_type") == "GLOBAL" and spec.get("row") == "-":
        return "全局诊断不对应单独的 Excel 匹配项，请选择一条规格。"
    scope = spec.get("position", "")
    if spec.get("position_alias"):
        scope = f"{spec['position_alias']} → {scope}"
    inst_label = "RS_inst 原文"
    if spec.get("RS_inst_formula_cell"):
        inst_label = f"RS_inst 公式结果（{spec['sheet']}!{spec['RS_inst_formula_cell']}，以保存值匹配）"
    lines = [f"{spec.get('sheet', '')} · 第 {spec.get('row', '—')} 行",
             f"层次：{scope}    期望模块：{spec.get('RS_module', '')}",
             f"{inst_label}：{_quote(spec.get('RS_inst', ''))}",
             f"RS_route 原文：{_quote(spec.get('RS_route', ''))}"]
    rule = spec.get("RS_inst_split_rule")
    if rule == RS_INST_SPLIT_RULE:
        lines.append("RS_inst 分项规则：空白（含 Tab、换行）、中英文分号、逗号、冒号及顿号；忽略空项。")
    elif rule == RS_INST_LEGACY_SPLIT_RULE:
        lines.append("RS_inst 分项规则（历史报告）：仅按分号或实际换行拆分。")
    match = record.get("instance_match")
    if not match:
        if "passed" not in record and "RS_inst_items" in spec:
            items = spec["RS_inst_items"]
            lines.append(f"RS_inst 提取 {len(items)} 项：" + ("；".join(map(_quote, items)) or "无"))
            if spec.get("selector_policy") == SELECTOR_POLICY and spec["route_group_size"] > 1:
                lines.append(f"三项组合在 {spec['route_group_size']} 行相同；只有全部名字都未命中时，"
                             f"才使用本行 RS_route2：{_quote(spec.get('RS_route2', ''))}。")
        notice = ("尚未运行 RTL 匹配；这里只显示 Excel 输入。" if "passed" not in record
                  else "旧报告没有记录匹配依据；请重新检查以获取明细。")
        return "\n".join([*lines, notice])

    mode = match["mode"]
    if mode == "none":
        if spec.get("selector_policy") == SELECTOR_POLICY:
            return "\n".join([*lines, "RS_inst 没有有效项：不使用 RS_route 或 RS_route2；未选择例化，不计拍。"])
        return "\n".join([*lines, "RS_inst 和 RS_route 均无有效值：没有选择例化；未检查拍数、实例参数及端口。"])
    if mode in ROUTE_FIELDS and "primary_attempts" in match:
        for attempt in match["primary_attempts"]:
            lines.append(f"RS_inst {_quote(attempt['query'])} 未命中：" +
                         (f"最小距离 {attempt['best_distance']}，{attempt['tied_best_count']} 项并列。"
                          if attempt['status'] == 'ambiguous' else "没有同模块候选。"))
        reason = (f"RS_module、解析后的 position、RS_route 三项在 {spec['route_group_size']} 行相同"
                  if mode == "RS_route2" else "三项组合在表内唯一，不使用 RS_route2")
        lines.append(f"使用字段：{mode}（所有 RS_inst 项均未选中；{reason}）。整格为一个查询，不拆分。")
    elif mode == "RS_route":
        lines.append("使用字段：RS_route（RS_inst 无有效项）。整格为一个查询，不拆分。")
    else:
        lines.append("使用字段：RS_inst。" + ("本行填写的 RS_route 不参与选择。" if match.get("route_ignored") else ""))
        if spec.get("selector_policy") == SELECTOR_POLICY:
            if any(f.get("code") == "RS_ROUTE2_REQUIRED" for f in record.get("findings", ())):
                lines.append(f"三项组合在 {spec['route_group_size']} 行相同，缺少可用的 RS_route2；未选中实例。")
            elif any(f.get("code") == "ROUTE_CELL_ERROR" for f in record.get("findings", ())):
                lines.extend(f["message"] for f in record["findings"] if f["code"] == "ROUTE_CELL_ERROR")
            elif any(i.get("status") == "matched" for i in match.get("items", ())) and any(
                    i.get("status") != "matched" for i in match.get("items", ())):
                lines.append("部分 RS_inst 命中、部分未命中：本行失败，不使用 RS_route 或 RS_route2 补选。")
            else:
                lines.append("RS_route2 未参与实例选择。")
        if match.get("suffix_regex"):
            lines.append(f"组后缀规则：{match['suffix_regex']}")
    instances = {inst["full_name"]: inst for inst in record.get("matched_instances", ())}
    contributions = {e["instance"]: e.get("contribution") for e in (record.get("step_check") or {}).get("contributions", ())}
    seen: set[str] = set()
    missing_position = any(f.get("code") == "POSITION_NOT_FOUND" for f in record.get("findings", ()))
    items = match["items"] if match.get("rule") == RS_INST_LIST_RULE else (match,)
    for index, item in enumerate(items, 1):
        query = item["query"]
        lines.extend(("", f"[{index}] {mode}：{_quote(query)}"))
        if missing_position or item.get("status") == "position_not_found":
            lines.append("未执行：找不到指定层次，没有查询候选。")
            continue
        ranked = mode in ROUTE_FIELDS or item.get("rule") == RS_INST_FUZZY_RULE
        if ranked:
            if mode == "RS_inst":
                lines.append("完整名和合法前缀组均未命中，改用本项原文做模糊匹配。")
            lines.extend(_ranking(item))
            chosen = item.get("selected")
            paths = (chosen["full_name"],) if chosen else ()
        else:
            paths = item.get("matched_paths", instances.keys())
            lines.append(f"按完整名或前缀加合法后缀直接命中 {len(paths)} 个实例；未用模糊匹配。")
        for path in paths:
            inst = instances[path]
            if ranked:
                reason = "唯一最近"
            elif query == inst["name"]:
                reason = "完整名相同"
            else:
                reason = f"前缀相同，后缀 {_quote(inst['name'][len(query):])} 符合规则"
            actual_module = inst.get("module")
            identity = f"定义 {actual_module or '未记录'}"
            if actual_module and actual_module != spec["RS_module"]:
                identity += "（与期望模块不同）"
            if inst.get("file"):
                identity += f"；RTL {inst['file']}" + (f":{inst['line']}" if inst.get("line") else "")
            step = contributions.get(path)
            effect = ("与前项重叠，不重复计拍" if path in seen else
                      "首次计入，贡献未确定" if step is None else f"首次计入，贡献 {step} 拍")
            lines.append(f"  {path}  [{reason}；{identity}；{effect}]")
            seen.add(path)
    rejected = [f for f in record.get("findings", ()) if f.get("code") == "INSTANCE_SUFFIX_INVALID"]
    if rejected:
        lines.extend(("", "未纳入相应前缀组的候选（后缀不符合规则）："))
        lines.extend(f"  {f.get('instance') or '—'}  {f.get('message', '')}" for f in rejected)
    expected = spec.get("step", "—")
    effective = (record.get("step_check") or {}).get("effective_step")
    lines.extend(("", f"本行去重后选中 {len(instances)} 个物理实例；有效拍数："
                      f"{'未确定' if effective is None else effective}，Excel 期望：{expected}。"))
    return "\n".join(lines)
