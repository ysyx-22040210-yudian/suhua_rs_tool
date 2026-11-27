// Test DUT only. Keep these dimensions in sync with the workbook and oracle.
module qa_crg(input wire ref_i, reset_i, output wire ck_o, rn_o);
  assign ck_o = ref_i;
  assign rn_o = reset_i;
endmodule

module qa_rs #(parameter RS_CRG_EN=0, parameter rs_mode=1)
  (input wire clk, rst_n, d, output reg q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 0; else q <= d;
endmodule

module qa_inner(input wire clk, rst_n, d, output wire [1:0] q);
  qa_rs OWN  (.clk(clk), .rst_n(rst_n), .d(d), .q(q[0]));
  qa_rs LEFT (.clk(clk), .rst_n(rst_n), .d(d), .q(q[1]));
endmodule

module qa_tx(input wire clk, rst_n, d, output reg link);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) link <= 0; else link <= d;
endmodule
module qa_rx(input wire clk, rst_n, link, output reg q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 0; else q <= link;
endmodule

module qa_tile(input wire clk, rst_n, d, output wire [34:0] q);
  wire channel;
  qa_tx TX(.clk(clk), .rst_n(rst_n), .d(d), .link(channel));
  qa_rx RX(.clk(clk), .rst_n(rst_n), .link(channel), .q(q[34]));
  qa_inner inner(.clk(clk), .rst_n(rst_n), .d(d), .q(q[33:32]));
  for (genvar p=0; p<32; p=p+1) begin : lanes
    qa_rs #(.rs_mode(p%2)) PIPE (.clk(clk), .rst_n(rst_n), .d(d), .q(q[p]));
  end
endmodule

module stress_top(input wire ref_i, reset_i, input wire [95:0] data, output wire [3359:0] q);
  wire root_clock, root_reset;
  qa_crg src(.ref_i(ref_i), .reset_i(reset_i), .ck_o(root_clock), .rn_o(root_reset));
  for (genvar i=0; i<96; i=i+1) begin : banks
    qa_tile tile(.clk(root_clock), .rst_n(root_reset), .d(data[i]), .q(q[35*i+:35]));
  end
endmodule
