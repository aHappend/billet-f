module billet_f1_mac #(
    parameter integer TAP_COUNT = 32,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1,
    localparam integer ACC_W = 32 + ((TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 0)
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     start,
    input  logic signed [15:0]       sample_data,
    output logic [ADDR_W-1:0]        coeff_addr,
    input  logic signed [15:0]       coeff_data,
    output logic                     busy,
    output logic                     final_valid,
    output logic signed [ACC_W-1:0]  final_sum
);

    logic signed [15:0] history [0:TAP_COUNT-1];
    logic [ADDR_W-1:0] tap_index;
    logic signed [ACC_W-1:0] accumulator;
    wire signed [31:0] product;
    wire signed [ACC_W-1:0] extended_product;
    integer i;

    assign coeff_addr = tap_index;
    assign product = $signed(history[tap_index]) * $signed(coeff_data);
    assign extended_product = {{(ACC_W-32){product[31]}}, product};
    assign final_sum = accumulator + extended_product;
    assign final_valid = busy && (tap_index == TAP_COUNT-1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            tap_index <= {ADDR_W{1'b0}};
            accumulator <= {ACC_W{1'b0}};
            for (i = 0; i < TAP_COUNT; i = i + 1)
                history[i] <= 16'sd0;
        end else if (start) begin
            for (i = TAP_COUNT-1; i > 0; i = i - 1)
                history[i] <= history[i-1];
            history[0] <= sample_data;
            busy <= 1'b1;
            tap_index <= {ADDR_W{1'b0}};
            accumulator <= {ACC_W{1'b0}};
        end else if (busy) begin
            if (final_valid) begin
                busy <= 1'b0;
                tap_index <= {ADDR_W{1'b0}};
                accumulator <= {ACC_W{1'b0}};
            end else begin
                accumulator <= final_sum;
                tap_index <= tap_index + 1'b1;
            end
        end
    end

endmodule
