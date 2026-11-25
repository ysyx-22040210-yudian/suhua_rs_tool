"""Clock and reset are independent witnesses to actual output connections of the target CRG."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from rscheck.checker import check_specs
from rscheck.config import load_config
from rscheck.excel_reader import read_spec_rows
from rscheck.gui_backend import GuiReportError, load_report
from rscheck.inventory import inventory_to_dict, load_inventory
from rscheck.model import InventoryError, PositionInventory
from rscheck.reporting import report_to_dict

ROOT = Path(__file__).resolve().parents[1]


class SourceSignalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(ROOT / "config/rscheck.example.json")
        self.inv = load_inventory(ROOT / "examples/inventory.json")
        self.spec, _ = read_spec_rows(ROOT / "examples/specs.csv", self.cfg.excel, self.cfg.position_mappings,
                                      self.cfg.crg_source_mappings)
        self.spec = replace(self.spec, rs_inst="AAAA_BBB_C0", step=1)
        self.inst = self.inv.positions[self.spec.position].instances[0]

    def check(self, inst=None, *, rtl=None, **changes):
        inv = replace(self.inv, positions={self.spec.position: PositionInventory(True, (inst or self.inst,))})
        return check_specs([replace(self.spec, **changes)], inv, rtl or self.cfg.rtl, self.cfg.module_rules)

    def test_sink_names_and_formals_do_not_match_source(self):
        for key, expected in (("clk", "clk_rs"), ("rst", "rst_rs"), ("clk", "clk_out"), ("rst", "reset_out"),
                              ("clk", "clk_aux"), ("clk", "top.u_tile_peer.clk_core")):
            with self.subTest(key=key, expected=expected):
                row, = self.check(**{key: expected}).rows
                self.assertEqual([f.code for f in row.findings], [key.upper()+"_CONNECTION_MISMATCH"])
                self.assertFalse(row.passed)
                self.assertEqual({s.role for s in row.source_signals if s.status == "matched"}, {"clk", "rst"}-{key})
        row, = self.check(clk="top.u_tile.clk_core", rst="top.u_tile.rst_core").rows
        self.assertTrue(row.passed)
        self.assertEqual([(s.matching[0].depth, s.matching[0].port) for s in row.source_signals],
                         [(3, "clk_out"), (2, "reset_out")])

    def test_resolves_local_name_in_source_parent_not_rs_parent(self):
        target = "top.subsystem.clock_domain.u_gen"
        t = self.inst.clock_trace
        n, e = t.modules[0], t.endpoints[0]
        def at(source, name):
            return replace(source, modules=(replace(n, instance=target, path=(target,), depth=1),),
                           endpoints=(replace(e, instance=target, path=(target,), depth=1,
                                              connection="top.subsystem.clock_domain."+name),))
        inst = replace(self.inst, clock_trace=at(t, "root_clk"), reset_trace=at(self.inst.reset_trace, "root_rst"))
        report = self.check(inst, crg_source=target, clk="root_clk", rst="root_rst")
        self.assertEqual((report.error_count, report.warning_count), (0, 0))
        self.assertFalse(self.check(inst, crg_source=target, clk="top.u_tile.root_clk", rst="root_rst").passed)

    def test_reconvergent_outputs_have_separate_depth_and_witness(self):
        tr = self.inst.clock_trace
        root = next(e for e in tr.endpoints if e.instance == self.spec.crg_source)
        other = replace(root, port="other_out", connection="top.u_tile.other", depth=4,
                        path=("top.u_tile.branch", *root.path))
        inst = replace(self.inst, clock_trace=replace(tr, endpoints=(*tr.endpoints, other)))
        r, = self.check(inst, clk="other").rows
        self.assertEqual(r.source_signals[0].matching, (other,))
        r, = self.check(inst, clk="other", rtl=replace(self.cfg.rtl, crg_trace_max_depth=3)).rows
        self.assertNotEqual(r.source_signals[0].status, "matched")

    def test_truncation_and_reset_cannot_be_hidden_by_clock_success(self):
        r, = self.check(rtl=replace(self.cfg.rtl, crg_trace_max_depth=2)).rows
        self.assertEqual([s.status for s in r.source_signals], ["depth_limited", "matched"])
        r, = self.check(replace(self.inst, reset_trace=None)).rows
        self.assertEqual([f.code for f in r.findings], ["RST_SOURCE_SIGNAL_UNAVAILABLE"])
        self.assertEqual([s.status for s in r.source_signals], ["matched", "unavailable"])
        tr = self.inst.reset_trace
        for status, suffix in (("complete", "NOT_FOUND"), ("depth_limited", "TRACE_DEPTH_LIMIT"),
                               ("unresolved", "TRACE_UNAVAILABLE")):
            r, = self.check(replace(self.inst, reset_trace=replace(tr, status=status, modules=(), endpoints=()))).rows
            self.assertEqual([(f.severity, f.code) for f in r.findings], [("warning", "RST_SOURCE_"+suffix)])

    def test_missing_output_name_cannot_fall_back_to_rs_net_or_all_crg_ports(self):
        tr = self.inst.reset_trace
        inst = replace(self.inst, reset_trace=replace(tr, endpoints=()))
        r, = self.check(inst, rst="rst_rs").rows
        self.assertEqual([(f.severity, f.code) for f in r.findings], [("warning", "RST_SOURCE_SIGNAL_UNAVAILABLE")])
        r, = self.check(replace(self.inst, reset_trace=replace(tr, clock_port="wrong"))).rows
        self.assertEqual(r.source_signals[1].status, "unavailable")

    def test_saved_evidence_and_schema_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "check.json"
            data = report_to_dict(self.check())
            file.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_report(file).rows[0]["source_signal_checks"][1]["matching"][0]["port"], "reset_out")
            data["rows"][0]["source_signal_checks"][1]["endpoints"][0]["port"] = "unrelated"
            file.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(GuiReportError):
                load_report(file)
            inv = inventory_to_dict(self.inv)
            file.write_text(json.dumps(inv), encoding="utf-8")
            self.assertEqual(load_inventory(file), self.inv)
            del inv["positions"][self.spec.position]["instances"][0]["reset_trace"]["endpoints"]
            file.write_text(json.dumps(inv), encoding="utf-8")
            with self.assertRaisesRegex(InventoryError, "endpoints"):
                load_inventory(file)
