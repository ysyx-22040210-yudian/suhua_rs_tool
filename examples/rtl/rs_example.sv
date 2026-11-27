module crg_core (
    input  logic ref_clk,
    input  logic ref_rst_n,
    output logic clk_out,
    output logic reset_out
);
    assign clk_out = ref_clk;
    assign reset_out = ref_rst_n;
endmodule

module crg_aux (
    input  logic ref_clk,
    input  logic ref_rst_n,
    output logic clk_out,
    output logic reset_out
);
    assign clk_out = ref_clk;
    assign reset_out = ref_rst_n;
endmodule

module reset_stage(input logic rst_n, output logic reset_out);
    assign reset_out = rst_n;
endmodule

module clk_mux (
    input  logic functional_in,
    input  logic test_in,
    input  logic select_in,
    output logic clk_out
);
    assign clk_out = select_in ? test_in : functional_in;
endmodule

module clk_occ (
    input  logic source_in,
    input  logic enable_in,
    input  logic clk,
    input  logic rst_n,
    output logic clk_out
);
    // Only clk_out -> source_in is registered in the example driver map;
    // enable_in, clk and rst_n are not part of that path.
    assign clk_out = rst_n && enable_in ? source_in : 1'b0;
endmodule

module rs_pipe #(
    parameter logic RS_CRG_EN = 1'b1,
    parameter logic WIDTH = 1'b1,
    parameter logic rs_mode = 1'b1
) (
    input  logic clk,
    input  logic rst,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin
            q <= 1'b0;
        end else if (!RS_CRG_EN && WIDTH && rs_mode) begin
            q <= d;
        end
    end
endmodule

module rs_custom #(
    parameter logic RS_CRG_EN = 1'b1
) (
    input  logic clock_i,
    input  logic reset_ni,
    input  logic d,
    output logic q
);
    always_ff @(posedge clock_i or negedge reset_ni) begin
        if (!reset_ni) begin
            q <= 1'b0;
        end else if (!RS_CRG_EN) begin
            q <= d;
        end
    end
endmodule

module rs_clk_only #(
    parameter logic RS_CRG_EN = 1'b1
) (
    input  logic clk,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) begin
        if (!RS_CRG_EN) begin
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
    logic clk_core;
    logic clk_aux;
    logic clk_muxed;
    logic rst_core, rst_aux, rst_rs, rst_ctrl;
    logic stage_0;
    logic stage_1;
    logic stage_2;
    logic stage_3;
    logic stage_4;
    logic custom_stage;
    logic clk_only_stage;

    crg_core u_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_core), .ref_rst_n(rst_n), .reset_out(rst_core)
    );

    crg_aux u_aux_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_aux), .ref_rst_n(rst_n), .reset_out(rst_aux)
    );
    reset_stage u_reset (.rst_n(rst_core), .reset_out(rst_rs));
    reset_stage u_ctrl_reset (.rst_n(rst_aux), .reset_out(rst_ctrl));

    // The positive CRG trace is deliberately multi-level and branched:
    // RS -> u_occ -> u_clk_mux -> {u_crg, u_aux_crg}.
    clk_mux u_clk_mux (
        .functional_in (clk_core),
        .test_in       (clk_aux),
        .select_in     (data_in),
        .clk_out       (clk_muxed)
    );

    clk_occ u_occ (
        .source_in (clk_muxed),
        .enable_in (1'b1),
        .clk       (ref_clk),
        .rst_n     (rst_n),
        .clk_out   (clk_rs)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C0 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (data_in),
        .q   (stage_0)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C1 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (stage_0),
        .q   (stage_1)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b0)
    ) AAAA_BBB_C2 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (stage_1),
        .q   (stage_2)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C3 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (stage_2),
        .q   (stage_3)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C4 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (stage_3),
        .q   (stage_4)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C5 (
        .clk (clk_rs),
        .rst (rst_rs),
        .d   (stage_4),
        .q   (data_out)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) CTRL_RS_D0 (
        .clk (clk_aux),
        .rst (rst_ctrl),
        .d   (data_in),
        .q   (ctrl_out)
    );

    // The VM regression owns this instance through a temporary custom-port spec.
    rs_custom #(
        .RS_CRG_EN (1'b0)
    ) CUSTOM_RS (
        .clock_i  (clk_rs),
        .reset_ni (rst_rs),
        .d        (data_in),
        .q        (custom_stage)
    );

    // This instance proves that a missing rst cannot hide an existing clk.
    rs_clk_only #(
        .RS_CRG_EN (1'b0)
    ) CLK_ONLY_RS (
        .clk (clk_rs),
        .d   (data_in),
        .q   (clk_only_stage)
    );
endmodule

module top (
    input  logic ref_clk,
    input  logic rst_n,
    input  logic data_in,
    output logic data_out,
    output logic ctrl_out
);
    logic peer_data_out;
    logic peer_ctrl_out;

    tile u_tile (
        .ref_clk  (ref_clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        .data_out (data_out),
        .ctrl_out (ctrl_out)
    );

    // A second identical hierarchy proves that trace caches cannot leak
    // same-named local nets or instances across parent scopes.
    tile u_tile_peer (
        .ref_clk  (ref_clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        .data_out (peer_data_out),
        .ctrl_out (peer_ctrl_out)
    );

`ifdef RSCHECK_PARTIAL_LOAD_FIXTURE
    // Intentional unresolved instance used by the partial-load VM regression.
    intentionally_missing_module u_expected_elaboration_error ();
`endif
endmodule
