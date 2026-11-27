"""Independent, fixed-size oracle for the scale-grid KDB and Excel workbook (Python 3.8+)."""
import argparse
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BOOK = ROOT / "tests/fixtures/scale_grid.xlsx"
TILES, LANES = 96, 32


def require(condition, message):
    if not condition:
        raise ValueError(message)


def base(i):
    return "stress_top.banks[{}].tile".format(i)


def prepare(out):
    from rscheck.config import load_config
    from rscheck.excel_reader import read_spec_rows
    with ZipFile(BOOK) as z:
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    formulas = [c for c in sheet.findall(".//{*}c") if c.find("{*}f") is not None]
    require(len(formulas) == 577 and all(c.findtext("{*}v") for c in formulas), "missing saved Excel results")
    require({r.get("ref") for r in sheet.findall(".//{*}mergeCell")} ==
            {"M{}:M{}".format(3+5*i, 5+5*i) for i in range(TILES)}, "wrong physical Excel merges")
    cfg = json.loads((ROOT / "config/rscheck.example.json").read_text(encoding="utf-8"))
    cfg["excel"]["sheet"] = "Scale"
    cfg["columns"] = dict(Intf_type=2, position=3, RS_module=4, RS_inst=5, step=6,
                          clk=7, rst=8, CRG_source=9, RS_CFG_EN=10, RS_route=11, RS_route2=12, step_sum=13)
    cfg["position_mappings"] = {"ROOT": "stress_top"}
    for i in range(TILES):
        cfg["position_mappings"]["T%03d" % i] = base(i)
        cfg["position_mappings"]["D%03d" % i] = base(i)+".inner"
    cfg["crg_source_mappings"] = {"SOURCE": "stress_top.src"}
    cfg["module_rules"] = {m: {"has_rs_cfg_en": m == "qa_rs", "step_parameters": ["rs_mode"] if m == "qa_rs" else [],
                               "clk_port": "clk", "rst_port": "rst_n"} for m in ("qa_rs","qa_tx","qa_rx")}
    cfg["rtl"]["trace_cells"] = []
    path = out / "config.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    config = load_config(path)
    skipped = []
    rows = read_spec_rows(BOOK, config.excel, config.position_mappings, config.crg_source_mappings,
                          on_skip=skipped.append)
    require(len(rows) == 385 and {r.row for r in skipped} == {4+5*i for i in range(TILES)}, "Excel selection")
    require(all(r.rs_inst_formula_cell for r in rows), "selector cache provenance missing")
    require([r.step_sum_check.as_dict()["actual"] for r in rows if r.step_sum_check and r.step_sum_check.merged]
            == [2]*TILES, "skipped numeric row contributed to subtotal")
    return {"data_rows":481, "active":385, "skipped":96, "formulas":577, "merges":96, "boundaries":193}


def audit(report):
    from rscheck.gui_backend import load_report
    doc = load_report(report)
    s = doc.summary
    require((s["rows"], s["passed_rows"], s["skipped_rows"], s["errors"], s["warnings"]) == (385,385,96,0,0),
            "scale result: {} ; first findings: {}".format(s, [f["code"] for r in doc.rows for f in r["findings"]][:8]))
    actual, modes, effective = set(), [], 0
    by_row = {r["spec"]["row"]: r for r in doc.rows}
    def matches(n, exp, step):
        nonlocal effective
        row = by_row[n]
        paths = [p["full_name"] for p in row["matched_instances"]]
        require(len(paths) == len(exp) and set(paths) == exp and actual.isdisjoint(exp), "wrong ownership row %d" % n)
        require(row["step_check"]["effective_step"] == step, "wrong step row %d" % n)
        actual.update(exp)
        effective += step
        return row
    matches(2, {base(i)+".inner.LEFT" for i in range(TILES)}, TILES)
    groups = {g["range"]: g for g in doc.raw["interconnect_groups"]}
    require(len(groups) == TILES, "wrong group count")
    for i in range(TILES):
        n, prefix = 3+5*i, base(i)
        tx = matches(n, {prefix+".TX"}, 1)
        matches(n+2, {prefix+".RX"}, 1)
        pipe = matches(n+3, {prefix+".lanes[%d].PIPE" % j for j in range(LANES)}, LANES//2)
        matches(n+4, {prefix+".inner.OWN"}, 1)
        require({c["instance"]:c["contribution"] for c in pipe["step_check"]["contributions"]} ==
                {prefix+".lanes[%d].PIPE" % j:j % 2 for j in range(LANES)}, "rs_mode parity %d" % i)
        require(tx["source_signal_status"] == "pass" and len(tx["source_signal_checks"]) == 2
                and tx["crg_source_check"]["status"] == "pass", "clock/reset source %d" % i)
        g = groups["Scale!M{}:M{}".format(n,n+2)]
        require(g["rows"] == [n,n+2] and not g["diagnostics"] and len(g["nets"]) == g["shared_count"] == 1,
                "unexpected merge graph %d" % i)
        net, = g["nets"]
        require(net["kind"] == "signal" and
                {(p["instance"], p["port"], p["direction"]) for p in net["pins"]} ==
                {(prefix+".TX","link","output"),(prefix+".RX","link","input")}, "wrong physical link %d" % i)
        modes.append([i, net["identity"]])
    proof = json.dumps([sorted(actual), effective, modes], ensure_ascii=True, separators=(",",":")).encode()
    return {"unique_rs":len(actual), "effective_steps":effective, "independent_links":len(groups),
            "source_signal_checks":TILES*2, "sha256":hashlib.sha256(proof).hexdigest()}


def fuzzy():
    from rscheck.checker import check_specs
    from rscheck.model import ActualInstance, Inventory, ModuleRule, PositionInventory, RtlConfig, SpecRow
    from rscheck.position_bounds import SUBTREE_SCOPE
    pool, queries = 4096, 64
    names = ["CHANNEL_%04X_X" % i for i in range(pool)]
    instances = tuple(ActualInstance(n, "wide."+n, "ff", "", None, {}, {}, ()) for n in names)
    inv = Inventory({"wide":PositionInventory(True,instances)},schema_version=4,position_scope=SUBTREE_SCOPE)
    wanted = [names[(i*59) % pool] for i in range(queries)]
    specs = [SpecRow(BOOK,"fuzzy",i+2,"data","ff",n[:-1]+"Y","wide",1,"NA","NA","NA","NA")
             for i,n in enumerate(wanted)]
    rep = check_specs(specs, inv, RtlConfig(), {"ff":ModuleRule("ff",False)})
    require(rep.error_count == rep.warning_count == 0 and len(rep.rows) == queries, "fuzzy findings")
    for row,n in zip(rep.rows,wanted):
        ev = row.nearest_match
        require([x.name for x in row.instances] == [n] and ev.candidate_count == pool and
                ev.tied_best_count == ev.ranked[0].distance == 1, "wrong nearest: "+n)
    return {"pool":pool, "selectors":queries, "distances_checked":pool*queries}


def fault(out):
    # Replay the same real NPI snapshot with deliberately bad expectations.
    from dataclasses import replace
    from rscheck.config import load_config
    from rscheck.excel_reader import read_spec_rows
    from rscheck.inventory import load_inventory
    from rscheck.checker import check_specs
    config = load_config(out/"config.json")
    rows = read_spec_rows(BOOK, config.excel, config.position_mappings, config.crg_source_mappings)
    wrong = {2:dict(step=95), 3:dict(clk="bad_clock",rst="bad_reset"), 5:dict(step=2),
             6:dict(rs_cfg_en="wrong"), 7:dict(clk="")}
    altered = [replace(r,**wrong[r.row_number]) if r.row_number in wrong else r for r in rows]
    rep = check_specs(altered, load_inventory(out/"grid-inventory.json"), config.rtl, config.module_rules)
    result = {r.spec.row_number:sorted({f.code for f in r.findings}) for r in rep.rows if r.findings}
    require({r.spec.row_number for r in rep.rows if not r.passed} == {2,3,5,6}, "unexpected failing rows: "+str(result))
    require(7 in result and rep.warning_count > 0 and rep.status == "FAIL", "blank not reported")
    return {"bad_rows":[2,3,5,6],"warning_rows":[7],"diagnostics":result}


def owned_group(pgid):
    """Linux-only observation: never signal or match another project's processes."""
    result = []
    for f in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = f.read_bytes().rsplit(b") ",1)[1].split()
            if int(fields[2]) == pgid:  # state, ppid, pgrp
                pid = int(f.parent.name)
                result.append((pid, fields[0], os.path.basename(os.readlink(str(f.parent/"exe")))
                               if fields[0] != b"Z" else "<zombie>"))
        except (OSError, IndexError, ValueError):
            continue  # exited while sampling /proc
    return result


def excel20k(out):
    """Real XLSX + GUI validation (not an RTL or offline inventory test). Inspect every displayed row."""
    import tkinter as tk
    from unittest.mock import patch
    from rscheck.config import load_config
    from rscheck.gui import RsCheckApp
    book = BOOK.with_name("scale_20k.xlsx")
    cells = merges = 0
    with ZipFile(book) as z, z.open("xl/worksheets/sheet1.xml") as xml:
        for _, el in ET.iterparse(xml):
            tag = el.tag.rpartition("}")[2]
            if tag == "c":
                if el.find("{*}f") is not None:
                    require(bool(el.findtext("{*}v")), "absent formula cache "+str(el.get("r")))
                    cells += 1
                el.clear()
            elif tag == "mergeCell":
                merges += 1
    require((cells, merges) == (20500, 500), "physical XLSX counts")
    root = tk.Tk()
    try:
        app = RsCheckApp(root)
        cfg = out/"config.json"
        app._apply_config(load_config(cfg), cfg, "stress")
        app.excel_var.set(str(book))
        root.update()
        require(root.winfo_ismapped(), "Tk not visible")
        start = time.monotonic()
        with patch("rscheck.gui.messagebox.showerror") as errors:
            app.validate_button.invoke()
            while app.running and time.monotonic()-start < 300:
                root.update()
                time.sleep(.02)
            require(not app.running and not errors.called and app.summary_state_var.get() == "VALID",
                    "large XLSX GUI: {} {}".format(app.summary_state_var.get(),errors.call_args))
        ready = time.monotonic()-start
        records = app._result_records
        require(len(records)==19500 and len(app.result_tree.get_children())==19500 and
                app.summary_rows_var.get()=="行数 19500 · 跳过 500" and
                app.summary_warnings_var.get()=="警告 0" and app.summary_errors_var.get()=="错误 0",
                "large XLSX displayed totals")
        ids, groups = set(), 0
        for record in records.values():
            s = record["spec"]
            n = s["row"]
            ids.add(n)
            require(s["RS_inst"]=="PIPE_%05d" % (n-2) and s["RS_inst_formula_cell"] and
                    s["step"]==1 and not record["findings"], "incorrect formula row %d" % n)
            check = s.get("step_sum_check")
            if (n-2)%40 == 0:
                require(check and check["status"]=="pass" and check["actual"]==check["expected"]==39 and
                        check["members"]==[{"row":k,"step":1} for k in range(n,n+39)], "wrong merged subtotal %d" % n)
                groups += 1
            else:
                require(not check, "double-counted merge")
        require(ids=={n for n in range(2,20002) if (n-2)%40 != 39} and groups==500,"missing active/skip records")
        return {"input_rows":20000,"cached_formulas":cells,"merges":groups,"validated_rows":len(ids),
                "skipped_rows":500,"gui_ready_s":round(ready,3),"xlsx_bytes":book.stat().st_size,
                "xlsx_sha256":hashlib.sha256(book.read_bytes()).hexdigest()}
    finally:
        for timer in root.tk.call("after","info"):
            root.after_cancel(timer)
        root.destroy()


def cancel_live(app, root, out, collector):
    """Actually observe the native ELF in the GUI's own process group before clicking Cancel."""
    durations = []
    for n in range(12):
        path = out/("cancel-%02d.json" % n)
        require(not path.exists(), "fresh work directory required")
        app.json_report_var.set(str(path))
        app.run_button.invoke()
        until = time.monotonic()+30
        while time.monotonic() < until:
            root.update()
            pg = app.controller._process_group_id
            if pg and any(exe == collector.name for _,_,exe in owned_group(pg)):
                break
            time.sleep(.02)
        else:
            raise ValueError("live collector not observed before cancel %d" % n)
        start = time.monotonic()
        app.cancel_button.invoke()
        while time.monotonic()-start < 12:
            root.update()
            if not app.running and not app.controller.is_running and not owned_group(pg):
                break
            time.sleep(.02)
        require(not app.running and not app.controller.is_running and not owned_group(pg) and
                app.summary_state_var.get() == "CANCELLED" and not path.exists(), "cancel failed %d" % n)
        durations.append(time.monotonic()-start)
    return {"cancelled_while_native_running":len(durations), "residual_group_members":0,
            "max_cancel_s":round(max(durations),3)}


def gui(out, collector, kdb, with_cancel=False):
    import tkinter as tk
    from unittest.mock import patch
    from rscheck.config import load_config
    from rscheck.gui import RsCheckApp
    from rscheck.gui_backend import LIVE_SOURCE
    root = tk.Tk()
    try:
        app = RsCheckApp(root)
        cfg = out/"config.json"
        app._apply_config(load_config(cfg), cfg, "stress")
        app.excel_var.set(str(BOOK))
        app.json_report_var.set(str(out/"grid-gui.json"))
        app.csv_report_var.set("")
        app.keep_inventory_var.set("")
        app.source_mode_var.set(LIVE_SOURCE)
        app.collector_var.set(str(collector))
        app.elab_db_var.set(str(kdb))
        app._update_source_mode()
        root.update()
        require(root.winfo_ismapped(), "Tk not mapped")
        with patch("rscheck.gui.messagebox.showerror") as errors:
            cancel_info = cancel_live(app, root, out, collector) if with_cancel else {}
            app.json_report_var.set(str(out/"grid-gui.json"))
            start = time.monotonic()
            app.run_button.invoke()
            while app.running and time.monotonic()-start < 420:
                root.update()
                time.sleep(.01)
            require(not app.running and not errors.called and app.summary_state_var.get() == "PASS",
                    "live GUI failed: {} {}".format(app.summary_state_var.get(), errors.call_args))
        ready = time.monotonic()-start
        view = app.connection_view
        app.notebook.select(app.results_tab)
        app.inspector_tabs.select(view)
        delays = []
        for i in range(TILES):
            t = time.monotonic()
            app.result_tree.selection_set("row-%d" % (1+4*i))
            app._on_result_selected()
            root.update()
            require(view.canvas.winfo_ismapped() and view.group_no == (i+1,TILES)
                    and len(view.canvas.find_withtag("signal")) == 1, "missing canvas wire %d" % i)
            require({n["instance"] for n in view.graph["nodes"]} == {base(i)+".TX",base(i)+".RX"}, "canvas leaked group")
            delays.append(time.monotonic()-t)
        return {"live_gui_ready_s":round(ready,3), "canvas_selections":len(delays),
                "canvas_max_s":round(max(delays),3), "canvas_median_s":round(sorted(delays)[len(delays)//2],3),
                **audit(out/"grid-gui.json"), **cancel_info}
    finally:
        for timer in root.tk.call("after","info"):
            root.after_cancel(timer)
        root.destroy()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("prepare","audit","fuzzy","fault","gui","cancel","excel20k"))
    p.add_argument("out", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--collector", type=Path)
    p.add_argument("--kdb", type=Path)
    args = p.parse_args()
    if args.action == "audit":
        result = audit(args.report)
    elif args.action in ("gui", "cancel"):
        result = gui(args.out, args.collector, args.kdb, with_cancel=args.action == "cancel")
    elif args.action == "fuzzy":
        result = fuzzy()
    else:
        result = {"prepare":prepare,"fault":fault,"excel20k":excel20k}[args.action](args.out)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
