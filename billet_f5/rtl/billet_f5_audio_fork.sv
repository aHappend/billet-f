module billet_f5_audio_fork (
    input wire clk, rst_n, clear_all,
    input wire s_valid,
    output wire s_ready,
    input wire signed [15:0] s_data,
    input wire s_frozen,
    output wire audio_valid,
    input wire audio_ready,
    output wire signed [15:0] audio_data,
    output wire audio_frozen,
    output wire spectrum_valid,
    input wire spectrum_ready,
    output wire signed [15:0] spectrum_data,
    output wire idle
);
    reg audio_pending, spectrum_pending;
    reg signed [15:0] data_q;
    reg frozen_q;
    wire release_audio=!audio_pending || audio_ready;
    wire release_spectrum=!spectrum_pending || spectrum_ready;
    assign s_ready=rst_n && !clear_all && release_audio && release_spectrum;
    assign audio_valid=rst_n && audio_pending;
    assign audio_data=audio_valid?data_q:16'sd0;
    assign audio_frozen=audio_valid && frozen_q;
    assign spectrum_valid=rst_n && spectrum_pending;
    assign spectrum_data=spectrum_valid?data_q:16'sd0;
    assign idle=rst_n && !audio_pending && !spectrum_pending;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            audio_pending<=0; spectrum_pending<=0; data_q<=0; frozen_q<=0;
        end else if (clear_all) begin
            audio_pending<=0; spectrum_pending<=0; data_q<=0; frozen_q<=0;
        end else if (s_valid && s_ready) begin
            data_q<=s_data; frozen_q<=s_frozen;
            audio_pending<=1; spectrum_pending<=1;
        end else begin
            if (audio_pending && audio_ready) audio_pending<=0;
            if (spectrum_pending && spectrum_ready) spectrum_pending<=0;
        end
    end
endmodule
