module billet_f2_coeff_bank #(
    parameter integer TAP_COUNT=32,
    parameter integer MAC_LANES=1,
    localparam integer ADDR_W=(TAP_COUNT>1)?$clog2(TAP_COUNT):1
) (
    input wire clk,rst_n,cfg_write,
    input wire [1:0] cfg_channel,
    input wire [ADDR_W-1:0] cfg_addr,
    input wire signed [15:0] cfg_data,
    input wire commit,
    input wire [1:0] commit_channel,
    output reg [3:0] active_banks,
    output wire [3:0] staging_complete,
    input wire [1:0] read_channel,
    input wire read_bank,
    input wire [ADDR_W-1:0] read_base,
    output wire [16*MAC_LANES-1:0] coeff_lanes
);
    (* ramstyle = "M20K", nomem2reg *)
    reg signed [15:0] bank_zero [0:4*TAP_COUNT-1];
    (* ramstyle = "M20K", nomem2reg *)
    reg signed [15:0] bank_one [0:4*TAP_COUNT-1];
    reg [TAP_COUNT-1:0] staging_written [0:3];
    reg [TAP_COUNT-1:0] valid_zero [0:3],valid_one [0:3];
    genvar channel,lane;
    generate
    for (channel=0;channel<4;channel=channel+1) begin : g_complete
        assign staging_complete[channel]=&staging_written[channel];
    end
    endgenerate
    generate
    for (lane=0;lane<MAC_LANES;lane=lane+1) begin : g_read
        wire [ADDR_W:0] tap_address={1'b0,read_base}+lane;
        reg signed [15:0] coefficient_q;
        assign coeff_lanes[16*lane+:16]=coefficient_q;
        always @(posedge clk) begin
            if (tap_address>=TAP_COUNT) coefficient_q<=0;
            else if (read_bank)
                coefficient_q<=valid_one[read_channel][tap_address]?
                    bank_one[read_channel*TAP_COUNT+tap_address]:16'sd0;
            else
                coefficient_q<=valid_zero[read_channel][tap_address]?
                    bank_zero[read_channel*TAP_COUNT+tap_address]:16'sd0;
        end
    end
    endgenerate
    always @(posedge clk) begin
        if (rst_n && cfg_write && !commit) begin
            if (active_banks[cfg_channel])
                bank_zero[cfg_channel*TAP_COUNT+cfg_addr]<=cfg_data;
            else bank_one[cfg_channel*TAP_COUNT+cfg_addr]<=cfg_data;
        end
    end
    integer c;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active_banks<=0;
            for (c=0;c<4;c=c+1) begin
                staging_written[c]<=0; valid_zero[c]<=0; valid_one[c]<=0;
            end
        end else if (commit) begin
            active_banks[commit_channel]<=!active_banks[commit_channel];
            staging_written[commit_channel]<=0;
        end else if (cfg_write) begin
            staging_written[cfg_channel][cfg_addr]<=1;
            if (active_banks[cfg_channel]) valid_zero[cfg_channel][cfg_addr]<=1;
            else valid_one[cfg_channel][cfg_addr]<=1;
        end
    end
endmodule
