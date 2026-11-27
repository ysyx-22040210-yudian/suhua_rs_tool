// Identical local RS names at three depths; no CRG/driver semantics are mocked.
module bound_crg(input wire ref_i, output wire clk_o);
  assign clk_o = ref_i;
endmodule
module bound_rs(input wire clk, output wire q);
  assign q = clk;
endmodule
module bound_deep(input wire ck, output wire [1:0] q);
  bound_rs PIPE_C0 (.clk(ck), .q(q[0]));
  bound_rs LEFT_RS (.clk(ck), .q(q[1]));
endmodule
module bound_tile(input wire ck, output wire [2:0] q);
  bound_rs PIPE_C0 (.clk(ck), .q(q[0]));
  for (genvar i=0; i<1; i=i+1) begin : lane
    bound_deep wrap_deep (.ck(ck), .q(q[2:1]));
  end
endmodule
module bound_peer(input wire ck, output wire q);
  bound_rs PIPE_C0 (.clk(ck), .q(q));
endmodule
module boundary_top(input wire ref_i, output wire [4:0] q);
  wire root_clk;
  bound_crg src (.ref_i(ref_i), .clk_o(root_clk));
  bound_rs PIPE_C0 (.clk(root_clk), .q(q[0]));
  bound_tile tile (.ck(root_clk), .q(q[3:1]));
  bound_peer tile2 (.ck(root_clk), .q(q[4]));
endmodule
