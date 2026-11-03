module billet_f4_src #(
    parameter integer SRC_TAPS = 32,
    localparam integer AW = (SRC_TAPS > 1) ? $clog2(SRC_TAPS) : 1,
    localparam integer ACC_W = 34 + ((SRC_TAPS > 1) ? $clog2(SRC_TAPS) : 0)
) (
    input wire clk, rst_n, clear_all,
    input wire s_valid,
    output wire s_ready,
    input wire signed [15:0] s_data,
    input wire [1:0] s_channel,
    output wire m_valid,
    input wire m_ready,
    output wire signed [15:0] m_data,
    output wire [1:0] m_channel,
    input wire cfg_valid,
    output wire cfg_ready,
    input wire [1:0] cfg_channel,
    input wire [2:0] cfg_phase,
    input wire [AW-1:0] cfg_tap,
    input wire signed [17:0] cfg_data,
    input wire commit_valid,
    output wire commit_ready,
    input wire [1:0] commit_channel,
    input wire [3:0] ratio_l, ratio_m,
    output wire idle
);
    generate if ((SRC_TAPS == 1) || (SRC_TAPS == 8) ||
                 (SRC_TAPS == 16) || (SRC_TAPS == 32) ||
                 (SRC_TAPS == 64)) begin : g_supported
        localparam [2:0] IDLE=0, CALC=1, OUTPUT=2, WRAP=3;
        reg [2:0] state;
        (* ramstyle = "M20K", nomem2reg *)
        reg signed [17:0] coefficients [0:64*SRC_TAPS-1];
        (* ramstyle = "M20K", nomem2reg *)
        reg signed [15:0] history [0:4*SRC_TAPS-1];
        reg [8*SRC_TAPS-1:0] written [0:3];
        reg [3:0] active, default_bank;
        reg [3:0] l [0:3], m [0:3];
        reg [AW-1:0] head [0:3];
        reg [AW:0] fill [0:3];
        reg [2:0] phase [0:3];
        reg [3:0] gap [0:3];
        reg [1:0] channel;
        reg [AW:0] tap;
        reg read_valid, product_valid, read_last, product_last, round_pending;
        reg signed [15:0] sample_q;
        reg signed [17:0] coefficient_q;
        reg signed [33:0] product_q;
        reg signed [ACC_W-1:0] accumulator;
        reg signed [15:0] output_data;
        reg [4:0] phase_work;
        reg [3:0] distance;
        wire [7:0] phase_complete;
        wire complete;
        wire cfg_fire = cfg_valid && cfg_ready;
        wire commit_fire = commit_valid && commit_ready;
        wire sample_fire = s_valid && s_ready;
        wire read_enable = (state == CALC) && (tap < SRC_TAPS);
        wire [AW-1:0] history_address = head[channel] - tap[AW-1:0];
        wire [AW-1:0] next_head = (SRC_TAPS == 1) ? {AW{1'b0}} : head[s_channel] + 1'b1;
        wire signed [15:0] rounded;
        wire signed [ACC_W-1:0] extended_product =
            {{(ACC_W-34){product_q[33]}},product_q};
        genvar p;
        for (p=0; p<8; p=p+1) begin : g_complete
            assign phase_complete[p] = (p >= ratio_l) ||
                (&written[commit_channel][p*SRC_TAPS +: SRC_TAPS]);
        end
        assign complete = &phase_complete;
        assign idle = rst_n && (state == IDLE);
        assign cfg_ready = rst_n && !clear_all && !commit_valid && (cfg_tap < SRC_TAPS);
        assign commit_ready = idle && !clear_all && complete &&
                              (ratio_l >= 1) && (ratio_l <= 8) &&
                              (ratio_m >= 1) && (ratio_m <= 8);
        assign s_ready = idle && !clear_all && !commit_valid;
        assign m_valid = rst_n && (state == OUTPUT);
        assign m_data = m_valid ? output_data : 16'sd0;
        assign m_channel = m_valid ? channel : 2'd0;

        billet_f3_round_sat #(.IN_W(ACC_W),.SHIFT(16),.OUT_W(16)) u_round (
            .value(accumulator), .result(rounded)
        );

        always @(posedge clk) begin
            if (cfg_fire)
                coefficients[((!active[cfg_channel])*4*8 +
                              cfg_channel*8 + cfg_phase)*SRC_TAPS + cfg_tap] <= cfg_data;
            if (sample_fire)
                history[s_channel*SRC_TAPS + next_head] <= s_data;
            if (read_enable) begin
                sample_q <= (tap < fill[channel]) ?
                    history[channel*SRC_TAPS +
                            ((SRC_TAPS == 1) ? 0 : history_address)] : 16'sd0;
                if (default_bank[channel])
                    coefficient_q <= ((phase[channel] == 0) && (tap == 0)) ?
                                      18'sd65536 : 18'sd0;
                else
                    coefficient_q <= coefficients[(active[channel]*4*8 +
                        channel*8 + phase[channel])*SRC_TAPS + tap];
            end
        end

        integer c;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                state <= IDLE;
                active <= 0;
                default_bank <= 4'b1111;
                channel <= 0;
                tap <= 0;
                read_valid <= 0; product_valid <= 0;
                read_last <= 0; product_last <= 0; round_pending <= 0;
                product_q <= 0; accumulator <= 0; output_data <= 0;
                phase_work <= 0; distance <= 0;
                for (c=0;c<4;c=c+1) begin
                    written[c] <= 0;
                    l[c] <= 1; m[c] <= 1;
                    head[c] <= 0; fill[c] <= 0; phase[c] <= 0; gap[c] <= 0;
                end
            end else begin
                if (cfg_fire)
                    written[cfg_channel][cfg_phase*SRC_TAPS+cfg_tap] <= 1;
                if (commit_fire) begin
                    active[commit_channel] <= !active[commit_channel];
                    default_bank[commit_channel] <= 0;
                    written[commit_channel] <= 0;
                    l[commit_channel] <= ratio_l;
                    m[commit_channel] <= ratio_m;
                    head[commit_channel] <= 0; fill[commit_channel] <= 0;
                    phase[commit_channel] <= 0; gap[commit_channel] <= 0;
                end
                if (clear_all) begin
                    state <= IDLE;
                    read_valid <= 0; product_valid <= 0; round_pending <= 0;
                    output_data <= 0;
                    for (c=0;c<4;c=c+1) begin
                        head[c] <= 0; fill[c] <= 0; phase[c] <= 0; gap[c] <= 0;
                    end
                end else begin
                    case (state)
                        IDLE: if (sample_fire) begin
                            head[s_channel] <= next_head;
                            if (fill[s_channel] < SRC_TAPS)
                                fill[s_channel] <= fill[s_channel] + 1'b1;
                            if (gap[s_channel] != 0)
                                gap[s_channel] <= gap[s_channel] - 1'b1;
                            else begin
                                channel <= s_channel;
                                tap <= 0; accumulator <= 0;
                                read_valid <= 0; product_valid <= 0; round_pending <= 0;
                                state <= CALC;
                            end
                        end
                        CALC: begin
                            read_valid <= read_enable;
                            read_last <= (tap == SRC_TAPS-1);
                            product_valid <= read_valid;
                            product_last <= read_last;
                            if (read_enable) tap <= tap + 1'b1;
                            if (read_valid) product_q <= sample_q * coefficient_q;
                            if (product_valid)
                                accumulator <= accumulator + extended_product;
                            round_pending <= product_valid && product_last;
                            if (round_pending) begin
                                output_data <= rounded;
                                state <= OUTPUT;
                            end
                        end
                        OUTPUT: if (m_ready) begin
                            phase_work <= {2'd0,phase[channel]} + {1'b0,m[channel]};
                            distance <= 0;
                            output_data <= 0;
                            state <= WRAP;
                        end
                        WRAP: begin
                            if (phase_work >= l[channel]) begin
                                phase_work <= phase_work - {1'b0,l[channel]};
                                distance <= distance + 1'b1;
                            end else begin
                                phase[channel] <= phase_work[2:0];
                                if (distance == 0) begin
                                    tap <= 0; accumulator <= 0;
                                    read_valid <= 0; product_valid <= 0; round_pending <= 0;
                                    state <= CALC;
                                end else begin
                                    gap[channel] <= distance - 1'b1;
                                    state <= IDLE;
                                end
                            end
                        end
                        default: state <= IDLE;
                    endcase
                end
            end
        end
    end else begin : g_unsupported
        initial $fatal(1,"PARAMETER ERROR: SRC_TAPS must be 1, 8, 16, 32 or 64");
        assign s_ready=0;
        assign m_valid=0;
        assign m_data=0;
        assign m_channel=0;
        assign cfg_ready=0;
        assign commit_ready=0;
        assign idle=0;
    end endgenerate
endmodule
