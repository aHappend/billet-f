module billet_f5_vad (
    input wire clk, rst_n, clear_all,
    input wire p_valid,
    output wire p_ready,
    input wire [31:0] p_power,
    input wire [8:0] p_bin,
    input wire p_last,
    output wire vad_valid,
    input wire vad_ready,
    output wire vad_active,
    output wire [47:0] vad_energy,
    output wire frame_valid, frame_active,
    input wire frame_done,
    output wire idle
);
    reg [47:0] accumulator, energy_q;
    reg active_q, decision_q;
    reg [1:0] hang, closing;
    reg event_pending, frame_pending;
    wire [47:0] contribution=((p_bin>=4)&&(p_bin<=128))?
        {16'd0,p_power}:48'd0;
    wire [47:0] energy_next=accumulator+contribution;
    wire on_threshold=energy_q>=48'd65536;
    wire off_threshold=energy_q>=48'd32768;
    assign p_ready=rst_n && !clear_all && (closing==0) &&
                   !frame_pending && !event_pending;
    assign vad_valid=rst_n && event_pending;
    assign vad_active=vad_valid && decision_q;
    assign vad_energy=vad_valid?energy_q:48'd0;
    assign frame_valid=rst_n && frame_pending;
    assign frame_active=frame_valid && decision_q;
    assign idle=rst_n && (closing==0) && !event_pending && !frame_pending;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            accumulator<=0; energy_q<=0; active_q<=0; decision_q<=0;
            hang<=0; closing<=0; event_pending<=0; frame_pending<=0;
        end else if (clear_all) begin
            accumulator<=0; energy_q<=0; active_q<=0; decision_q<=0;
            hang<=0; closing<=0; event_pending<=0; frame_pending<=0;
        end else begin
            if (vad_valid && vad_ready) event_pending<=0;
            if (frame_done) frame_pending<=0;
            if (p_valid && p_ready) begin
                if (p_last) begin
                    energy_q<=energy_next; accumulator<=0; closing<=1;
                end else accumulator<=energy_next;
            end
            if (closing==1) begin
                if (on_threshold) begin
                    decision_q<=1; active_q<=1; hang<=3;
                end else if (active_q && off_threshold) begin
                    decision_q<=1; active_q<=1;
                end else if (active_q && (hang!=0)) begin
                    decision_q<=1; active_q<=1; hang<=hang-1'b1;
                end else begin decision_q<=0; active_q<=0; hang<=0; end
                closing<=2;
            end else if (closing==2) begin
                event_pending<=1; frame_pending<=1; closing<=0;
            end
        end
    end
endmodule
