module billet_f3_sos_coeff_bank #(
    parameter integer SOS_COUNT = 2
) (
    input  wire clk,
    input  wire rst_n,
    input  wire cfg_write,
    input  wire [1:0] cfg_channel,
    input  wire [1:0] cfg_section,
    input  wire [2:0] cfg_addr,
    input  wire signed [17:0] cfg_data,
    input  wire commit,
    input  wire [1:0] commit_channel,
    output wire [3:0] active_banks,
    output wire [3:0] staging_complete,
    input  wire [1:0] read_channel,
    input  wire [1:0] read_section,
    input  wire read_bank,
    output wire [89:0] coefficients
);

    generate
        if ((SOS_COUNT >= 1) && (SOS_COUNT <= 4)) begin : g_supported
            logic signed [17:0] bank_zero [0:4*SOS_COUNT*5-1];
            logic signed [17:0] bank_one [0:4*SOS_COUNT*5-1];
            logic [SOS_COUNT*5-1:0] staging_written [0:3];
            logic [3:0] bank_select;
            integer i;
            integer c;
            genvar channel;
            genvar coefficient;

            assign active_banks = bank_select;
            for (channel = 0; channel < 4; channel = channel + 1) begin : g_complete
                assign staging_complete[channel] = &staging_written[channel];
            end
            for (coefficient = 0; coefficient < 5;
                 coefficient = coefficient + 1) begin : g_read
                assign coefficients[18*coefficient +: 18] =
                    (read_section < SOS_COUNT) ?
                    (read_bank ?
                     bank_one[(read_channel*SOS_COUNT + read_section)*5 + coefficient] :
                     bank_zero[(read_channel*SOS_COUNT + read_section)*5 + coefficient]) :
                    18'sd0;
            end

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    bank_select <= 4'b0000;
                    for (c = 0; c < 4; c = c + 1)
                        staging_written[c] <= {(SOS_COUNT*5){1'b0}};
                    for (i = 0; i < 4*SOS_COUNT*5; i = i + 1) begin
                        bank_zero[i] <= 18'sd0;
                        bank_one[i] <= 18'sd0;
                    end
                end else if (commit) begin
                    bank_select[commit_channel] <= !bank_select[commit_channel];
                    staging_written[commit_channel] <= {(SOS_COUNT*5){1'b0}};
                end else if (cfg_write && (cfg_section < SOS_COUNT) &&
                             (cfg_addr < 5)) begin
                    if (bank_select[cfg_channel])
                        bank_zero[(cfg_channel*SOS_COUNT + cfg_section)*5 + cfg_addr]
                            <= cfg_data;
                    else
                        bank_one[(cfg_channel*SOS_COUNT + cfg_section)*5 + cfg_addr]
                            <= cfg_data;
                    staging_written[cfg_channel][cfg_section*5 + cfg_addr] <= 1'b1;
                end
            end
        end else begin : g_unsupported
            initial begin
                $fatal(1, "PARAMETER ERROR: SOS_COUNT must be 1, 2, 3 or 4");
            end
            assign active_banks = 4'd0;
            assign staging_complete = 4'd0;
            assign coefficients = 90'd0;
        end
    endgenerate

endmodule
