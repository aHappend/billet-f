module billet_f5_nlms #(
    parameter integer AEC_TAPS=16,
    localparam integer TAP_W=(AEC_TAPS>1)?$clog2(AEC_TAPS):1
) (
    input wire clk, rst_n, clear_all, enable,
    input wire s_valid,
    output wire s_ready,
    input wire signed [15:0] s_data, s_far,
    output wire m_valid,
    input wire m_ready,
    output wire signed [15:0] m_data,
    output wire m_frozen,
    output wire idle
);
    generate if ((AEC_TAPS==1)||(AEC_TAPS==8)||
                 (AEC_TAPS==16)||(AEC_TAPS==32)) begin : g_supported
        localparam [3:0] IDLE=0, READ=1, MULTIPLY=2, ACCUMULATE=3,
            ROUND=4, ERROR=5, GRADIENT=6, LOAD=7, DIVIDE=8,
            ADD=9, WRITE=10, FINISH=11, OUTPUT=12;
        reg [3:0] state;
        reg [TAP_W-1:0] tap;
        reg [5:0] iteration;
        reg signed [23:0] weights [0:AEC_TAPS-1];
        reg signed [15:0] history [0:AEC_TAPS-1];
        reg signed [15:0] vector_q [0:AEC_TAPS-1];
        reg signed [15:0] desired, prediction, error_q, output_q;
        reg enabled_q, frozen_q;
        reg [31:0] desired_power, far_power;
        reg [31:0] desired_square, far_square;
        reg signed [23:0] weight_operand;
        reg signed [15:0] sample_operand;
        reg signed [39:0] product_q;
        reg [31:0] square_q;
        reg signed [44:0] dot;
        reg [35:0] denominator_q;
        reg signed [31:0] gradient_q;
        reg gradient_negative;
        reg signed [24:0] leaked_q;
        reg signed [57:0] update_q;
        wire signed [15:0] rounded_prediction;
        wire signed [16:0] error_wide=
            $signed({desired[15],desired})-$signed({prediction[15],prediction});
        wire signed [15:0] saturated_error=(error_wide>17'sd32767)?16'sd32767:
            (error_wide< -17'sd32768)?16'sh8000:error_wide[15:0];
        wire signed [23:0] leakage;
        wire signed [24:0] leaked_value=
            $signed({weights[tap][23],weights[tap]})-$signed({leakage[23],leakage});
        wire signed [55:0] numerator_signed=
            $signed({{24{gradient_q[31]}},gradient_q}) <<< 21;
        wire [55:0] numerator_magnitude=numerator_signed[55]?
            (~numerator_signed+56'd1):numerator_signed;
        wire [55:0] quotient;
        wire divider_busy, divider_done;
        wire signed [56:0] signed_quotient=gradient_negative?
            -$signed({1'b0,quotient}):$signed({1'b0,quotient});
        wire signed [57:0] updated_weight=
            $signed({signed_quotient[56],signed_quotient})+
            $signed({{33{leaked_q[24]}},leaked_q});
        wire signed [23:0] saturated_weight=
            (update_q>58'sd8388607)?24'sh7fffff:
            (update_q< -58'sd8388608)?24'sh800000:update_q[23:0];
        wire [31:0] desired_power_next=desired_power-(desired_power>>5)+
            (desired_square>>5);
        wire [31:0] far_power_next=far_power-(far_power>>5)+(far_square>>5);
        wire freeze_decision=(far_power<32'd65536) ||
            ({2'b00,desired_power}>(({2'b00,far_power}<<1)+34'd65536));
        assign s_ready=rst_n && !clear_all && (state==IDLE);
        assign m_valid=rst_n && (state==OUTPUT);
        assign m_data=m_valid?output_q:16'sd0;
        assign m_frozen=m_valid && frozen_q;
        assign idle=rst_n && (state==IDLE);
        billet_f3_round_sat #(.IN_W(45),.SHIFT(22),.OUT_W(16)) u_prediction_round (
            .value(dot),.result(rounded_prediction));
        billet_f3_round_sat #(.IN_W(24),.SHIFT(16),.OUT_W(24)) u_leak_round (
            .value(weights[tap]),.result(leakage));
        billet_f5_udiv u_divider (
            .clk(clk),.rst_n(rst_n),.clear_all(clear_all),
            .start(state==LOAD),.numerator(numerator_magnitude),
            .denominator(denominator_q),.quotient(quotient),
            .busy(divider_busy),.done(divider_done));
        integer j;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                state<=IDLE; tap<=0; iteration<=0;
                desired<=0; prediction<=0; error_q<=0; output_q<=0;
                enabled_q<=0; frozen_q<=0;
                desired_power<=0; far_power<=0; desired_square<=0; far_square<=0;
                weight_operand<=0; sample_operand<=0; product_q<=0; square_q<=0;
                dot<=0; denominator_q<=36'd65536;
                gradient_q<=0; gradient_negative<=0; leaked_q<=0; update_q<=0;
                for (j=0;j<AEC_TAPS;j=j+1) begin
                    weights[j]<=0; history[j]<=0; vector_q[j]<=0;
                end
            end else if (clear_all) begin
                state<=IDLE; tap<=0; iteration<=0;
                desired<=0; prediction<=0; error_q<=0; output_q<=0;
                enabled_q<=0; frozen_q<=0;
                desired_power<=0; far_power<=0; desired_square<=0; far_square<=0;
                weight_operand<=0; sample_operand<=0; product_q<=0; square_q<=0;
                dot<=0; denominator_q<=36'd65536;
                gradient_q<=0; gradient_negative<=0; leaked_q<=0; update_q<=0;
                for (j=0;j<AEC_TAPS;j=j+1) begin
                    weights[j]<=0; history[j]<=0; vector_q[j]<=0;
                end
            end else begin
                case (state)
                    IDLE: if (s_valid && s_ready) begin
                        desired<=s_data; enabled_q<=enable; tap<=0;
                        desired_square<=s_data*s_data; far_square<=s_far*s_far;
                        vector_q[0]<=s_far; history[0]<=s_far;
                        for (j=1;j<AEC_TAPS;j=j+1) begin
                            vector_q[j]<=history[j-1]; history[j]<=history[j-1];
                        end
                        dot<=0; denominator_q<=36'd65536; state<=READ;
                    end
                    READ: begin
                        weight_operand<=weights[tap]; sample_operand<=vector_q[tap];
                        if (tap==0) begin
                            desired_power<=desired_power_next; far_power<=far_power_next;
                        end
                        state<=MULTIPLY;
                    end
                    MULTIPLY: begin
                        product_q<=weight_operand*sample_operand;
                        square_q<=sample_operand*sample_operand;
                        if (tap==0) frozen_q<=!enabled_q || freeze_decision;
                        state<=ACCUMULATE;
                    end
                    ACCUMULATE: begin
                        dot<=dot+$signed({{5{product_q[39]}},product_q});
                        denominator_q<=denominator_q+{4'b0000,square_q};
                        if (tap==AEC_TAPS-1) begin tap<=0; state<=ROUND; end
                        else begin tap<=tap+1'b1; state<=READ; end
                    end
                    ROUND: begin prediction<=rounded_prediction; state<=ERROR; end
                    ERROR: begin error_q<=saturated_error; state<=GRADIENT; end
                    GRADIENT: begin
                        gradient_q<=error_q*vector_q[tap]; state<=LOAD;
                    end
                    LOAD: begin
                        gradient_negative<=numerator_signed[55];
                        leaked_q<=leaked_value; iteration<=0; state<=DIVIDE;
                    end
                    DIVIDE: begin
                        if (iteration==55) state<=ADD;
                        else iteration<=iteration+1'b1;
                    end
                    ADD: begin update_q<=updated_weight; state<=WRITE; end
                    WRITE: begin
                        if (!frozen_q) weights[tap]<=saturated_weight;
                        if (tap==AEC_TAPS-1) state<=FINISH;
                        else begin tap<=tap+1'b1; state<=GRADIENT; end
                    end
                    FINISH: begin
                        output_q<=enabled_q?error_q:desired; state<=OUTPUT;
                    end
                    OUTPUT: if (m_ready) begin output_q<=0; state<=IDLE; end
                    default: state<=IDLE;
                endcase
            end
        end
    end else begin : g_unsupported
        initial $fatal(1,"PARAMETER ERROR: AEC_TAPS must be 1, 8, 16 or 32");
        assign s_ready=0; assign m_valid=0; assign m_data=0;
        assign m_frozen=0; assign idle=0;
    end endgenerate
endmodule
