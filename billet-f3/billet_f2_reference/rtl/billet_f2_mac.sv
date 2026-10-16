module billet_f2_mac #(
    parameter integer TAP_COUNT = 32,
    parameter integer MAC_LANES = 1,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1,
    localparam integer ACC_W = 32 + ((TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 0)
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic                         start,
    input  logic [1:0]                   sample_channel,
    input  logic                         sample_bank,
    input  logic [16*MAC_LANES-1:0]      coeff_lanes,
    input  logic [16*MAC_LANES-1:0]      sample_lanes,
    output logic [1:0]                   read_channel,
    output logic                         read_bank,
    output wire [ADDR_W-1:0]             read_base,
    output logic                         busy,
    output wire                          final_valid,
    output wire signed [ACC_W-1:0]      final_sum,
    output wire [1:0]                   final_channel
);

    logic [ADDR_W-1:0] tap_index;
    logic signed [ACC_W-1:0] accumulator;
    wire signed [ACC_W-1:0] reduction [0:2*MAC_LANES-2];
    genvar lane;
    genvar node;

    assign read_base = tap_index;
    assign final_channel = read_channel;
    assign final_valid = busy && (tap_index + MAC_LANES >= TAP_COUNT);
    assign final_sum = accumulator + reduction[0];

    generate
        for (lane = 0; lane < MAC_LANES; lane = lane + 1) begin : g_lane
            wire signed [15:0] sample_operand;
            wire signed [15:0] coeff_operand;
            wire signed [31:0] product;
            wire signed [ACC_W-1:0] extended_product;

            assign sample_operand = sample_lanes[16*lane +: 16];
            assign coeff_operand = coeff_lanes[16*lane +: 16];
            assign product = sample_operand * coeff_operand;
            assign extended_product = {{(ACC_W-32){product[31]}}, product};
            assign reduction[MAC_LANES-1+lane] =
                (tap_index + lane < TAP_COUNT) ? extended_product :
                                               {ACC_W{1'b0}};
        end
        for (node = 0; node < MAC_LANES-1; node = node + 1) begin : g_reduce
            assign reduction[node] = reduction[2*node+1] + reduction[2*node+2];
        end
    endgenerate

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            tap_index <= {ADDR_W{1'b0}};
            accumulator <= {ACC_W{1'b0}};
            read_channel <= 2'd0;
            read_bank <= 1'b0;
        end else if (start) begin
            busy <= 1'b1;
            tap_index <= {ADDR_W{1'b0}};
            accumulator <= {ACC_W{1'b0}};
            read_channel <= sample_channel;
            read_bank <= sample_bank;
        end else if (busy) begin
            if (final_valid) begin
                busy <= 1'b0;
                tap_index <= {ADDR_W{1'b0}};
                accumulator <= {ACC_W{1'b0}};
            end else begin
                tap_index <= tap_index + MAC_LANES;
                accumulator <= final_sum;
            end
        end
    end

endmodule
