module billet_f2_top #(
    parameter integer TAP_COUNT = 32,
    parameter integer MAC_LANES = 1,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1,
    localparam integer ACC_W = 32 + ((TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 0)
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic signed [15:0]      s_data,
    input  logic [1:0]              s_channel,
    input  logic                    s_valid,
    output logic                    s_ready,
    output logic signed [15:0]      m_data,
    output logic [1:0]              m_channel,
    output logic                    m_valid,
    input  logic                    m_ready,
    input  logic                    cfg_valid,
    output logic                    cfg_ready,
    input  logic [1:0]              cfg_channel,
    input  logic [ADDR_W-1:0]       cfg_addr,
    input  logic signed [15:0]      cfg_data,
    input  logic                    commit_valid,
    output logic                    commit_ready,
    input  logic [1:0]              commit_channel
);

    generate
        if (((TAP_COUNT == 1) || (TAP_COUNT == 8) ||
             (TAP_COUNT == 16) || (TAP_COUNT == 32) ||
             (TAP_COUNT == 64)) &&
            ((MAC_LANES == 1) || (MAC_LANES == 2) ||
             (MAC_LANES == 4))) begin : g_supported
            wire busy;
            wire sample_accept;
            wire [3:0] active_banks;
            wire [3:0] staging_complete;
            wire [1:0] read_channel;
            wire read_bank;
            wire [ADDR_W-1:0] read_base;
            wire [16*MAC_LANES-1:0] coeff_lanes;
            wire [16*MAC_LANES-1:0] sample_lanes;
            wire final_valid;
            wire signed [ACC_W-1:0] final_sum;
            wire [1:0] final_channel;
            wire signed [15:0] rounded_result;
            logic output_valid;
            logic signed [15:0] output_data;
            logic [1:0] output_channel;

            assign m_valid = rst_n && output_valid;
            assign m_data = (rst_n && output_valid) ? output_data : 16'sd0;
            assign m_channel = (rst_n && output_valid) ? output_channel : 2'd0;
            assign s_ready = rst_n && !busy && (!output_valid || m_ready) &&
                             !commit_valid;
            assign cfg_ready = rst_n && !commit_valid && (cfg_addr < TAP_COUNT);
            assign commit_ready = rst_n && staging_complete[commit_channel] &&
                                  !busy && !output_valid;
            assign sample_accept = s_valid && s_ready;

            billet_f2_coeff_bank #(
                .TAP_COUNT(TAP_COUNT),
                .MAC_LANES(MAC_LANES)
            ) u_coeff_bank (
                .clk(clk),
                .rst_n(rst_n),
                .cfg_write(cfg_valid && cfg_ready),
                .cfg_channel(cfg_channel),
                .cfg_addr(cfg_addr),
                .cfg_data(cfg_data),
                .commit(commit_valid && commit_ready),
                .commit_channel(commit_channel),
                .active_banks(active_banks),
                .staging_complete(staging_complete),
                .read_channel(read_channel),
                .read_bank(read_bank),
                .read_base(read_base),
                .coeff_lanes(coeff_lanes)
            );

            billet_f2_history #(
                .TAP_COUNT(TAP_COUNT),
                .MAC_LANES(MAC_LANES)
            ) u_history (
                .clk(clk),
                .rst_n(rst_n),
                .push(sample_accept),
                .push_channel(s_channel),
                .push_data(s_data),
                .read_channel(read_channel),
                .read_base(read_base),
                .sample_lanes(sample_lanes)
            );

            billet_f2_mac #(
                .TAP_COUNT(TAP_COUNT),
                .MAC_LANES(MAC_LANES)
            ) u_mac (
                .clk(clk),
                .rst_n(rst_n),
                .start(sample_accept),
                .sample_channel(s_channel),
                .sample_bank(active_banks[s_channel]),
                .coeff_lanes(coeff_lanes),
                .sample_lanes(sample_lanes),
                .read_channel(read_channel),
                .read_bank(read_bank),
                .read_base(read_base),
                .busy(busy),
                .final_valid(final_valid),
                .final_sum(final_sum),
                .final_channel(final_channel)
            );

            billet_f2_round_sat #(
                .ACC_W(ACC_W)
            ) u_round_sat (
                .accumulator(final_sum),
                .result(rounded_result)
            );

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    output_valid <= 1'b0;
                    output_data <= 16'sd0;
                    output_channel <= 2'd0;
                end else if (final_valid) begin
                    output_valid <= 1'b1;
                    output_data <= rounded_result;
                    output_channel <= final_channel;
                end else if (m_valid && m_ready) begin
                    output_valid <= 1'b0;
                    output_data <= 16'sd0;
                    output_channel <= 2'd0;
                end
            end
        end else begin : g_unsupported
            initial begin
                $fatal(1, "PARAMETER ERROR: TAP_COUNT must be 1, 8, 16, 32 or 64; MAC_LANES must be 1, 2 or 4");
            end
            assign s_ready = 1'b0;
            assign m_data = 16'sd0;
            assign m_channel = 2'd0;
            assign m_valid = 1'b0;
            assign cfg_ready = 1'b0;
            assign commit_ready = 1'b0;
        end
    endgenerate

endmodule
