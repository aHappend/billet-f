module billet_f4_mel (
    input wire clk, rst_n, clear_all, clear_channel_valid,
    input wire [1:0] clear_channel,
    input wire p_valid,
    output wire p_ready,
    input wire [31:0] p_power,
    input wire [1:0] p_channel,
    input wire [8:0] p_bin,
    input wire p_last,
    output wire e_valid,
    input wire e_ready,
    output wire [47:0] e_energy,
    output wire [1:0] e_channel,
    output wire [5:0] e_band,
    output wire e_last,
    output wire feature_error,
    output wire idle
);
    localparam [2:0] FILL=0, DISCARD=1, START=2, CALC=3, OUTPUT=4;
    reg [2:0] state;
    (* ramstyle = "M20K", nomem2reg *)
    reg [31:0] powers [0:256];
    reg [8:0] expected_bin;
    reg [1:0] channel;
    reg [5:0] band;
    reg error_q;
    reg [8:0] read_index;
    reg read_valid, product_valid, read_last, product_last, round_pending;
    reg [31:0] power_q;
    wire [6:0] band_a, band_b;
    wire [16:0] weight_a, weight_b;
    wire [16:0] weight = (band_a == {1'b0,band}) ? weight_a :
                        (band_b == {1'b0,band}) ? weight_b : 17'd0;
    reg [48:0] product_q;
    reg [63:0] accumulator;
    reg [47:0] energy_q;
    wire read_enable = (state == CALC) && (read_index < 257);
    wire accept = p_valid && p_ready;
    wire malformed = (p_bin != expected_bin) ||
        ((expected_bin != 0) && (p_channel != channel)) ||
        (p_last != (expected_bin == 256));
    wire [48:0] rounded_energy = {1'b0,accumulator[63:16]} +
        ((accumulator[15:0] > 16'd32768) ||
         ((accumulator[15:0] == 16'd32768) && accumulator[16]));

    billet_f4_mel_rom u_weights (.clk(clk),.enable(read_enable),
        .bin(read_index),.band_a(band_a),.band_b(band_b),
        .weight_a(weight_a),.weight_b(weight_b));
    assign p_ready = rst_n && !clear_all && !clear_channel_valid &&
                     ((state == FILL) || (state == DISCARD));
    assign e_valid = rst_n && (state == OUTPUT);
    assign e_energy = e_valid ? energy_q : 48'd0;
    assign e_channel = e_valid ? channel : 2'd0;
    assign e_band = e_valid ? band : 6'd0;
    assign e_last = e_valid && (band == 63);
    assign feature_error = rst_n && error_q;
    assign idle = rst_n && ((state == FILL) || (state == DISCARD));

    always @(posedge clk) begin
        if (accept && (state == FILL) && !malformed)
            powers[p_bin] <= p_power;
        if (read_enable)
            power_q <= powers[read_index];
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= FILL; expected_bin <= 0; channel <= 0; band <= 0;
            error_q <= 0; read_index <= 0; read_valid <= 0;
            product_valid <= 0; read_last <= 0; product_last <= 0;
            round_pending <= 0; product_q <= 0; accumulator <= 0; energy_q <= 0;
        end else if (clear_all) begin
            state <= FILL; expected_bin <= 0; channel <= 0; band <= 0;
            error_q <= 0; read_valid <= 0; product_valid <= 0; round_pending <= 0;
            energy_q <= 0;
        end else if (clear_channel_valid && (state == FILL) &&
                     (expected_bin != 0) && (channel == clear_channel)) begin
            expected_bin <= 0;
        end else begin
            case (state)
                FILL: if (accept) begin
                    if (malformed) begin
                        error_q <= 1;
                        expected_bin <= 0;
                        if (p_last) state <= FILL;
                        else state <= DISCARD;
                    end else begin
                        if (expected_bin == 0) channel <= p_channel;
                        if (p_last) begin
                            expected_bin <= 0;
                            band <= 0;
                            state <= START;
                        end else expected_bin <= expected_bin + 1'b1;
                    end
                end
                DISCARD: if (accept && p_last) begin
                    expected_bin <= 0;
                    state <= FILL;
                end
                START: begin
                    read_index <= 0; accumulator <= 0;
                    read_valid <= 0; product_valid <= 0; round_pending <= 0;
                    state <= CALC;
                end
                CALC: begin
                    read_valid <= read_enable;
                    read_last <= (read_index == 256);
                    product_valid <= read_valid;
                    product_last <= read_last;
                    if (read_enable) read_index <= read_index + 1'b1;
                    if (read_valid) product_q <= power_q * weight;
                    if (product_valid) accumulator <= accumulator + {15'd0,product_q};
                    round_pending <= product_valid && product_last;
                    if (round_pending) begin
                        energy_q <= rounded_energy[47:0];
                        state <= OUTPUT;
                    end
                end
                OUTPUT: if (e_ready) begin
                    energy_q <= 0;
                    if (band == 63) state <= FILL;
                    else begin
                        band <= band + 1'b1;
                        read_index <= 0; accumulator <= 0;
                        read_valid <= 0; product_valid <= 0; round_pending <= 0;
                        state <= CALC;
                    end
                end
                default: state <= FILL;
            endcase
        end
    end
endmodule
