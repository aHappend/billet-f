module billet_f2_coeff_bank #(
    parameter integer TAP_COUNT = 32,
    parameter integer MAC_LANES = 1,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic                         cfg_write,
    input  logic [1:0]                   cfg_channel,
    input  logic [ADDR_W-1:0]            cfg_addr,
    input  logic signed [15:0]           cfg_data,
    input  logic                         commit,
    input  logic [1:0]                   commit_channel,
    output logic [3:0]                   active_banks,
    output wire [3:0]                    staging_complete,
    input  logic [1:0]                   read_channel,
    input  logic                         read_bank,
    input  logic [ADDR_W-1:0]            read_base,
    output wire [16*MAC_LANES-1:0]       coeff_lanes
);

    logic signed [15:0] bank_zero [0:4*TAP_COUNT-1];
    logic signed [15:0] bank_one [0:4*TAP_COUNT-1];
    logic [TAP_COUNT-1:0] staging_written [0:3];
    integer i;
    integer c;
    genvar channel;
    genvar lane;

    generate
        for (channel = 0; channel < 4; channel = channel + 1) begin : g_complete
            assign staging_complete[channel] = &staging_written[channel];
        end
        for (lane = 0; lane < MAC_LANES; lane = lane + 1) begin : g_read
            wire [31:0] tap_addr;
            assign tap_addr = read_base + lane;
            assign coeff_lanes[16*lane +: 16] =
                (tap_addr < TAP_COUNT) ?
                (read_bank ? bank_one[read_channel*TAP_COUNT + tap_addr] :
                             bank_zero[read_channel*TAP_COUNT + tap_addr]) :
                16'sd0;
        end
    endgenerate

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active_banks <= 4'b0000;
            for (c = 0; c < 4; c = c + 1)
                staging_written[c] <= {TAP_COUNT{1'b0}};
            for (i = 0; i < 4*TAP_COUNT; i = i + 1) begin
                bank_zero[i] <= 16'sd0;
                bank_one[i] <= 16'sd0;
            end
        end else if (commit) begin
            active_banks[commit_channel] <= !active_banks[commit_channel];
            staging_written[commit_channel] <= {TAP_COUNT{1'b0}};
        end else if (cfg_write) begin
            if (active_banks[cfg_channel])
                bank_zero[cfg_channel*TAP_COUNT + cfg_addr] <= cfg_data;
            else
                bank_one[cfg_channel*TAP_COUNT + cfg_addr] <= cfg_data;
            staging_written[cfg_channel][cfg_addr] <= 1'b1;
        end
    end

endmodule
