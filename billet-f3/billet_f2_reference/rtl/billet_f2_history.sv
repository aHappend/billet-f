module billet_f2_history #(
    parameter integer TAP_COUNT = 32,
    parameter integer MAC_LANES = 1,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic                         push,
    input  logic [1:0]                   push_channel,
    input  logic signed [15:0]           push_data,
    input  logic [1:0]                   read_channel,
    input  logic [ADDR_W-1:0]            read_base,
    output wire [16*MAC_LANES-1:0]       sample_lanes
);

    logic signed [15:0] history [0:4*TAP_COUNT-1];
    integer i;
    genvar lane;

    generate
        for (lane = 0; lane < MAC_LANES; lane = lane + 1) begin : g_read
            wire [31:0] tap_addr;
            assign tap_addr = read_base + lane;
            assign sample_lanes[16*lane +: 16] =
                (tap_addr < TAP_COUNT) ?
                history[read_channel*TAP_COUNT + tap_addr] : 16'sd0;
        end
    endgenerate

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < 4*TAP_COUNT; i = i + 1)
                history[i] <= 16'sd0;
        end else if (push) begin
            for (i = TAP_COUNT-1; i > 0; i = i - 1)
                history[push_channel*TAP_COUNT + i] <=
                    history[push_channel*TAP_COUNT + i-1];
            history[push_channel*TAP_COUNT] <= push_data;
        end
    end

endmodule
