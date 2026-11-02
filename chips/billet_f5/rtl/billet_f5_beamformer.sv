module billet_f5_beamformer #(
    parameter integer DELAY_DEPTH=8,
    localparam integer DELAY_W=(DELAY_DEPTH>1)?$clog2(DELAY_DEPTH):1
) (
    input wire clk, rst_n,
    input wire commit_pending, commit_fire,
    input wire cfg_valid,
    output wire cfg_ready,
    input wire [2:0] cfg_mic,
    input wire [DELAY_W+7:0] cfg_delay,
    output wire staging_complete,
    input wire s_valid,
    output wire s_ready,
    input wire [127:0] s_mics,
    input wire signed [15:0] s_far,
    output wire m_valid,
    input wire m_ready,
    output wire signed [15:0] m_data, m_far,
    output wire idle
);
    generate if ((DELAY_DEPTH==1)||(DELAY_DEPTH==8)||
                 (DELAY_DEPTH==17)||(DELAY_DEPTH==32)) begin : g_supported
        (* nomem2reg *) reg signed [15:0] history [0:8*DELAY_DEPTH-1];
        reg [DELAY_W-1:0] head;
        reg [DELAY_W:0] fill;
        reg [DELAY_W+7:0] delays [0:15];
        reg active;
        reg [7:0] written, valid_q;
        reg signed [15:0] far_q [0:7];
        reg signed [15:0] u_q [0:7], v_q [0:7], microphone_q [0:7];
        reg [7:0] fraction_q [0:7];
        reg signed [24:0] product_u [0:7], product_v [0:7];
        reg signed [25:0] interpolation [0:7];
        reg signed [16:0] pairs [0:3];
        reg signed [17:0] quartets [0:1];
        reg signed [18:0] total;
        reg signed [15:0] result_q;
        wire advance=!valid_q[7] || m_ready;
        wire accept=s_valid && s_ready;
        wire signed [15:0] rounded_total;
        assign cfg_ready=rst_n && !commit_pending && !commit_fire &&
                         (cfg_delay[DELAY_W+7:8]<DELAY_DEPTH);
        assign staging_complete=rst_n && (&written);
        assign s_ready=rst_n && !commit_pending && !commit_fire && advance;
        assign m_valid=rst_n && valid_q[7];
        assign m_data=m_valid?result_q:16'sd0;
        assign m_far=m_valid?far_q[7]:16'sd0;
        assign idle=rst_n && !(|valid_q);
        billet_f3_round_sat #(.IN_W(19),.SHIFT(3),.OUT_W(16)) u_sum_round (
            .value(total),.result(rounded_total));
        genvar mic;
        for (mic=0;mic<8;mic=mic+1) begin : g_microphone
            wire [DELAY_W+7:0] delay_value=delays[{active,3'b000}+mic];
            wire [DELAY_W:0] age_u={1'b0,delay_value[DELAY_W+7:8]};
            wire [DELAY_W:0] age_v=age_u+1'b1;
            wire [DELAY_W:0] address_u=({1'b0,head}>=age_u)?
                ({1'b0,head}-age_u):({1'b0,head}+DELAY_DEPTH-age_u);
            wire [DELAY_W:0] address_v=({1'b0,head}>=age_v)?
                ({1'b0,head}-age_v):({1'b0,head}+DELAY_DEPTH-age_v);
            wire signed [15:0] selected_u=(age_u==0)?s_mics[16*mic+:16]:
                (fill>=age_u)?history[mic*DELAY_DEPTH+address_u]:16'sd0;
            wire signed [15:0] selected_v=(fill>=age_v)?
                history[mic*DELAY_DEPTH+address_v]:16'sd0;
            wire signed [9:0] complement=10'sd256-$signed({2'b00,fraction_q[mic]});
            wire signed [8:0] fraction={1'b0,fraction_q[mic]};
            wire signed [15:0] rounded_microphone;
            billet_f3_round_sat #(.IN_W(26),.SHIFT(8),.OUT_W(16)) u_round (
                .value(interpolation[mic]),.result(rounded_microphone));
            always @(posedge clk) begin
                if (accept) history[mic*DELAY_DEPTH+head]<=s_mics[16*mic+:16];
            end
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    u_q[mic]<=0; v_q[mic]<=0; fraction_q[mic]<=0;
                    product_u[mic]<=0; product_v[mic]<=0;
                    interpolation[mic]<=0; microphone_q[mic]<=0;
                end else if (commit_fire) begin
                    u_q[mic]<=0; v_q[mic]<=0; fraction_q[mic]<=0;
                    product_u[mic]<=0; product_v[mic]<=0;
                    interpolation[mic]<=0; microphone_q[mic]<=0;
                end else if (advance) begin
                    u_q[mic]<=selected_u; v_q[mic]<=selected_v;
                    fraction_q[mic]<=delay_value[7:0];
                    product_u[mic]<=u_q[mic]*complement;
                    product_v[mic]<=v_q[mic]*fraction;
                    interpolation[mic]<=$signed({product_u[mic][24],product_u[mic]})+
                        $signed({product_v[mic][24],product_v[mic]});
                    microphone_q[mic]<=rounded_microphone;
                end
            end
        end
        integer i;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                head<=0; fill<=0; active<=0; written<=0; valid_q<=0;
                total<=0; result_q<=0;
                for (i=0;i<16;i=i+1) delays[i]<=0;
                for (i=0;i<8;i=i+1) far_q[i]<=0;
                for (i=0;i<4;i=i+1) pairs[i]<=0;
                for (i=0;i<2;i=i+1) quartets[i]<=0;
            end else if (commit_fire) begin
                head<=0; fill<=0; active<=!active; written<=0; valid_q<=0;
                total<=0; result_q<=0;
                for (i=0;i<8;i=i+1) far_q[i]<=0;
                for (i=0;i<4;i=i+1) pairs[i]<=0;
                for (i=0;i<2;i=i+1) quartets[i]<=0;
            end else begin
                if (cfg_valid && cfg_ready) begin
                    delays[{!active,cfg_mic}]<=cfg_delay;
                    written[cfg_mic]<=1;
                end
                if (accept) begin
                    if (head==DELAY_DEPTH-1) head<=0;
                    else head<=head+1'b1;
                    if (fill<DELAY_DEPTH) fill<=fill+1'b1;
                end
                if (advance) begin
                    valid_q<={valid_q[6:0],accept};
                    far_q[0]<=s_far;
                    for (i=1;i<8;i=i+1) far_q[i]<=far_q[i-1];
                    for (i=0;i<4;i=i+1)
                        pairs[i]<=$signed({microphone_q[2*i][15],microphone_q[2*i]})+
                            $signed({microphone_q[2*i+1][15],microphone_q[2*i+1]});
                    for (i=0;i<2;i=i+1)
                        quartets[i]<=$signed({pairs[2*i][16],pairs[2*i]})+
                            $signed({pairs[2*i+1][16],pairs[2*i+1]});
                    total<=$signed({quartets[0][17],quartets[0]})+
                           $signed({quartets[1][17],quartets[1]});
                    result_q<=rounded_total;
                end
            end
        end
    end else begin : g_unsupported
        initial $fatal(1,"PARAMETER ERROR: DELAY_DEPTH must be 1, 8, 17 or 32");
        assign cfg_ready=0; assign staging_complete=0; assign s_ready=0;
        assign m_valid=0; assign m_data=0; assign m_far=0; assign idle=0;
    end endgenerate
endmodule
