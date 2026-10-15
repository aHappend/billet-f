module billet_f1_top #(
    parameter integer TAP_COUNT = 32,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1,
    localparam integer ACC_W = 32 + ((TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 0)
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic signed [15:0]       s_data,
    input  logic                     s_valid,
    output logic                     s_ready,
    output logic signed [15:0]       m_data,
    output logic                     m_valid,
    input  logic                     m_ready,
    input  logic                     cfg_valid,
    output logic                     cfg_ready,
    input  logic [ADDR_W-1:0]        cfg_addr,
    input  logic signed [15:0]       cfg_data,
    input  logic                     commit_valid,
    output logic                     commit_ready
);

    generate
        if ((TAP_COUNT == 1) || (TAP_COUNT == 8) ||
            (TAP_COUNT == 16) || (TAP_COUNT == 32) ||
            (TAP_COUNT == 64)) begin : g_supported
            wire busy;
            wire staging_complete;
            wire [ADDR_W-1:0] coeff_addr;
            wire signed [15:0] coeff_data;
            wire final_valid;
            wire signed [ACC_W-1:0] final_sum;
            wire signed [15:0] rounded_result;
            logic output_valid;
            logic signed [15:0] output_data;

            assign m_valid = rst_n && output_valid;
            assign m_data = (rst_n && output_valid) ? output_data : 16'sd0;
            assign s_ready = rst_n && !busy && (!m_valid || m_ready) &&
                             !commit_valid;
            assign cfg_ready = rst_n && !commit_valid && (cfg_addr < TAP_COUNT);
            assign commit_ready = rst_n && staging_complete && !busy && !m_valid;

            billet_f1_coeff_bank #(
                .TAP_COUNT(TAP_COUNT)
            ) u_coeff_bank (
                .clk(clk),
                .rst_n(rst_n),
                .cfg_write(cfg_valid && cfg_ready),
                .cfg_addr(cfg_addr),
                .cfg_data(cfg_data),
                .commit(commit_valid && commit_ready),
                .staging_complete(staging_complete),
                .read_addr(coeff_addr),
                .read_data(coeff_data)
            );

            billet_f1_mac #(
                .TAP_COUNT(TAP_COUNT)
            ) u_mac (
                .clk(clk),
                .rst_n(rst_n),
                .start(s_valid && s_ready),
                .sample_data(s_data),
                .coeff_addr(coeff_addr),
                .coeff_data(coeff_data),
                .busy(busy),
                .final_valid(final_valid),
                .final_sum(final_sum)
            );

            billet_f1_round_sat #(
                .ACC_W(ACC_W)
            ) u_round_sat (
                .accumulator(final_sum),
                .result(rounded_result)
            );

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    output_valid <= 1'b0;
                    output_data <= 16'sd0;
                end else if (final_valid) begin
                    output_valid <= 1'b1;
                    output_data <= rounded_result;
                end else if (m_valid && m_ready) begin
                    output_valid <= 1'b0;
                    output_data <= 16'sd0;
                end
            end
        end else begin : g_unsupported
            initial begin
                $fatal(1, "PARAMETER ERROR: TAP_COUNT must be 1, 8, 16, 32 or 64");
            end
            assign s_ready = 1'b0;
            assign m_data = 16'sd0;
            assign m_valid = 1'b0;
            assign cfg_ready = 1'b0;
            assign commit_ready = 1'b0;
        end
    endgenerate

endmodule
