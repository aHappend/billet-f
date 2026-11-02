module billet_f3_top #(
    parameter integer TAP_COUNT = 32,
    parameter integer MAC_LANES = 1,
    parameter integer SOS_COUNT = 2,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1
) (
    input  wire clk,
    input  wire rst_n,
    input  wire signed [15:0] s_data,
    input  wire [1:0] s_channel,
    input  wire s_valid,
    output wire s_ready,
    output wire signed [15:0] m_data,
    output wire [1:0] m_channel,
    output wire m_valid,
    input  wire m_ready,
    input  wire cfg_valid,
    output wire cfg_ready,
    input  wire [1:0] cfg_channel,
    input  wire [ADDR_W-1:0] cfg_addr,
    input  wire signed [15:0] cfg_data,
    input  wire commit_valid,
    output wire commit_ready,
    input  wire [1:0] commit_channel,
    input  wire sos_cfg_valid,
    output wire sos_cfg_ready,
    input  wire [1:0] sos_cfg_channel,
    input  wire [1:0] sos_cfg_section,
    input  wire [2:0] sos_cfg_addr,
    input  wire signed [17:0] sos_cfg_data,
    input  wire sos_commit_valid,
    output wire sos_commit_ready,
    input  wire [1:0] sos_commit_channel,
    input  wire chain_valid,
    output wire chain_ready,
    input  wire [2:0] chain_mode,
    input  wire [2:0] chain_sections
);

    generate
        if (((TAP_COUNT == 1) || (TAP_COUNT == 8) ||
             (TAP_COUNT == 16) || (TAP_COUNT == 32) ||
             (TAP_COUNT == 64)) &&
            ((MAC_LANES == 1) || (MAC_LANES == 2) ||
             (MAC_LANES == 4)) &&
            ((SOS_COUNT >= 1) && (SOS_COUNT <= 4))) begin : g_supported
            localparam [2:0] IDLE = 3'd0;
            localparam [2:0] BYPASS = 3'd1;
            localparam [2:0] FIR_ONLY = 3'd2;
            localparam [2:0] SOS_ONLY = 3'd3;
            localparam [2:0] FIR_FIRST = 3'd4;
            localparam [2:0] SOS_LAST = 3'd5;
            localparam [2:0] SOS_FIRST = 3'd6;
            localparam [2:0] FIR_LAST = 3'd7;
            logic [2:0] state;
            logic [2:0] active_mode;
            logic [2:0] active_sections;
            logic signed [15:0] bypass_data;
            logic [1:0] bypass_channel;
            wire any_update;
            wire quiescent;
            wire descriptor_legal;
            wire chain_fire;
            wire sample_accept;
            wire retire;
            wire slot_available;
            wire first_ready;
            wire fir_final;
            wire sos_final;
            wire fir_to_sos;
            wire sos_to_fir;
            wire fir_s_valid;
            wire fir_s_ready;
            wire signed [15:0] fir_s_data;
            wire [1:0] fir_s_channel;
            wire signed [15:0] fir_m_data;
            wire [1:0] fir_m_channel;
            wire fir_m_valid;
            wire fir_m_ready;
            wire fir_cfg_ready;
            wire fir_commit_valid;
            wire fir_commit_ready;
            wire sos_s_valid;
            wire sos_s_ready;
            wire signed [15:0] sos_s_data;
            wire [1:0] sos_s_channel;
            wire signed [15:0] sos_m_data;
            wire [1:0] sos_m_channel;
            wire sos_m_valid;
            wire sos_m_ready;
            wire engine_cfg_ready;
            wire engine_commit_valid;
            wire engine_commit_ready;

            assign any_update = chain_valid || sos_commit_valid || commit_valid;
            assign quiescent = (state == IDLE);
            assign descriptor_legal =
                (((chain_mode == 3'd0) || (chain_mode == 3'd1)) &&
                 (chain_sections == 3'd0)) ||
                (((chain_mode == 3'd2) || (chain_mode == 3'd3) ||
                  (chain_mode == 3'd4)) &&
                 (chain_sections >= 1) && (chain_sections <= SOS_COUNT));
            assign chain_ready = rst_n && quiescent && descriptor_legal;
            assign chain_fire = chain_valid && chain_ready;
            assign engine_commit_valid = sos_commit_valid && !chain_valid && quiescent;
            assign sos_commit_ready = rst_n && !chain_valid && quiescent &&
                                      engine_commit_ready;
            assign fir_commit_valid = commit_valid && !chain_valid &&
                                      !sos_commit_valid && quiescent;
            assign commit_ready = rst_n && !chain_valid && !sos_commit_valid &&
                                  quiescent && fir_commit_ready;
            assign cfg_ready = rst_n && !any_update && fir_cfg_ready;
            assign sos_cfg_ready = rst_n && !any_update && engine_cfg_ready;

            assign fir_final = (state == FIR_ONLY) || (state == FIR_LAST);
            assign sos_final = (state == SOS_ONLY) || (state == SOS_LAST);
            assign m_valid = rst_n && ((state == BYPASS) ||
                                      (fir_final && fir_m_valid) ||
                                      (sos_final && sos_m_valid));
            assign m_data = !m_valid ? 16'sd0 :
                            (state == BYPASS) ? bypass_data :
                            fir_final ? fir_m_data : sos_m_data;
            assign m_channel = !m_valid ? 2'd0 :
                               (state == BYPASS) ? bypass_channel :
                               fir_final ? fir_m_channel : sos_m_channel;
            assign retire = m_valid && m_ready;
            assign slot_available = quiescent || retire;
            assign first_ready = (active_mode == 3'd0) ? 1'b1 :
                                 ((active_mode == 3'd1) ||
                                  (active_mode == 3'd3)) ? fir_s_ready : sos_s_ready;
            assign s_ready = rst_n && !any_update && slot_available && first_ready;
            assign sample_accept = s_valid && s_ready;

            assign fir_to_sos = (state == FIR_FIRST) && fir_m_valid && sos_s_ready;
            assign sos_to_fir = (state == SOS_FIRST) && sos_m_valid && fir_s_ready;
            assign fir_s_valid = ((active_mode == 3'd1 || active_mode == 3'd3) &&
                                  sample_accept) ||
                                 ((state == SOS_FIRST) && sos_m_valid);
            assign fir_s_data = (state == SOS_FIRST) ? sos_m_data : s_data;
            assign fir_s_channel = (state == SOS_FIRST) ? sos_m_channel : s_channel;
            // The single reservation guarantees the second block is empty.
            assign fir_m_ready = rst_n && ((fir_final && m_ready) ||
                                          (state == FIR_FIRST));
            assign sos_s_valid = ((active_mode == 3'd2 || active_mode == 3'd4) &&
                                  sample_accept) ||
                                 ((state == FIR_FIRST) && fir_m_valid);
            assign sos_s_data = (state == FIR_FIRST) ? fir_m_data : s_data;
            assign sos_s_channel = (state == FIR_FIRST) ? fir_m_channel : s_channel;
            assign sos_m_ready = rst_n && ((sos_final && m_ready) ||
                                          (state == SOS_FIRST));

            billet_f2_top #(
                .TAP_COUNT(TAP_COUNT),
                .MAC_LANES(MAC_LANES)
            ) u_fir (
                .clk(clk),
                .rst_n(rst_n),
                .s_data(fir_s_data),
                .s_channel(fir_s_channel),
                .s_valid(fir_s_valid),
                .s_ready(fir_s_ready),
                .m_data(fir_m_data),
                .m_channel(fir_m_channel),
                .m_valid(fir_m_valid),
                .m_ready(fir_m_ready),
                .cfg_valid(cfg_valid && !any_update),
                .cfg_ready(fir_cfg_ready),
                .cfg_channel(cfg_channel),
                .cfg_addr(cfg_addr),
                .cfg_data(cfg_data),
                .commit_valid(fir_commit_valid),
                .commit_ready(fir_commit_ready),
                .commit_channel(commit_channel)
            );

            billet_f3_sos_engine #(.SOS_COUNT(SOS_COUNT)) u_sos (
                .clk(clk),
                .rst_n(rst_n),
                .s_data(sos_s_data),
                .s_channel(sos_s_channel),
                .s_valid(sos_s_valid),
                .s_ready(sos_s_ready),
                .sections(active_sections),
                .m_data(sos_m_data),
                .m_channel(sos_m_channel),
                .m_valid(sos_m_valid),
                .m_ready(sos_m_ready),
                .cfg_valid(sos_cfg_valid && !any_update),
                .cfg_ready(engine_cfg_ready),
                .cfg_channel(sos_cfg_channel),
                .cfg_section(sos_cfg_section),
                .cfg_addr(sos_cfg_addr),
                .cfg_data(sos_cfg_data),
                .commit_valid(engine_commit_valid),
                .commit_ready(engine_commit_ready),
                .commit_channel(sos_commit_channel),
                .clear_all(chain_fire)
            );

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    state <= IDLE;
                    active_mode <= 3'd1;
                    active_sections <= 3'd0;
                    bypass_data <= 16'sd0;
                    bypass_channel <= 2'd0;
                end else begin
                    if (chain_fire) begin
                        active_mode <= chain_mode;
                        active_sections <= chain_sections;
                    end
                    if (sample_accept) begin
                        case (active_mode)
                            3'd0: state <= BYPASS;
                            3'd1: state <= FIR_ONLY;
                            3'd2: state <= SOS_ONLY;
                            3'd3: state <= FIR_FIRST;
                            3'd4: state <= SOS_FIRST;
                            default: state <= IDLE;
                        endcase
                        if (active_mode == 3'd0) begin
                            bypass_data <= s_data;
                            bypass_channel <= s_channel;
                        end
                    end else if (retire) begin
                        state <= IDLE;
                        bypass_data <= 16'sd0;
                        bypass_channel <= 2'd0;
                    end else if (fir_to_sos) begin
                        state <= SOS_LAST;
                    end else if (sos_to_fir) begin
                        state <= FIR_LAST;
                    end
                end
            end
        end else begin : g_unsupported
            initial begin
                $fatal(1, "PARAMETER ERROR: TAP_COUNT must be 1, 8, 16, 32 or 64; MAC_LANES must be 1, 2 or 4; SOS_COUNT must be 1, 2, 3 or 4");
            end
            assign s_ready = 1'b0;
            assign m_data = 16'sd0;
            assign m_channel = 2'd0;
            assign m_valid = 1'b0;
            assign cfg_ready = 1'b0;
            assign commit_ready = 1'b0;
            assign sos_cfg_ready = 1'b0;
            assign sos_commit_ready = 1'b0;
            assign chain_ready = 1'b0;
        end
    endgenerate

endmodule
