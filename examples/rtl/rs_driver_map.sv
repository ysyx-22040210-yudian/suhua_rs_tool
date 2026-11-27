// The decoy CRG drives only controls and an independently selectable test clock.
module demo_crg(input wire ref_i, output wire co, ro);
  assign co = ref_i;
  assign ro = ~ref_i;
endmodule
module demo_div(input wire clk, rst_n, output wire co, ro);
  assign co = clk;
  assign ro = rst_n;
endmodule
module demo_gate(input wire ci, ri, en, output wire co, ro);
  assign co = ci & en;
  assign ro = ri & en;
endmodule
module demo_occ(input wire func_i, test_i, sel, ri, output wire co, ro);
  assign co = sel ? test_i : func_i;
  assign ro = ri;
endmodule
module demo_rs(input wire cn, rn, output wire seen);
  assign seen = cn & rn;
endmodule
module driver_top(input wire a, b, output wire q);
  wire src_clk, src_rst, test_clk, enable, dc, dr, gc, gr, oc, ors;
  demo_crg src (.ref_i(a), .co(src_clk), .ro(src_rst));
  demo_crg decoy (.ref_i(b), .co(test_clk), .ro(enable));
  demo_div div_i (.clk(src_clk), .rst_n(src_rst), .co(dc), .ro(dr));
  demo_gate gate_i (.ci(dc), .ri(dr), .en(enable), .co(gc), .ro(gr));
  demo_occ occ_i (.func_i(gc), .test_i(test_clk), .sel(enable), .ri(gr), .co(oc), .ro(ors));
  demo_rs pipe (.cn(oc), .rn(ors), .seen(q));
endmodule
