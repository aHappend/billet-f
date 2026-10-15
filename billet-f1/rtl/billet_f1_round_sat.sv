module billet_f1_round_sat #(
    parameter integer ACC_W = 37
) (
    input  logic signed [ACC_W-1:0] accumulator,
    output logic signed [15:0]     result
);

    localparam signed [ACC_W:0] MIN_OUTPUT = -32768;
    localparam signed [ACC_W:0] MAX_OUTPUT = 32767;
    localparam signed [ACC_W:0] ONE = 1;
    localparam signed [ACC_W:0] ZERO = 0;
    wire signed [ACC_W:0] floor_quotient;
    wire [14:0] remainder;
    wire round_up;
    wire signed [ACC_W:0] rounded;

    assign floor_quotient = $signed({accumulator[ACC_W-1], accumulator}) >>> 15;
    assign remainder = accumulator[14:0];
    assign round_up = (remainder > 15'd16384) ||
                      ((remainder == 15'd16384) && floor_quotient[0]);
    assign rounded = floor_quotient + (round_up ? ONE : ZERO);

    always @* begin
        if (rounded > MAX_OUTPUT)
            result = 16'sh7fff;
        else if (rounded < MIN_OUTPUT)
            result = 16'sh8000;
        else
            result = rounded[15:0];
    end

endmodule
