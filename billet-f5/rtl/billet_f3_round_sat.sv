module billet_f3_round_sat #(
    parameter integer IN_W = 53,
    parameter integer SHIFT = 16,
    parameter integer OUT_W = 32
) (
    input  wire signed [IN_W-1:0] value,
    output wire signed [OUT_W-1:0] result
);

    localparam signed [IN_W:0] ONE = 1;
    localparam signed [IN_W:0] ZERO = 0;
    localparam signed [IN_W:0] MAX_OUTPUT = (ONE <<< (OUT_W-1)) - ONE;
    localparam signed [IN_W:0] MIN_OUTPUT = -(ONE <<< (OUT_W-1));
    localparam [SHIFT-1:0] HALF = {1'b1, {(SHIFT-1){1'b0}}};
    wire signed [IN_W:0] floor_quotient;
    wire [SHIFT-1:0] remainder;
    wire round_up;
    wire signed [IN_W:0] rounded;

    assign floor_quotient = $signed({value[IN_W-1], value}) >>> SHIFT;
    assign remainder = value[SHIFT-1:0];
    assign round_up = (remainder > HALF) ||
                      ((remainder == HALF) && floor_quotient[0]);
    assign rounded = floor_quotient + (round_up ? ONE : ZERO);
    assign result = (rounded > MAX_OUTPUT) ? {1'b0, {(OUT_W-1){1'b1}}} :
                    (rounded < MIN_OUTPUT) ? {1'b1, {(OUT_W-1){1'b0}}} :
                    rounded[OUT_W-1:0];

endmodule
