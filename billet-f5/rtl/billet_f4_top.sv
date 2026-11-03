module billet_f4_top #(
    parameter integer TAP_COUNT=32,
    parameter integer MAC_LANES=1,
    parameter integer SOS_COUNT=2,
    parameter integer SRC_TAPS=32,
    localparam integer ADDR_W=(TAP_COUNT>1)?$clog2(TAP_COUNT):1,
    localparam integer SRC_AW=(SRC_TAPS>1)?$clog2(SRC_TAPS):1
) (
    input wire clk,rst_n,
    input wire signed [15:0] s_data,
    input wire [1:0] s_channel,
    input wire s_valid,
    output wire s_ready,
    output wire signed [15:0] m_data,
    output wire [1:0] m_channel,
    output wire m_valid,
    input wire m_ready,
    input wire cfg_valid,
    output wire cfg_ready,
    input wire [1:0] cfg_channel,
    input wire [ADDR_W-1:0] cfg_addr,
    input wire signed [15:0] cfg_data,
    input wire commit_valid,
    output wire commit_ready,
    input wire [1:0] commit_channel,
    input wire sos_cfg_valid,
    output wire sos_cfg_ready,
    input wire [1:0] sos_cfg_channel,
    input wire [1:0] sos_cfg_section,
    input wire [2:0] sos_cfg_addr,
    input wire signed [17:0] sos_cfg_data,
    input wire sos_commit_valid,
    output wire sos_commit_ready,
    input wire [1:0] sos_commit_channel,
    input wire chain_valid,
    output wire chain_ready,
    input wire [2:0] chain_mode,chain_sections,
    input wire feature_valid,
    output wire feature_ready,
    input wire [1:0] feature_mode,
    input wire src_cfg_valid,
    output wire src_cfg_ready,
    input wire [1:0] src_cfg_channel,
    input wire [2:0] src_cfg_phase,
    input wire [SRC_AW-1:0] src_cfg_tap,
    input wire signed [17:0] src_cfg_data,
    input wire src_commit_valid,
    output wire src_commit_ready,
    input wire [1:0] src_commit_channel,
    input wire [3:0] src_l,src_m,
    input wire p_valid,
    output wire p_ready,
    input wire [31:0] p_power,
    input wire [1:0] p_channel,
    input wire [8:0] p_bin,
    input wire p_last,
    output wire t_valid,
    input wire t_ready,
    output wire [15:0] t_data,
    output wire [1:0] t_channel,
    output wire [5:0] t_band,
    output wire t_last,
    output wire feature_error
);
    generate if (((TAP_COUNT==1)||(TAP_COUNT==8)||(TAP_COUNT==16)||
                  (TAP_COUNT==32)||(TAP_COUNT==64)) &&
                 ((MAC_LANES==1)||(MAC_LANES==2)||(MAC_LANES==4)) &&
                 ((SOS_COUNT>=1)&&(SOS_COUNT<=4)) &&
                 ((SRC_TAPS==1)||(SRC_TAPS==8)||(SRC_TAPS==16)||
                  (SRC_TAPS==32)||(SRC_TAPS==64))) begin : g_supported
        reg [1:0] mode;
        reg legacy_reserved;
        wire ls_ready, lm_valid, lm_ready;
        wire signed [15:0] lm_data;
        wire [1:0] lm_channel;
        wire lc_ready, lcommit_ready, lsc_ready, lscommit_ready, lchain_ready;
        wire src_s_ready, src_valid, src_ready, src_idle;
        wire signed [15:0] src_data;
        wire [1:0] src_channel;
        wire src_write_ready, src_swap_ready;
        wire spectrum_s_ready, spectrum_valid, spectrum_ready, spectrum_idle;
        wire [31:0] spectrum_power;
        wire [1:0] spectrum_channel;
        wire [8:0] spectrum_bin;
        wire spectrum_last;
        wire mel_ready, energy_valid, energy_ready, mel_idle, mel_error;
        wire [47:0] energy;
        wire [1:0] energy_channel;
        wire [5:0] energy_band;
        wire energy_last;
        wire log_valid, log_ready, log_idle;
        wire [15:0] log_data;
        wire [1:0] log_channel;
        wire [5:0] log_band;
        wire log_last;
        wire new_empty=src_idle && spectrum_idle && mel_idle && log_idle;
        wire drained=!legacy_reserved && new_empty;
        wire feature_fire=feature_valid && feature_ready;
        wire src_fire=src_commit_valid && src_commit_ready;
        wire control_enable=!feature_valid && !src_commit_valid;
        wire any_request=feature_valid || src_commit_valid || chain_valid ||
                         sos_commit_valid || commit_valid;
        wire inherited_enable=control_enable && new_empty;
        wire input_slot=(mode==0) || (!legacy_reserved && src_idle);
        assign feature_ready=rst_n && drained;
        assign src_commit_ready=rst_n && !feature_valid && drained && src_swap_ready;
        assign chain_ready=rst_n && inherited_enable && lchain_ready;
        assign sos_commit_ready=rst_n && inherited_enable && lscommit_ready;
        assign commit_ready=rst_n && inherited_enable && lcommit_ready;
        assign cfg_ready=rst_n && !any_request && lc_ready;
        assign sos_cfg_ready=rst_n && !any_request && lsc_ready;
        assign src_cfg_ready=rst_n && control_enable && src_write_ready;
        assign s_ready=rst_n && (mode!=3) && !any_request && input_slot && ls_ready;

        assign lm_ready=(mode==0)?m_ready:
                        ((mode==1)||(mode==2))?src_s_ready:1'b0;
        assign src_ready=(mode==1)?m_ready:(mode==2)?spectrum_s_ready:1'b0;
        assign m_valid=rst_n && (((mode==0)&&lm_valid)||((mode==1)&&src_valid));
        assign m_data=!m_valid?16'sd0:(mode==0)?lm_data:src_data;
        assign m_channel=!m_valid?2'd0:(mode==0)?lm_channel:src_channel;
        assign p_ready=rst_n && (mode==3) && !any_request && mel_ready;
        assign spectrum_ready=(mode==2) && mel_ready;
        assign log_ready=((mode==2)||(mode==3)) && t_ready;
        assign t_valid=rst_n && ((mode==2)||(mode==3)) && log_valid;
        assign t_data=t_valid?log_data:16'd0;
        assign t_channel=t_valid?log_channel:2'd0;
        assign t_band=t_valid?log_band:6'd0;
        assign t_last=t_valid && log_last;
        assign feature_error=rst_n && mel_error;

        billet_f3_top #(.TAP_COUNT(TAP_COUNT),.MAC_LANES(MAC_LANES),
            .SOS_COUNT(SOS_COUNT)) u_legacy (
            .clk(clk),.rst_n(rst_n),
            .s_data(s_data),.s_channel(s_channel),.s_valid(s_valid && s_ready),
            .s_ready(ls_ready),.m_data(lm_data),.m_channel(lm_channel),
            .m_valid(lm_valid),.m_ready(lm_ready),
            .cfg_valid(cfg_valid && !any_request),.cfg_ready(lc_ready),
            .cfg_channel(cfg_channel),.cfg_addr(cfg_addr),.cfg_data(cfg_data),
            .commit_valid(commit_valid && inherited_enable),.commit_ready(lcommit_ready),
            .commit_channel(commit_channel),
            .sos_cfg_valid(sos_cfg_valid && !any_request),.sos_cfg_ready(lsc_ready),
            .sos_cfg_channel(sos_cfg_channel),.sos_cfg_section(sos_cfg_section),
            .sos_cfg_addr(sos_cfg_addr),.sos_cfg_data(sos_cfg_data),
            .sos_commit_valid(sos_commit_valid && inherited_enable),
            .sos_commit_ready(lscommit_ready),.sos_commit_channel(sos_commit_channel),
            .chain_valid(chain_valid && inherited_enable),.chain_ready(lchain_ready),
            .chain_mode(chain_mode),.chain_sections(chain_sections));
        billet_f4_src #(.SRC_TAPS(SRC_TAPS)) u_src (
            .clk(clk),.rst_n(rst_n),.clear_all(feature_fire),
            .s_valid(lm_valid && ((mode==1)||(mode==2))),.s_ready(src_s_ready),
            .s_data(lm_data),.s_channel(lm_channel),
            .m_valid(src_valid),.m_ready(src_ready),.m_data(src_data),.m_channel(src_channel),
            .cfg_valid(src_cfg_valid && control_enable),.cfg_ready(src_write_ready),
            .cfg_channel(src_cfg_channel),.cfg_phase(src_cfg_phase),
            .cfg_tap(src_cfg_tap),.cfg_data(src_cfg_data),
            .commit_valid(src_commit_valid && !feature_valid && drained),
            .commit_ready(src_swap_ready),.commit_channel(src_commit_channel),
            .ratio_l(src_l),.ratio_m(src_m),.idle(src_idle));
        billet_f4_spectrum u_spectrum (
            .clk(clk),.rst_n(rst_n),.clear_all(feature_fire),
            .clear_channel_valid(src_fire),.clear_channel(src_commit_channel),
            .s_valid(src_valid && (mode==2)),.s_ready(spectrum_s_ready),
            .s_data(src_data),.s_channel(src_channel),
            .p_valid(spectrum_valid),.p_ready(spectrum_ready),
            .p_power(spectrum_power),.p_channel(spectrum_channel),
            .p_bin(spectrum_bin),.p_last(spectrum_last),.idle(spectrum_idle));
        billet_f4_mel u_mel (
            .clk(clk),.rst_n(rst_n),.clear_all(feature_fire),
            .clear_channel_valid(src_fire),.clear_channel(src_commit_channel),
            .p_valid((mode==2)?spectrum_valid:(mode==3)?(p_valid && !any_request):1'b0),
            .p_ready(mel_ready),.p_power((mode==2)?spectrum_power:p_power),
            .p_channel((mode==2)?spectrum_channel:p_channel),
            .p_bin((mode==2)?spectrum_bin:p_bin),
            .p_last((mode==2)?spectrum_last:p_last),
            .e_valid(energy_valid),.e_ready(energy_ready),.e_energy(energy),
            .e_channel(energy_channel),.e_band(energy_band),.e_last(energy_last),
            .feature_error(mel_error),.idle(mel_idle));
        billet_f4_log u_log (
            .clk(clk),.rst_n(rst_n),.s_valid(energy_valid),.s_ready(energy_ready),
            .s_energy(energy),.s_channel(energy_channel),.s_band(energy_band),
            .s_last(energy_last),.m_valid(log_valid),.m_ready(log_ready),
            .m_data(log_data),.m_channel(log_channel),.m_band(log_band),
            .m_last(log_last),.idle(log_idle));
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin mode<=0; legacy_reserved<=0; end
            else begin
                if (feature_fire) mode<=feature_mode;
                if (s_valid && s_ready) legacy_reserved<=1;
                else if (lm_valid && lm_ready) legacy_reserved<=0;
            end
        end
    end else begin : g_unsupported
        initial $fatal(1,"PARAMETER ERROR: illegal BILLET-F4 TAP_COUNT, MAC_LANES, SOS_COUNT or SRC_TAPS");
        assign s_ready=0; assign m_data=0; assign m_channel=0; assign m_valid=0;
        assign cfg_ready=0; assign commit_ready=0; assign sos_cfg_ready=0;
        assign sos_commit_ready=0; assign chain_ready=0; assign feature_ready=0;
        assign src_cfg_ready=0; assign src_commit_ready=0; assign p_ready=0;
        assign t_valid=0; assign t_data=0; assign t_channel=0; assign t_band=0;
        assign t_last=0; assign feature_error=0;
    end endgenerate
endmodule
