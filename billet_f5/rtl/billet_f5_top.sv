module billet_f5_top #(
    parameter integer TAP_COUNT=32,
    parameter integer MAC_LANES=1,
    parameter integer SOS_COUNT=2,
    parameter integer SRC_TAPS=32,
    parameter integer DELAY_DEPTH=8,
    parameter integer AEC_TAPS=16,
    localparam integer ADDR_W=(TAP_COUNT>1)?$clog2(TAP_COUNT):1,
    localparam integer SRC_AW=(SRC_TAPS>1)?$clog2(SRC_TAPS):1,
    localparam integer DELAY_W=(DELAY_DEPTH>1)?$clog2(DELAY_DEPTH):1
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
    output wire feature_error,
    input wire [127:0] a_mics,
    input wire signed [15:0] a_far,
    input wire a_valid,
    output wire a_ready,
    input wire a_cfg_valid,
    output wire a_cfg_ready,
    input wire [2:0] a_cfg_mic,
    input wire [DELAY_W+7:0] a_cfg_delay,
    input wire a_commit_valid,
    output wire a_commit_ready,
    input wire a_aec_enable, a_gate_enable,
    output wire audio_valid,
    input wire audio_ready,
    output wire signed [15:0] audio_data,
    output wire audio_frozen,
    output wire a_t_valid,
    input wire a_t_ready,
    output wire [15:0] a_t_data,
    output wire [5:0] a_t_band,
    output wire a_t_last,
    output wire vad_valid,
    input wire vad_ready,
    output wire vad_active,
    output wire [47:0] vad_energy
);
    generate if (((TAP_COUNT==1)||(TAP_COUNT==8)||(TAP_COUNT==16)||
                  (TAP_COUNT==32)||(TAP_COUNT==64)) &&
                 ((MAC_LANES==1)||(MAC_LANES==2)||(MAC_LANES==4)) &&
                 ((SOS_COUNT>=1)&&(SOS_COUNT<=4)) &&
                 ((SRC_TAPS==1)||(SRC_TAPS==8)||(SRC_TAPS==16)||
                  (SRC_TAPS==32)||(SRC_TAPS==64)) &&
                 ((DELAY_DEPTH==1)||(DELAY_DEPTH==8)||
                  (DELAY_DEPTH==17)||(DELAY_DEPTH==32)) &&
                 ((AEC_TAPS==1)||(AEC_TAPS==8)||(AEC_TAPS==16)||(AEC_TAPS==32)))
    begin : g_supported
        reg aec_enable_q, gate_enable_q, tokens_retired;
        wire beam_valid, beam_ready, beam_idle, staging_complete;
        wire signed [15:0] beam_data, beam_far;
        wire aec_valid, aec_ready, aec_idle, aec_frozen;
        wire signed [15:0] aec_data;
        wire fork_valid, fork_ready, fork_idle;
        wire signed [15:0] fork_data;
        wire spectrum_valid, spectrum_ready, spectrum_idle;
        wire [31:0] spectrum_power;
        wire [1:0] spectrum_channel;
        wire [8:0] spectrum_bin;
        wire spectrum_last;
        wire mel_ready, mel_idle, mel_error;
        wire energy_valid, energy_ready, energy_last;
        wire [47:0] energy;
        wire [1:0] energy_channel;
        wire [5:0] energy_band;
        wire log_valid, log_ready, log_last, log_idle;
        wire [15:0] log_data;
        wire [1:0] log_channel;
        wire [5:0] log_band;
        wire vad_bin_ready, vad_idle, frame_valid, frame_active;
        wire acoustic_empty=beam_idle && aec_idle && fork_idle &&
            spectrum_idle && mel_idle && log_idle && vad_idle;
        wire commit_fire=a_commit_valid && a_commit_ready;
        wire forward_frame=!gate_enable_q || frame_active;
        wire token_last_fire=log_valid && log_ready && log_last;
        wire frame_done=frame_valid && (tokens_retired || token_last_fire) &&
                        (!vad_valid || vad_ready);

        assign a_commit_ready=rst_n && staging_complete && acoustic_empty;
        assign spectrum_ready=mel_ready && vad_bin_ready;
        assign log_ready=frame_valid && !tokens_retired &&
                         (!forward_frame || a_t_ready);
        assign a_t_valid=rst_n && frame_valid && !tokens_retired &&
                         forward_frame && log_valid;
        assign a_t_data=a_t_valid?log_data:16'd0;
        assign a_t_band=a_t_valid?log_band:6'd0;
        assign a_t_last=a_t_valid && log_last;

        billet_f4_top #(.TAP_COUNT(TAP_COUNT),.MAC_LANES(MAC_LANES),
            .SOS_COUNT(SOS_COUNT),.SRC_TAPS(SRC_TAPS)) u_legacy (
            .clk(clk),.rst_n(rst_n),
            .s_data(s_data),.s_channel(s_channel),.s_valid(s_valid),.s_ready(s_ready),
            .m_data(m_data),.m_channel(m_channel),.m_valid(m_valid),.m_ready(m_ready),
            .cfg_valid(cfg_valid),.cfg_ready(cfg_ready),.cfg_channel(cfg_channel),
            .cfg_addr(cfg_addr),.cfg_data(cfg_data),
            .commit_valid(commit_valid),.commit_ready(commit_ready),
            .commit_channel(commit_channel),
            .sos_cfg_valid(sos_cfg_valid),.sos_cfg_ready(sos_cfg_ready),
            .sos_cfg_channel(sos_cfg_channel),.sos_cfg_section(sos_cfg_section),
            .sos_cfg_addr(sos_cfg_addr),.sos_cfg_data(sos_cfg_data),
            .sos_commit_valid(sos_commit_valid),.sos_commit_ready(sos_commit_ready),
            .sos_commit_channel(sos_commit_channel),
            .chain_valid(chain_valid),.chain_ready(chain_ready),
            .chain_mode(chain_mode),.chain_sections(chain_sections),
            .feature_valid(feature_valid),.feature_ready(feature_ready),
            .feature_mode(feature_mode),
            .src_cfg_valid(src_cfg_valid),.src_cfg_ready(src_cfg_ready),
            .src_cfg_channel(src_cfg_channel),.src_cfg_phase(src_cfg_phase),
            .src_cfg_tap(src_cfg_tap),.src_cfg_data(src_cfg_data),
            .src_commit_valid(src_commit_valid),.src_commit_ready(src_commit_ready),
            .src_commit_channel(src_commit_channel),.src_l(src_l),.src_m(src_m),
            .p_valid(p_valid),.p_ready(p_ready),.p_power(p_power),
            .p_channel(p_channel),.p_bin(p_bin),.p_last(p_last),
            .t_valid(t_valid),.t_ready(t_ready),.t_data(t_data),
            .t_channel(t_channel),.t_band(t_band),.t_last(t_last),
            .feature_error(feature_error));
        billet_f5_beamformer #(.DELAY_DEPTH(DELAY_DEPTH)) u_beamformer (
            .clk(clk),.rst_n(rst_n),
            .commit_pending(a_commit_valid),.commit_fire(commit_fire),
            .cfg_valid(a_cfg_valid),.cfg_ready(a_cfg_ready),
            .cfg_mic(a_cfg_mic),.cfg_delay(a_cfg_delay),
            .staging_complete(staging_complete),
            .s_valid(a_valid),.s_ready(a_ready),.s_mics(a_mics),.s_far(a_far),
            .m_valid(beam_valid),.m_ready(beam_ready),
            .m_data(beam_data),.m_far(beam_far),.idle(beam_idle));
        billet_f5_nlms #(.AEC_TAPS(AEC_TAPS)) u_aec (
            .clk(clk),.rst_n(rst_n),.clear_all(commit_fire),.enable(aec_enable_q),
            .s_valid(beam_valid),.s_ready(beam_ready),
            .s_data(beam_data),.s_far(beam_far),
            .m_valid(aec_valid),.m_ready(aec_ready),.m_data(aec_data),
            .m_frozen(aec_frozen),.idle(aec_idle));
        billet_f5_audio_fork u_audio_fork (
            .clk(clk),.rst_n(rst_n),.clear_all(commit_fire),
            .s_valid(aec_valid),.s_ready(aec_ready),.s_data(aec_data),
            .s_frozen(aec_frozen),.audio_valid(audio_valid),.audio_ready(audio_ready),
            .audio_data(audio_data),.audio_frozen(audio_frozen),
            .spectrum_valid(fork_valid),.spectrum_ready(fork_ready),
            .spectrum_data(fork_data),.idle(fork_idle));
        billet_f4_spectrum u_acoustic_spectrum (
            .clk(clk),.rst_n(rst_n),.clear_all(commit_fire),
            .clear_channel_valid(1'b0),.clear_channel(2'd0),
            .s_valid(fork_valid),.s_ready(fork_ready),.s_data(fork_data),
            .s_channel(2'd0),.p_valid(spectrum_valid),.p_ready(spectrum_ready),
            .p_power(spectrum_power),.p_channel(spectrum_channel),
            .p_bin(spectrum_bin),.p_last(spectrum_last),.idle(spectrum_idle));
        billet_f5_vad u_vad (
            .clk(clk),.rst_n(rst_n),.clear_all(commit_fire),
            .p_valid(spectrum_valid && mel_ready),.p_ready(vad_bin_ready),
            .p_power(spectrum_power),.p_bin(spectrum_bin),.p_last(spectrum_last),
            .vad_valid(vad_valid),.vad_ready(vad_ready),.vad_active(vad_active),
            .vad_energy(vad_energy),.frame_valid(frame_valid),
            .frame_active(frame_active),.frame_done(frame_done),.idle(vad_idle));
        billet_f4_mel u_acoustic_mel (
            .clk(clk),.rst_n(rst_n),.clear_all(commit_fire),
            .clear_channel_valid(1'b0),.clear_channel(2'd0),
            .p_valid(spectrum_valid && vad_bin_ready),.p_ready(mel_ready),
            .p_power(spectrum_power),.p_channel(2'd0),
            .p_bin(spectrum_bin),.p_last(spectrum_last),
            .e_valid(energy_valid),.e_ready(energy_ready),.e_energy(energy),
            .e_channel(energy_channel),.e_band(energy_band),.e_last(energy_last),
            .feature_error(mel_error),.idle(mel_idle));
        billet_f4_log u_acoustic_log (
            .clk(clk),.rst_n(rst_n),.s_valid(energy_valid),.s_ready(energy_ready),
            .s_energy(energy),.s_channel(2'd0),.s_band(energy_band),.s_last(energy_last),
            .m_valid(log_valid),.m_ready(log_ready),.m_data(log_data),
            .m_channel(log_channel),.m_band(log_band),.m_last(log_last),.idle(log_idle));

        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                aec_enable_q<=1; gate_enable_q<=1; tokens_retired<=0;
            end else if (commit_fire) begin
                aec_enable_q<=a_aec_enable; gate_enable_q<=a_gate_enable;
                tokens_retired<=0;
            end else begin
                if (frame_done) tokens_retired<=0;
                else if (token_last_fire) tokens_retired<=1;
            end
        end
    end else begin : g_unsupported
        initial $fatal(1,"PARAMETER ERROR: illegal BILLET-F5 parameter");
        assign s_ready=0; assign m_data=0; assign m_channel=0; assign m_valid=0;
        assign cfg_ready=0; assign commit_ready=0; assign sos_cfg_ready=0;
        assign sos_commit_ready=0; assign chain_ready=0; assign feature_ready=0;
        assign src_cfg_ready=0; assign src_commit_ready=0; assign p_ready=0;
        assign t_valid=0; assign t_data=0; assign t_channel=0; assign t_band=0;
        assign t_last=0; assign feature_error=0;
        assign a_ready=0; assign a_cfg_ready=0; assign a_commit_ready=0;
        assign audio_valid=0; assign audio_data=0; assign audio_frozen=0;
        assign a_t_valid=0; assign a_t_data=0; assign a_t_band=0; assign a_t_last=0;
        assign vad_valid=0; assign vad_active=0; assign vad_energy=0;
    end endgenerate
endmodule
