module billet_f5_udiv (
    input wire clk, rst_n, clear_all, start,
    input wire [55:0] numerator,
    input wire [35:0] denominator,
    output wire [55:0] quotient,
    output wire busy, done
);
    reg [55:0] quotient_q;
    reg [35:0] remainder_q, denominator_q;
    reg [5:0] iteration;
    reg busy_q, done_q;
    wire [36:0] shifted={remainder_q,quotient_q[55]};
    wire [36:0] trial=shifted-{1'b0,denominator_q};
    wire subtract_ok=!trial[36];
    assign quotient=quotient_q;
    assign busy=rst_n && busy_q;
    assign done=rst_n && done_q;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            quotient_q<=0; remainder_q<=0; denominator_q<=0;
            iteration<=0; busy_q<=0; done_q<=0;
        end else if (clear_all) begin
            quotient_q<=0; remainder_q<=0; denominator_q<=0;
            iteration<=0; busy_q<=0; done_q<=0;
        end else begin
            done_q<=0;
            if (start && !busy_q) begin
                quotient_q<=numerator; remainder_q<=0;
                denominator_q<=denominator;
                iteration<=0; busy_q<=1;
            end else if (busy_q) begin
                remainder_q<=subtract_ok?trial[35:0]:shifted[35:0];
                quotient_q<={quotient_q[54:0],subtract_ok};
                if (iteration==55) begin busy_q<=0; done_q<=1; end
                else iteration<=iteration+1'b1;
            end
        end
    end
endmodule
