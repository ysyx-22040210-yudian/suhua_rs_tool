// Independent Verdi integration fixture: exact signal, same type/different
// interface objects, declared modports and bare/generic interface ports.
interface bus_if;
  logic req, ack;
  modport mst(output req, input ack);
  modport slv(input req, output ack);
endinterface

module rs_send(input wire clk, input wire rst_n, input wire d,
               output wire data_o, bus_if.mst bus);
  assign data_o = d & rst_n;
  assign bus.req = d;
endmodule
module rs_recv(input wire clk, input wire rst_n, input wire data_i, bus_if.slv bus);
  assign bus.ack = data_i;
endmodule
module rs_wire(input wire clk, input wire rst_n, input wire sink);
  wire sampled = sink;
endmodule
module rs_watch(input wire clk, input wire rst_n, bus_if bus);
  wire sample = bus.req;
endmodule
module rs_generic(input wire clk, input wire rst_n, interface bus);
  wire sample = bus.req;
endmodule
module rx_wrap(input wire clk, input wire rst_n, input wire forwarded, bus_if fabric);
  rs_recv leaf(.clk(clk), .rst_n(rst_n), .data_i(forwarded), .bus(fabric));
endmodule

// A bundled interface with no modport declarations at all. Each RS below
// has exactly one formal; no scalar I/O, clock or reset is needed to bind it.
interface whole_if;
  logic request, reply;
  wire [7:0] payload, result;
  wire spare;
endinterface
module rs_whole_left(whole_if channel);
  assign channel.request = 1'b1;
endmodule
module rs_whole_right(interface port_if);
  wire observed = port_if.request;
endmodule
module whole_wrap(whole_if from_parent);
  rs_whole_right leaf(.port_if(from_parent));
endmodule
interface limited_if;
  logic request, hidden;
  modport rx(input request);
endinterface
module rs_limited(limited_if.rx channel);
  wire observed = channel.request;
endmodule
module whole_area;
  whole_if lane_pair();
  whole_if lane_other();
  whole_if lane_shared();
  rs_whole_left first(.channel(lane_pair));
  whole_wrap hop(.from_parent(lane_pair));
  rs_whole_left separate(.channel(lane_other));
  rs_whole_left a(.channel(lane_shared));
  rs_whole_right b(.port_if(lane_shared));
  rs_whole_right c(.port_if(lane_shared));
  // Distinct interface instances: individual members, not the bundles, connect.
  whole_if outbound();
  whole_if inbound();
  whole_if unrelated();
  rs_whole_left producer(.channel(outbound));
  rs_whole_right consumer(.port_if(inbound));
  rs_whole_left bystander(.channel(unrelated));
  wire [7:0] data_bridge;
  assign inbound.request = outbound.request;
  assign outbound.reply = inbound.reply;
  assign data_bridge = outbound.payload;
  assign inbound.result = data_bridge;
  assign inbound.spare = ~outbound.spare; // logic, not a wire binding
  assign unrelated.request = 1'b0;                  // constant
  assign unrelated.reply = outbound.payload;         // truncated vector
  assign unrelated.result = {8{outbound.request}};   // replication
  assign unrelated.spare = outbound.payload[1];      // bit select
  whole_if plain_view();
  limited_if restricted_view();
  rs_whole_left plain(.channel(plain_view));
  rs_limited restricted(.channel(restricted_view));
  assign restricted_view.request = plain_view.request;
  assign restricted_view.hidden = plain_view.reply; // not exposed in rx
endmodule
module link_top(input wire clk, input wire rst_n, input wire d);
  wire shared_data;
  bus_if fabric();
  bus_if separate();
  rs_send tx(clk, rst_n, d, shared_data, fabric);
  rs_recv rx(clk, rst_n, shared_data, fabric);
  rs_wire peer(clk, rst_n, shared_data);
  rs_recv other(clk, rst_n, d, separate);
  rs_watch observe(clk, rst_n, fabric);
  rs_generic explicit_view(clk, rst_n, fabric.slv);
  rx_wrap wrap(clk, rst_n, shared_data, fabric);
  whole_area bundled();
endmodule
