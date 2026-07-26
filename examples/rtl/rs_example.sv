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
    logic clk_aux;
    logic stage_0;
    logic stage_1;
    logic stage_2;
    logic stage_3;
    logic stage_4;
    logic custom_stage;
    logic clk_only_stage;

    crg_core u_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_rs)
    );

    crg_aux u_aux_crg (
        .ref_clk (ref_clk),
        .clk_out (clk_aux)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C0 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (data_in),
        .q   (stage_0)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C1 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_0),
        .q   (stage_1)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b0)
    ) AAAA_BBB_C2 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_1),
        .q   (stage_2)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C3 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_2),
        .q   (stage_3)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C4 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_3),
        .q   (stage_4)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) AAAA_BBB_C5 (
        .clk (clk_rs),
        .rst (rst_n),
        .d   (stage_4),
        .q   (data_out)
    );

    rs_pipe #(
        .RS_CRG_EN (1'b0),
        .rs_mode   (1'b1)
    ) CTRL_RS_D0 (
        .clk (clk_aux),
        .rst (rst_n),
        .d   (data_in),
        .q   (ctrl_out)
    );

    // The VM regression owns this instance through a temporary custom-port spec.
    rs_custom #(
        .RS_CRG_EN (1'b0)
    ) CUSTOM_RS (
        .clock_i  (clk_rs),
        .reset_ni (rst_n),
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
    tile u_tile (
        .ref_clk  (ref_clk),
        .rst_n    (rst_n),
        .data_in  (data_in),
        .data_out (data_out),
        .ctrl_out (ctrl_out)
    );

`ifdef RSCHECK_PARTIAL_LOAD_FIXTURE
    // Intentional unresolved instance used by the partial-load VM regression.
    intentionally_missing_module u_expected_elaboration_error ();
`endif
endmodule
