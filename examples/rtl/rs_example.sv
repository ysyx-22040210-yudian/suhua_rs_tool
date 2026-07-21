module crg_core (
    input  logic ref_clk,
    output logic clk_out
);
    assign clk_out = ref_clk;
endmodule

module crg_aux (
    input  logic ref_clk,
    output logic clk_out
);
    assign clk_out = ref_clk;
endmodule

module rs_pipe (
    input  logic clk,
    input  logic rst,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin
            q <= 1'b0;
        end else begin
            q <= d;
        end
    end
endmodule

module tile (
    input  logic ref_clk,
    input  logic rst_n,
    input  logic data_in,
    output logic data_out,
    output logic ctrl_out
);
    logic clk_rs;
    logic clk_aux;
    logic stage_0;

    crg_core u_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_rs)
    );

    crg_aux u_aux_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_aux)
    );

    rs_pipe AAAA_BBB_C0 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (data_in),
        .q   (stage_0)
    );

    rs_pipe AAAA_BBB_C1 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_0),
        .q   (data_out)
    );

    rs_pipe CTRL_RS_D0 (
        .clk (clk_aux),
        .rst (rst_n),
        .d   (data_in),
        .q   (ctrl_out)
    );
endmodule

module top (
    input  logic ref_clk,
    input  logic rst_n,
    input  logic data_in,
    output logic data_out,
    output logic ctrl_out
);
    tile u_tile (
        .ref_clk  (ref_clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        .data_out (data_out),
        .ctrl_out (ctrl_out)
    );
endmodule

