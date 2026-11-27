from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rscheck.checker import check_specs
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui import RsCheckApp, tk
from rscheck.gui_backend import GuiReportError, INVENTORY_SOURCE, LIVE_SOURCE, load_report
from rscheck.inventory import inventory_to_dict, load_inventory
from rscheck.interconnect import build_group_graphs, instance_wires, schematic_columns
from rscheck.model import ActualInstance, InterfaceMemberPair, Inventory, InventoryError, PortConnection, PositionInventory
from rscheck.reporting import report_to_dict, write_json_report
from tests.test_excel_reader import _xlsx
from tests.test_step_sum import spec, text_cell, num

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/interconnect_inventory.json"


def raw_net(name, pins, kind="signal"):
    return {"kind":kind, "identity":name, "shared":len({p[0] for p in pins})>1,
            "pins":[{"instance":node,"port":port,"direction":role if kind=="signal" else "unknown",
                     "modport":role if kind=="interface" else "", "interface_type":"if_t" if kind=="interface" else "",
                     "selected_by":[], "connection":name} for node,port,role in pins]}


def sample(path):
    """Independent OOXML fixture with two real merges and one excluded member."""
    rows = [("data_o","rs_send","tx","link_top"), ("bus_if.slv","rs_recv","rx","link_top"),
            ("bus_if","rs_watch","observe","link_top"), ("bus_if.mst","rs_send","NA","NA"),
            ("bus_if","rs_recv","other","link_top"), ("data_i","rs_recv","leaf","link_top.wrap"),
            ("bus_if.slv","rs_generic","explicit_view","link_top"), ("sink","rs_wire","peer","link_top"),
            ("bus_if.mst","rs_send","tx","link_top"), ("data_i","rs_recv","rx","link_top")]
    # Different merges may not claim the same real RS; second group deliberately
    # uses a missing instance/position and is exercised separately for isolation.
    cells = {}
    for n, (selector, module, inst, pos) in enumerate(rows, 2):
        if n >= 10:
            pos = "not_in_first_group"
        cells.update(spec(n, inst, 1))
        for col, val in (("A", selector), ("B", module), ("D", pos), ("J", "NA"), ("K", "NA")):
            address = f"{col}{n}"
            cells[address] = text_cell(address, val)
    cells.update(L2=num("L2", 7), L10=num("L10", 2))
    _xlsx(path, cell_overrides=cells, merge_refs=("L2:L9", "L10:L11"))


BUNDLE = "link_top.bundled"
# instance, position, formal port, actual bundle, Excel selector
WHOLE = (("first", BUNDLE, "channel","lane_pair","whole_if"),
         ("leaf", BUNDLE+".hop", "port_if","lane_pair","port_if"),
         ("separate", BUNDLE, "channel","lane_other","channel"),
         ("a", BUNDLE, "channel","lane_shared","whole_if"),
         ("b", BUNDLE, "port_if","lane_shared","port_if"),
         ("c", BUNDLE, "port_if","lane_shared","whole_if"),
         ("producer", BUNDLE, "channel","outbound","whole_if"),
         ("consumer", BUNDLE, "port_if","inbound","whole_if"),
         ("bystander", BUNDLE, "channel","unrelated","whole_if"),
         ("plain", BUNDLE, "channel","plain_view","whole_if"),
         ("restricted", BUNDLE, "channel","restricted_view","limited_if.rx"))
MEMBER_PATHS = (("outbound.request","inbound.request"),
                ("inbound.reply","outbound.reply"),
                ("outbound.payload","data_bridge","inbound.result"))
MIXED_PATH=("plain_view.request","restricted_view.request")


def whole_fixture(book):
    """Independent expected port evidence; the VM integration uses actual KDB instead."""
    cells, positions = {}, {}
    pairs=[]
    for trail in MEMBER_PATHS+(MIXED_PATH,("plain_view.reply","restricted_view.hidden")):
        path=tuple(BUNDLE+"."+s for s in trail)
        pairs.append(InterfaceMemberPair(path[0],path[-1],path))
    for row,(inst,pos,port,bundle,selector) in enumerate(WHOLE,2):
        module = "rs_limited" if inst=="restricted" else "rs_whole_left" if port=="channel" else "rs_whole_right"
        cells.update(spec(row,inst,1))
        for col,val in (("A",selector),("B",module),("D",pos),("J","NA"),("K","NA")):
            cells[f"{col}{row}"]=text_cell(f"{col}{row}",val)
        actual=BUNDLE+"."+bundle
        proof=tuple(p for p in pairs if actual in (p.source.rpartition(".")[0],p.target.rpartition(".")[0]))
        view = inst=="restricted"
        if view:
            proof=tuple(p for p in proof if p.target.endswith(".request"))
        link={"kind":"interface","identity":actual,"interface_type":"limited_if" if view else "whole_if",
              "modport":"rx" if view else "", "direction":"unknown"}
        pin=PortConnection(actual,"npiInterface",link,proof)
        positions.setdefault(pos,[]).append(ActualInstance(inst,pos+"."+inst,module,"",None,{}, {port:pin},()))
    cells.update(L2=num("L2",3),L5=num("L5",3),L8=num("L8",3),L11=num("L11",2))
    _xlsx(book,cell_overrides=cells,merge_refs=("L2:L4","L5:L7","L8:L10","L11:L12"))
    return Inventory({pos:PositionInventory(True,tuple(items)) for pos,items in positions.items()},schema_version=4)


class InterconnectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.book = self.tmp / "links.xlsx"
        sample(self.book)
        self.cfg = load_config(ROOT / "config/rscheck.example.json")
        self.rows = read_spec_rows(self.book, replace(self.cfg.excel, columns={**self.cfg.excel.columns, "step_sum":12}))

    def check(self, inventory=None, rows=None):
        return check_specs(self.rows if rows is None else rows, inventory or load_inventory(FIXTURE),
                           self.cfg.rtl, self.cfg.module_rules)

    def assert_graph(self, report):
        g, g2 = report["interconnect_groups"]
        self.assertEqual(g["rows"], [2,3,4,6,7,8,9])
        self.assertEqual(len(g["nodes"]), 7)
        self.assertEqual(g["shared_count"], 2)
        self.assertFalse(g["diagnostics"])
        self.assertFalse(g2["nodes"])
        self.assertTrue(g2["diagnostics"])
        nets = {n["identity"]: n for n in g["nets"] if n["kind"] == "interface"}
        self.assertEqual(set(nets), {"link_top.fabric","link_top.separate"})
        self.assertFalse(nets["link_top.separate"]["shared"])
        view = {p["instance"]:(p["modport"],p["selected_by"]) for p in nets["link_top.fabric"]["pins"]}
        self.assertEqual(view, {"link_top.tx":("mst",[]), "link_top.rx":("slv",[3]),
                                "link_top.observe":("",[4]), "link_top.wrap.leaf":("slv",[]),
                                "link_top.explicit_view":("slv",[8])})
        signal, = [n for n in g["nets"] if n["kind"] == "signal"]
        pins = {(p["instance"],p["port"]):p for p in signal["pins"]}
        self.assertEqual(pins[("link_top.tx", "data_o")]["direction"], "output")
        self.assertEqual(pins[("link_top.rx", "data_i")]["selected_by"], [])
        self.assertEqual(pins[("link_top.wrap.leaf", "data_i")]["connection"], "link_top.wrap.forwarded")
        self.assertNotIn(("link_top.other", "data_i"),pins)
        self.assertEqual(len(pins), 4)

    def test_real_inventory_scalar_alias_across_wrapper_interface_and_views(self):
        self.assert_graph(report_to_dict(self.check()))
        rows = [replace(s, intf_type="bus_if.mst") if s.row_number == 2 else s for s in self.rows]
        net = report_to_dict(self.check(rows=rows))["interconnect_groups"][0]["nets"][0]
        self.assertTrue(any(p["modport"] == "mst" and p["selected_by"] == [2] for p in net["pins"]))

    def test_direct_rs_ports_fanout_and_interface_views(self):
        g = report_to_dict(self.check())["interconnect_groups"][0]
        wires, notes = instance_wires(g["nets"])
        self.assertFalse(notes)
        def endpoints(w):
            return tuple((p["instance"],p["port"],p["modport"]) for p in w.pins)
        self.assertEqual({endpoints(w) for w in wires if w.kind=="signal"},
                         {(("link_top.tx","data_o",""), (n,p,"")) for n,p in
                          (("link_top.rx","data_i"), ("link_top.wrap.leaf","data_i"), ("link_top.peer","sink"))})
        self.assertEqual({endpoints(w) for w in wires if w.kind=="interface"},
                         {(("link_top.tx","bus","mst"), (n,"bus",v)) for n,v in
                          (("link_top.rx","slv"),("link_top.wrap.leaf","slv"),
                           ("link_top.explicit_view","slv"),("link_top.observe",""))})
        self.assertEqual({(w.kind,w.mode,w.label) for w in wires},
                         {("signal","directed","link_top.shared_data"), ("interface","binding","link_top.fabric")})
        identity, = {n["identity"] for n in g["nets"] if n["kind"]=="signal"}
        self.assertTrue(all(w.identity==identity for w in wires if w.kind=="signal"))

    def test_chain_parallel_channels_and_feedback_stay_distinct(self):
        ab=raw_net("ab",[("A","send","output"),("B","receive","input")])
        bc=raw_net("bc",[("B","send","output"),("C","receive","input")])
        another=raw_net("parallel",[("A","flag","output"),("B","ready","input")])
        wires,_=instance_wires([bc,another,ab])
        self.assertEqual(schematic_columns(wires),[["A"],["B"],["C"]])
        self.assertEqual({(w.identity,tuple(p["instance"] for p in w.pins)) for w in wires},
                         {("ab",("A","B")),("parallel",("A","B")),("bc",("B","C"))})
        feedback=raw_net("return",[("C","out","output"),("A","back","input")])
        wires,_=instance_wires([ab,bc,feedback])
        self.assertEqual([p["instance"] for p in wires[-1].pins],["C","A"])
        self.assertEqual(sorted(n for c in schematic_columns(wires) for n in c),["A","B","C"])

    def test_ambiguous_networks_remain_physical_bindings_without_invented_drivers(self):
        for kind,pins,expected in (
            ("signal",[("A","p","output"),("B","p","output"),("C","q","input")],"multidrop"),
            ("signal",[("A","p","inout"),("B","q","input")],"binding"),
            ("signal",[("A","p","unknown"),("B","q","unknown")],"binding"),
            ("interface",[("A","p","slv"),("B","q","slv"),("C","r","")],"multidrop"),
            ("interface",[("A","p","mst"),("B","q","mst"),("C","r","slv")],"multidrop"),
            ("interface",[("A","p",""),("B","q","")],"binding")):
            with self.subTest(kind=kind,pins=pins):
                net=raw_net("top.bus",pins,kind)
                wires,_=instance_wires([net])
                self.assertEqual(len(wires),1)
                self.assertEqual((wires[0].mode,list(wires[0].pins)),(expected,net["pins"]))
        same=raw_net("loop",[("A","out","output"),("A","in","input")])
        self.assertFalse(instance_wires([same])[0])

    def test_no_connections_guessed_from_legacy_constants_or_row_order(self):
        inv = load_inventory(FIXTURE)
        def changes(inst):
            return replace(inst, ports={k:PortConnection("1'b1", "npiConstant",
                       {**p.link, "identity":""} if inst.name != "rx" and p.link else None)
                       for k,p in inst.ports.items()})
        pos = {k:replace(v,instances=tuple(changes(i) for i in v.instances)) for k,v in inv.positions.items()}
        g = report_to_dict(self.check(replace(inv, positions=pos)))["interconnect_groups"][0]
        self.assertFalse(g["nets"])
        self.assertTrue(any("重新采集" in d["message"] for d in g["diagnostics"]))
        self.assertTrue(any("无法证明" in d["message"] for d in g["diagnostics"]))

    def test_matched_zero_step_stage_remains_a_physical_endpoint(self):
        inv = load_inventory(FIXTURE)
        positions = {k:replace(v, instances=tuple(replace(i, parameters={"mode":"0" if i.name == "tx" else "1"})
                                                   for i in v.instances)) for k,v in inv.positions.items()}
        from rscheck.model import ModuleRule
        rules = {s.rs_module:ModuleRule(s.rs_module, False, ("mode",)) for s in self.rows}
        rows = list(self.rows)
        group = rows[0].step_sum_check
        rows[0] = replace(rows[0], step=0, step_sum_check=replace(group, expected=6,
                          members=tuple((n, 0 if n==2 else v) for n,v in group.members)))
        result = check_specs(rows, replace(inv, positions=positions), self.cfg.rtl, rules)
        self.assertEqual(result.rows[0].effective_step, 0)
        net = next(n for n in report_to_dict(result)["interconnect_groups"][0]["nets"] if n["kind"]=="signal")
        self.assertIn("link_top.tx", [p["instance"] for p in net["pins"]])

    def test_selector_typo_is_diagnostic_not_a_fuzzy_port_or_type_selection(self):
        rows = [replace(s, intf_type="Bus_if.Mst") for s in self.rows]
        raw = report_to_dict(self.check(rows=rows))
        group = raw["interconnect_groups"][0]
        self.assertFalse(group["nets"])
        self.assertEqual(len(group["diagnostics"]), 7)
        self.assertTrue(all("未匹配" in d["message"] for d in group["diagnostics"]))

    def test_unmapped_singleton_and_group_with_na_total(self):
        rows = [replace(s,step_sum_check=None) for s in self.rows]
        self.assertNotIn("interconnect_groups", report_to_dict(self.check(rows=rows)))
        rows[0] = replace(rows[0],step_sum_check=replace(self.rows[0].step_sum_check,cell_range="RS_Check!L2",
                                                          members=((2,1),)))
        self.assertNotIn("interconnect_groups", report_to_dict(self.check(rows=rows)))
        rows[0] = replace(self.rows[0],step_sum_check=replace(self.rows[0].step_sum_check,expected="NA"))
        self.assertEqual(report_to_dict(self.check(rows=rows))["interconnect_groups"][0]["shared_count"],2)

    def test_auditable_report_roundtrip_rejects_fabricated_edge(self):
        inv = load_inventory(FIXTURE)
        payload = self.tmp / "inv.json"
        payload.write_text(json.dumps(inventory_to_dict(inv)),encoding="utf-8")
        self.assertEqual(inv, load_inventory(payload))
        report_path = write_json_report(self.check(), self.tmp / "view.json")
        raw = dict(load_report(report_path).raw)
        self.assert_graph(raw)
        raw["interconnect_groups"][0]["nets"][0]["pins"][0]["instance"] = "unrelated"
        report_path.write_text(json.dumps(raw),encoding="utf-8")
        with self.assertRaisesRegex(GuiReportError,"interconnect_groups"):
            load_report(report_path)

    def test_large_fanout_is_one_shared_net_not_quadratic_pairs(self):
        port={"connection":"top.n","type":"npiNet","link":{"kind":"signal","identity":"top.n","direction":"input",
                                                         "interface_type":"","modport":""}}
        count=650
        rows=[{"spec":{"sheet":"S","source":"f","row":n+2,"step":1,"Intf_type":"d","RS_inst":f"x{n}","RS_module":"rs"},
               "instance_match":{}, "matched_instances":[{"full_name":f"top.x{n}","module":"rs","ports":{"d":port}}]}
              for n in range(count)]
        rows[0]["spec"]["step_sum_check"]={"range":f"S!L2:L{count+1}","members":[{"row":i+2,"step":1} for i in range(count)]}
        g,=build_group_graphs(rows)
        self.assertEqual((len(g["nodes"]),len(g["nets"]),len(g["nets"][0]["pins"])),(count,1,count))
        wires,notes=instance_wires(g["nets"])
        self.assertFalse(wires)  # a shared input is not an RS-to-RS driver
        self.assertIn("只有输入",notes[0])
        g["nets"][0]["pins"][0]["direction"]="output"
        self.assertEqual(len(instance_wires(g["nets"])[0]),count-1)

    def test_many_disjoint_merges_using_the_same_actual_interface(self):
        # Every group intentionally reuses the very same RTL interface object
        # and module type. Only the physical Excel merge limits the endpoints.
        count = 256
        pin = PortConnection("top.fabric","npiInterface",{
            "kind":"interface","identity":"top.fabric","modport":"","interface_type":"bus_if","direction":"unknown"})
        rows = []
        for i in range(count):
            first = 2 + 3*i  # leave a row between successive groups
            for n in (first,first+1):
                name = f"rs_{n}"
                spec = {"source":"book","sheet":"Spec","row":n,"step":1,"Intf_type":"bus_if",
                        "RS_module":"rs","RS_inst":name}
                if n==first:
                    spec["step_sum_check"]={"range":f"Spec!L{n}:L{n+1}","members":[
                        {"row":j,"step":1} for j in (n,n+1)]}
                rows.append({"spec":spec,"instance_match":{},"matched_instances":[
                    {"full_name":"top."+name,"module":"rs","ports":{"port":pin.as_dict()}}]})
        groups=build_group_graphs(rows)
        self.assertEqual(len(groups),count)
        for i,g in enumerate(groups):
            first=2+3*i
            self.assertEqual((g["range"],g["rows"]),(f"Spec!L{first}:L{first+1}",[first,first+1]))
            w,=instance_wires(g["nets"])[0]
            self.assertEqual({p["instance"] for p in w.pins},{f"top.rs_{n}" for n in g["rows"]})

    def run_canvas(self, offline, book):
        collector = os.environ.get("RSCHECK_LINK_COLLECTOR")
        kdb = os.environ.get("RSCHECK_LINK_KDB")
        self.assertEqual(bool(collector), bool(kdb))
        if tk is None and not kdb:
            self.skipTest("Tk unavailable")
        try:
            root=tk.Tk()
        except tk.TclError as exc:
            if kdb: raise
            self.skipTest(str(exc))
        def close():
            for timer in root.tk.call("after","info"):
                root.after_cancel(timer)
            root.destroy()
        self.addCleanup(close)
        app=RsCheckApp(root)
        app.excel_var.set(str(book))
        app.column_vars["step_sum"].set("12")
        app.json_report_var.set(str(self.tmp / "result.json"))
        app.csv_report_var.set("")
        app.keep_inventory_var.set("")
        app.source_mode_var.set(LIVE_SOURCE if kdb else INVENTORY_SOURCE)
        if kdb:
            app.collector_var.set(collector)
            app.elab_db_var.set(kdb)
            app.npi_lib_var.set(os.environ.get("RSCHECK_ROUTE_NPI_LIB",""))
        else:
            app.inventory_var.set(str(offline))
        app._update_source_mode()
        root.update()
        with patch("rscheck.gui.messagebox.showerror") as err:
            app.run_button.invoke()
            end=time.monotonic()+90
            while app.running and time.monotonic()<end:
                root.update()
                time.sleep(.02)
            self.assertFalse(app.running)
            self.assertFalse(err.called,str(err.call_args))
        return root,app,load_report(app.json_report_var.get()).raw

    def test_visible_canvas_offline_or_live(self):
        root,app,raw=self.run_canvas(FIXTURE,self.book)
        self.assert_graph(raw)
        app.result_tree.selection_set("row-1")  # not the anchor row
        app._on_result_selected()
        view=app.connection_view
        app.inspector_tabs.select(view)
        root.update()
        self.assertTrue(view.canvas.winfo_ismapped())
        c=view.canvas
        def strings(tag):
            return {c.itemcget(i,"text") for i in c.find_withtag(tag)}
        self.assertFalse(c.find_withtag("network"))  # only RS instances get node boxes
        self.assertEqual(strings("wire_name"),{"link_top.shared_data","link_top.fabric"})
        self.assertEqual(strings("instance"),{"link_top."+n for n in ("tx","rx","peer","wrap.leaf","explicit_view","observe")})
        self.assertEqual(strings("isolated"),{"link_top.other"})
        self.assertEqual(len(c.find_withtag("instance_box")),6)
        self.assertEqual(len(c.find_withtag("wire")),7)
        self.assertTrue(c.find_withtag("junction"))
        self.assertEqual(view.counts,[3,4])
        self.assertEqual([c.itemcget(i,"arrow") for i in c.find_withtag("signal")],["last"]*3)
        self.assertEqual([c.itemcget(i,"arrow") for i in c.find_withtag("interface")],["none"]*4)
        # Actual geometry: both ends of every line touch sockets on real RS cards.
        sockets={(r[0]+3,r[1]+3) for r in (c.coords(i) for i in c.find_withtag("terminal"))}
        for i in c.find_withtag("wire"):
            xy=c.coords(i)
            self.assertIn(tuple(xy[:2]),sockets)
            self.assertIn(tuple(xy[-2:]),sockets)
        self.assertTrue(any("bus_if.mst" in p for p in strings("pin")))
        self.assertTrue(any("bus_if.slv" in p for p in strings("pin")))
        view._expand()
        root.update()
        win, = [w for w in view.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_ismapped()]
        if os.environ.get("RSCHECK_LINK_SCREENSHOT"):
            subprocess.run(["xwd", "-silent", "-id", str(win.winfo_id()), "-out", os.environ["RSCHECK_LINK_SCREENSHOT"]],
                           timeout=20,check=True)
        view.focus_mode.set("连接明细")
        view._switch()
        self.assertTrue(any("link_top.wrap.forwarded" in p for p in strings("pin")))
        view.focus_mode.set("端口证据")
        view._switch()
        self.assertTrue(c.find_withtag("pin"))
        app.result_tree.selection_set("row-7")
        app._on_result_selected()
        self.assertFalse(view.canvas.find_withtag("wire"))

        # Cycle/long route, same-interface multi-drop, and large fanout paging.
        def draw(nets):
            names=sorted({p["instance"] for net in nets for p in net["pins"]})
            view.set_graph({"range":"S!L2:L9","nodes":[{"instance":n,"rows":[2],"module":"rs"} for n in names],
                            "nets":nets,"diagnostics":[]})
        draw([raw_net("a",[("A","out","output"),("B","in","input")]),
              raw_net("b",[("B","out","output"),("C","in","input")]),
              raw_net("c",[("C","out","output"),("A","in","input")])])
        self.assertEqual(len(c.find_withtag("instance_box")),3)
        self.assertEqual(len(c.find_withtag("wire")),3)
        draw([raw_net("if",[(f"u{i}","p","") for i in range(61)],"interface")])
        self.assertEqual(len(view.sheets),2)
        self.assertEqual(len(c.find_withtag("shared_bus")),1)
        self.assertEqual(len(c.find_withtag("bus_branch")),view.PINS)
        view._move(1)
        self.assertEqual(len(c.find_withtag("bus_branch")),61-view.PINS)
        draw([raw_net("d",[("tx","out","output")]+[(f"u{i}","in","input") for i in range(650)])])
        view._move(999)
        self.assertEqual(len(c.find_withtag("wire")),650 % view.WIRES)
        self.assertEqual(len(view.sheets),(650+view.WIRES-1)//view.WIRES)

    def test_whole_interface_and_member_assignments_in_actual_gui(self):
        book=self.tmp/"whole.xlsx"
        expected=whole_fixture(book)
        inventory=self.tmp/"whole.json"
        inventory.write_text(json.dumps(inventory_to_dict(expected)),encoding="utf-8")
        root,app,raw=self.run_canvas(inventory,book)
        for row in raw["rows"]:
            inst,=row["matched_instances"]
            port,=inst["ports"].values()
            restricted=inst["name"]=="restricted"
            self.assertEqual(port["link"]["interface_type"],"limited_if" if restricted else "whole_if")
            self.assertEqual(port["link"]["modport"],"rx" if restricted else "")
            self.assertIn("member_pairs",port)
            self.assertTrue(row["passed"],row["findings"])
        pair,bus,members,mixed=raw["interconnect_groups"]
        groups=(pair,bus,members,mixed)
        self.assertEqual([g["range"] for g in groups],
                         ["RS_Check!"+r for r in ("L2:L4","L5:L7","L8:L10","L11:L12")])
        self.assertTrue(all(not g["diagnostics"] for g in (pair,bus,members,mixed)))
        mixed_wire,=instance_wires(mixed["nets"])[0]
        self.assertEqual(mixed_wire.path,tuple(BUNDLE+"."+s for s in MIXED_PATH))
        w,=instance_wires(pair["nets"])[0]
        self.assertEqual((w.mode,w.identity),("binding",BUNDLE+".lane_pair"))
        self.assertEqual({p["instance"] for p in w.pins},{BUNDLE+".first",BUNDLE+".hop.leaf"})
        w,=instance_wires(bus["nets"])[0]
        self.assertEqual((w.mode,len(w.pins)),("multidrop",3))
        wires,notes=instance_wires(members["nets"])
        self.assertFalse(notes)
        self.assertEqual({w.path for w in wires},
                         {tuple(BUNDLE+"."+s for s in path) for path in MEMBER_PATHS})
        self.assertTrue(all(w.kind=="member" and w.mode=="directed" for w in wires))
        self.assertEqual({tuple((p["instance"].rsplit(".",1)[-1],p["port"]) for p in w.pins) for w in wires},
            {(("producer","channel.request"),("consumer","port_if.request")),
             (("consumer","port_if.reply"),("producer","channel.reply")),
             (("producer","channel.payload"),("consumer","port_if.result"))})
        view=app.connection_view
        def show(index):
            app.result_tree.selection_set(f"row-{index}")
            app._on_result_selected()
            app.inspector_tabs.select(view)
            root.update()
            self.assertTrue(view.canvas.winfo_ismapped())
            group_number = next(i for i,g in enumerate(groups,1) if index+2 in g["rows"])
            self.assertEqual(view.graph["range"],groups[group_number-1]["range"])
            self.assertIn(f"合并组 {group_number}/4",view.status.cget("text"))
            self.assertIn("组内页 1/",view.status.cget("text"))
        def labels(tag):
            return [view.canvas.itemcget(i,"text") for i in view.canvas.find_withtag(tag)]
        show(1)
        self.assertEqual(len(view.canvas.find_withtag("interface")),1)
        self.assertTrue(all("整体接口" in s for s in labels("pin")))
        self.assertEqual(labels("isolated"),[BUNDLE+".separate"])
        show(4)
        self.assertEqual(len(view.canvas.find_withtag("bus_branch")),3)
        self.assertTrue(all("整体接口" in s for s in labels("pin")))
        show(7)
        self.assertEqual(len(view.canvas.find_withtag("member")),3)
        self.assertFalse(view.canvas.find_withtag("interface"))
        self.assertEqual(labels("isolated"),[BUNDLE+".bystander"])
        self.assertEqual(set(labels("formal")),{"channel · whole_if（整体接口）","port_if · whole_if（整体接口）"})
        coords=[view.canvas.coords(i) for i in view.canvas.find_withtag("member")]
        self.assertTrue(all(len(q)==4 and q[1]==q[3] for q in coords))
        self.assertEqual(sum(q[0]>q[2] for q in coords),1)  # return/reply
        if os.environ.get("RSCHECK_MEMBER_SCREENSHOT"):
            view._expand()
            root.update()
            win, = [w for w in view.winfo_children() if isinstance(w,tk.Toplevel) and w.winfo_ismapped()]
            subprocess.run(["xwd","-silent","-id",str(win.winfo_id()),"-out",os.environ["RSCHECK_MEMBER_SCREENSHOT"]],
                           timeout=20,check=True)
        view.focus_mode.set("连接明细")
        view._switch()
        self.assertTrue(any("data_bridge" in s for s in labels("member_path")))
        self.assertFalse(any("spare" in s for s in labels("member_path")))
        show(10)
        self.assertIn("channel · limited_if.rx",labels("formal"))
        self.assertEqual(len(view.canvas.find_withtag("member")),1)
        # Switching backwards restores that block only, including in the expanded view.
        show(1)
        view._expand()
        root.update()
        newest=[w for w in view.winfo_children() if isinstance(w,tk.Toplevel)][-1]
        expanded,=newest.winfo_children()
        self.assertIn("合并组 1/4",expanded.status.cget("text"))
        self.assertFalse(expanded.canvas.find_withtag("member"))

    def test_member_proof_rejects_malformed_paths_and_requires_both_group_endpoints(self):
        book=self.tmp/"whole.xlsx"
        inv=whole_fixture(book)
        sample=next(i for i in inv.positions[BUNDLE].instances if i.name=="producer")
        raw=sample.ports["channel"].as_dict()
        for path in ([],["top.x","top.y"],[raw["member_pairs"][0]["source"]]*2):
            broken={**raw,"member_pairs":[{**raw["member_pairs"][0],"path":path}]}
            with self.assertRaises(InventoryError):
                PortConnection.from_value(broken)
        saved=self.tmp/"whole.json"
        saved.write_text(json.dumps(inventory_to_dict(inv)),encoding="utf-8")
        self.assertEqual(load_inventory(saved),inv)
        rows=read_spec_rows(book,replace(self.cfg.excel,columns={**self.cfg.excel.columns,"step_sum":12}))
        old=replace(inv,positions={k:replace(v,instances=tuple(
            replace(i,ports={n:replace(p,member_pairs=None) if i.name=="consumer" else p for n,p in i.ports.items()})
            for i in v.instances)) for k,v in inv.positions.items()})
        g=report_to_dict(self.check(old,rows))["interconnect_groups"][2]
        self.assertFalse(instance_wires(g["nets"])[0])
