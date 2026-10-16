module billet_f3_sos_engine #(
    parameter integer SOS_COUNT = 2
) (
    input  wire clk,
    input  wire rst_n,
    input  wire signed [15:0] s_data,
    input  wire [1:0] s_channel,
    input  wire s_valid,
    output wire s_ready,
    input  wire [2:0] sections,
    output wire signed [15:0] m_data,
    output wire [1:0] m_channel,
    output wire m_valid,
    input  wire m_ready,
    input  wire cfg_valid,
    output wire cfg_ready,
    input  wire [1:0] cfg_channel,
    input  wire [1:0] cfg_section,
    input  wire [2:0] cfg_addr,
    input  wire signed [17:0] cfg_data,
    input  wire commit_valid,
    output wire commit_ready,
    input  wire [1:0] commit_channel,
    input  wire clear_all
);

    generate
        if ((SOS_COUNT >= 1) && (SOS_COUNT <= 4)) begin : g_supported
            logic busy;
            logic output_valid;
            logic signed [15:0] output_data;
            logic [1:0] output_channel;
            logic [1:0] channel;
            logic bank;
            logic [1:0] section_index;
            logic [2:0] section_count;
            logic signed [31:0] section_input;
            logic signed [31:0] x1 [0:4*SOS_COUNT-1];
            logic signed [31:0] x2 [0:4*SOS_COUNT-1];
            logic signed [31:0] y1 [0:4*SOS_COUNT-1];
            logic signed [31:0] y2 [0:4*SOS_COUNT-1];
            wire [3:0] active_banks;
            wire [3:0] staging_complete;
            wire [89:0] coefficients;
            wire [31:0] state_index;
            wire signed [17:0] b0;
            wire signed [17:0] b1;
            wire signed [17:0] b2;
            wire signed [17:0] a1;
            wire signed [17:0] a2;
            wire signed [49:0] product_b0;
            wire signed [49:0] product_b1;
            wire signed [49:0] product_b2;
            wire signed [49:0] product_a1;
            wire signed [49:0] product_a2;
            wire signed [52:0] extended_b0;
            wire signed [52:0] extended_b1;
            wire signed [52:0] extended_b2;
            wire signed [52:0] extended_a1;
            wire signed [52:0] extended_a2;
            wire signed [52:0] accumulator;
            wire signed [31:0] quantized_y;
            wire signed [15:0] cascade_result;
            wire sample_accept;
            wire commit_fire;
            integer i;

            assign m_valid = rst_n && output_valid;
            assign m_data = m_valid ? output_data : 16'sd0;
            assign m_channel = m_valid ? output_channel : 2'd0;
            assign s_ready = rst_n && !busy && (!output_valid || m_ready) &&
                             !commit_valid && !clear_all &&
                             (sections >= 1) && (sections <= SOS_COUNT);
            assign cfg_ready = rst_n && !commit_valid && !clear_all &&
                               (cfg_section < SOS_COUNT) && (cfg_addr < 5);
            assign commit_ready = rst_n && !busy && !output_valid && !clear_all &&
                                  staging_complete[commit_channel];
            assign sample_accept = s_valid && s_ready;
            assign commit_fire = commit_valid && commit_ready;
            assign state_index = channel*SOS_COUNT + section_index;
            assign b0 = coefficients[0 +: 18];
            assign b1 = coefficients[18 +: 18];
            assign b2 = coefficients[36 +: 18];
            assign a1 = coefficients[54 +: 18];
            assign a2 = coefficients[72 +: 18];
            assign product_b0 = b0 * section_input;
            assign product_b1 = b1 * x1[state_index];
            assign product_b2 = b2 * x2[state_index];
            assign product_a1 = a1 * y1[state_index];
            assign product_a2 = a2 * y2[state_index];
            assign extended_b0 = {{3{product_b0[49]}}, product_b0};
            assign extended_b1 = {{3{product_b1[49]}}, product_b1};
            assign extended_b2 = {{3{product_b2[49]}}, product_b2};
            assign extended_a1 = {{3{product_a1[49]}}, product_a1};
            assign extended_a2 = {{3{product_a2[49]}}, product_a2};
            assign accumulator = extended_b0 + extended_b1 + extended_b2 -
                                 extended_a1 - extended_a2;

            billet_f3_sos_coeff_bank #(.SOS_COUNT(SOS_COUNT)) u_coeff_bank (
                .clk(clk),
                .rst_n(rst_n),
                .cfg_write(cfg_valid && cfg_ready),
                .cfg_channel(cfg_channel),
                .cfg_section(cfg_section),
                .cfg_addr(cfg_addr),
                .cfg_data(cfg_data),
                .commit(commit_fire),
                .commit_channel(commit_channel),
                .active_banks(active_banks),
                .staging_complete(staging_complete),
                .read_channel(channel),
                .read_section(section_index),
                .read_bank(bank),
                .coefficients(coefficients)
            );

            billet_f3_round_sat #(.IN_W(53), .SHIFT(16), .OUT_W(32)) u_state_round (
                .value(accumulator),
                .result(quantized_y)
            );
            billet_f3_round_sat #(.IN_W(32), .SHIFT(8), .OUT_W(16)) u_output_round (
                .value(quantized_y),
                .result(cascade_result)
            );

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    busy <= 1'b0;
                    output_valid <= 1'b0;
                    output_data <= 16'sd0;
                    output_channel <= 2'd0;
                    channel <= 2'd0;
                    bank <= 1'b0;
                    section_index <= 2'd0;
                    section_count <= 3'd0;
                    section_input <= 32'sd0;
                end else begin
                    if (m_valid && m_ready) begin
                        output_valid <= 1'b0;
                        output_data <= 16'sd0;
                        output_channel <= 2'd0;
                    end
                    if (sample_accept) begin
                        busy <= 1'b1;
                        channel <= s_channel;
                        bank <= active_banks[s_channel];
                        section_index <= 2'd0;
                        section_count <= sections;
                        section_input <= $signed({{16{s_data[15]}}, s_data}) <<< 8;
                    end else if (busy) begin
                        if (({1'b0, section_index} + 3'd1) == section_count) begin
                            busy <= 1'b0;
                            output_valid <= 1'b1;
                            output_data <= cascade_result;
                            output_channel <= channel;
                            section_index <= 2'd0;
                            section_input <= 32'sd0;
                        end else begin
                            section_index <= section_index + 2'd1;
                            section_input <= quantized_y;
                        end
                    end
                end
            end

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    for (i = 0; i < 4*SOS_COUNT; i = i + 1) begin
                        x1[i] <= 32'sd0;
                        x2[i] <= 32'sd0;
                        y1[i] <= 32'sd0;
                        y2[i] <= 32'sd0;
                    end
                end else if (clear_all) begin
                    for (i = 0; i < 4*SOS_COUNT; i = i + 1) begin
                        x1[i] <= 32'sd0;
                        x2[i] <= 32'sd0;
                        y1[i] <= 32'sd0;
                        y2[i] <= 32'sd0;
                    end
                end else if (commit_fire) begin
                    for (i = 0; i < SOS_COUNT; i = i + 1) begin
                        x1[commit_channel*SOS_COUNT+i] <= 32'sd0;
                        x2[commit_channel*SOS_COUNT+i] <= 32'sd0;
                        y1[commit_channel*SOS_COUNT+i] <= 32'sd0;
                        y2[commit_channel*SOS_COUNT+i] <= 32'sd0;
                    end
                end else if (busy) begin
                    x2[state_index] <= x1[state_index];
                    x1[state_index] <= section_input;
                    y2[state_index] <= y1[state_index];
                    y1[state_index] <= quantized_y;
                end
            end
        end else begin : g_unsupported
            initial begin
                $fatal(1, "PARAMETER ERROR: SOS_COUNT must be 1, 2, 3 or 4");
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
