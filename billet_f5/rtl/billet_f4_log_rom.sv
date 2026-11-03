// Frozen mathematical ROM constants, generated with 90-digit Decimal arithmetic
// and round-to-nearest, ties-to-even. No external initialization files.
module billet_f4_log_rom (input wire [3:0] address,
    output reg [16:0] base, following);
    always @* begin
        case (address)
            4'd0: begin base=17'd0; following=17'd5732; end
            4'd1: begin base=17'd5732; following=17'd11136; end
            4'd2: begin base=17'd11136; following=17'd16248; end
            4'd3: begin base=17'd16248; following=17'd21098; end
            4'd4: begin base=17'd21098; following=17'd25711; end
            4'd5: begin base=17'd25711; following=17'd30109; end
            4'd6: begin base=17'd30109; following=17'd34312; end
            4'd7: begin base=17'd34312; following=17'd38336; end
            4'd8: begin base=17'd38336; following=17'd42196; end
            4'd9: begin base=17'd42196; following=17'd45904; end
            4'd10: begin base=17'd45904; following=17'd49472; end
            4'd11: begin base=17'd49472; following=17'd52911; end
            4'd12: begin base=17'd52911; following=17'd56229; end
            4'd13: begin base=17'd56229; following=17'd59434; end
            4'd14: begin base=17'd59434; following=17'd62534; end
            4'd15: begin base=17'd62534; following=17'd65536; end
            default: begin base=0; following=0; end
        endcase
    end
endmodule
