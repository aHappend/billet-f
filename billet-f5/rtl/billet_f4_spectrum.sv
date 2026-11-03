module billet_f4_spectrum (
    input wire clk, rst_n, clear_all, clear_channel_valid,
    input wire [1:0] clear_channel,
    input wire s_valid,
    output wire s_ready,
    input wire signed [15:0] s_data,
    input wire [1:0] s_channel,
    output wire p_valid,
    input wire p_ready,
    output wire [31:0] p_power,
    output wire [1:0] p_channel,
    output wire [8:0] p_bin,
    output wire p_last,
    output wire idle
);
    (* ramstyle = "M20K", nomem2reg *)
    reg signed [15:0] frames [0:2047];
    reg [8:0] count [0:3];
    reg [3:0] full;
    reg [1:0] window_stage, window_channel;
    reg [8:0] window_index;
    reg signed [15:0] window_sample;
    wire [16:0] window_coefficient;
    reg signed [33:0] window_product;
    wire signed [15:0] window_result;
    wire sample_fire = s_valid && s_ready;
    localparam [1:0] IDLE=0, CALC=1, OUTPUT=2;
    reg [1:0] state, channel, rr;
    reg [8:0] bin, phase;
    reg [9:0] read_index;
    reg read_valid, product_valid, read_last, product_last;
    reg round_pending, average_valid, squares_valid;
    reg signed [15:0] sample_q;
    wire signed [17:0] cosine_q, sine_q;
    reg signed [33:0] real_product, imag_product;
    reg signed [42:0] real_acc, imag_acc;
    wire signed [17:0] real_rounded, imag_rounded;
    reg signed [17:0] real_average, imag_average;
    wire [35:0] real_square = real_average * real_average;
    wire [35:0] imag_square = imag_average * imag_average;
    reg [36:0] square_sum;
    reg [31:0] power_q;
    wire read_enable = (state == CALC) && (read_index < 512);
    reg pick_found;
    reg [1:0] pick, candidate;
    integer offset;
    always @* begin
        pick_found=0; pick=0; candidate=0;
        for (offset=0;offset<4;offset=offset+1) begin
            candidate=rr+offset;
            if (!pick_found && full[candidate]) begin
                pick_found=1;
                pick=candidate;
            end
        end
    end

    billet_f4_hann_rom u_window (.clk(clk),.enable(sample_fire),
        .address(count[s_channel]),.coefficient(window_coefficient));
    billet_f4_twiddle_rom u_twiddles (.clk(clk),.enable(read_enable),
        .address(phase),.cosine(cosine_q),.sine(sine_q));
    billet_f3_round_sat #(.IN_W(34),.SHIFT(16),.OUT_W(16)) u_window_round (
        .value(window_product),.result(window_result));
    billet_f3_round_sat #(.IN_W(43),.SHIFT(25),.OUT_W(18)) u_real_round (
        .value(real_acc),.result(real_rounded));
    billet_f3_round_sat #(.IN_W(43),.SHIFT(25),.OUT_W(18)) u_imag_round (
        .value(imag_acc),.result(imag_rounded));

    assign s_ready = rst_n && !clear_all && !clear_channel_valid &&
                     (window_stage == 0) && !full[s_channel];
    assign p_valid = rst_n && (state == OUTPUT);
    assign p_power = p_valid ? power_q : 32'd0;
    assign p_channel = p_valid ? channel : 2'd0;
    assign p_bin = p_valid ? bin : 9'd0;
    assign p_last = p_valid && (bin == 256);
    assign idle = rst_n && (state == IDLE) && (window_stage == 0) && !(|full);

    always @(posedge clk) begin
        if (rst_n && !clear_all && (window_stage == 2))
            frames[window_channel*512+window_index] <= window_result;
        if (read_enable)
            sample_q <= frames[channel*512+read_index];
    end
    integer c;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            full <= 0;
            window_stage <= 0; window_channel <= 0; window_index <= 0;
            window_sample <= 0; window_product <= 0;
            state <= IDLE; channel <= 0; rr <= 0; bin <= 0; phase <= 0;
            read_index <= 0; read_valid <= 0; product_valid <= 0;
            read_last <= 0; product_last <= 0; round_pending <= 0;
            average_valid <= 0; squares_valid <= 0;
            real_product <= 0; imag_product <= 0;
            real_acc <= 0; imag_acc <= 0;
            real_average <= 0; imag_average <= 0; square_sum <= 0; power_q <= 0;
            for (c=0;c<4;c=c+1) count[c]<=0;
        end else if (clear_all) begin
            full <= 0; window_stage <= 0; state <= IDLE; rr <= 0;
            read_valid <= 0; product_valid <= 0; round_pending <= 0;
            average_valid <= 0; squares_valid <= 0; power_q <= 0;
            for (c=0;c<4;c=c+1) count[c]<=0;
        end else begin
            if (clear_channel_valid) count[clear_channel]<=0;
            if (sample_fire) begin
                window_stage <= 1;
                window_channel <= s_channel;
                window_index <= count[s_channel];
                window_sample <= s_data;
            end else if (window_stage == 1) begin
                window_product <= window_sample * $signed({1'b0,window_coefficient});
                window_stage <= 2;
            end else if (window_stage == 2) begin
                window_stage <= 0;
                if (window_index == 511) begin
                    full[window_channel] <= 1;
                    count[window_channel] <= 0;
                end else count[window_channel] <= window_index + 1'b1;
            end

            case (state)
                IDLE: if (pick_found) begin
                    channel <= pick; rr <= pick + 1'b1;
                    bin <= 0; phase <= 0; read_index <= 0;
                    real_acc <= 0; imag_acc <= 0;
                    read_valid <= 0; product_valid <= 0; round_pending <= 0;
                    average_valid <= 0; squares_valid <= 0;
                    state <= CALC;
                end
                CALC: begin
                    read_valid <= read_enable;
                    read_last <= (read_index == 511);
                    product_valid <= read_valid;
                    product_last <= read_last;
                    if (read_enable) begin
                        read_index <= read_index + 1'b1;
                        phase <= phase + bin;
                    end
                    if (read_valid) begin
                        real_product <= sample_q * cosine_q;
                        imag_product <= sample_q * sine_q;
                    end
                    if (product_valid) begin
                        real_acc <= real_acc + {{9{real_product[33]}},real_product};
                        imag_acc <= imag_acc + {{9{imag_product[33]}},imag_product};
                    end
                    round_pending <= product_valid && product_last;
                    average_valid <= round_pending;
                    squares_valid <= average_valid;
                    if (round_pending) begin
                        real_average <= real_rounded;
                        imag_average <= imag_rounded;
                    end
                    if (average_valid)
                        square_sum <= {1'b0,real_square} + {1'b0,imag_square};
                    if (squares_valid) begin
                        power_q <= (|square_sum[36:32]) ? 32'hffffffff : square_sum[31:0];
                        state <= OUTPUT;
                    end
                end
                OUTPUT: if (p_ready) begin
                    power_q <= 0;
                    if (bin == 256) begin
                        full[channel] <= 0;
                        state <= IDLE;
                    end else begin
                        bin <= bin + 1'b1; phase <= 0; read_index <= 0;
                        real_acc <= 0; imag_acc <= 0;
                        read_valid <= 0; product_valid <= 0; round_pending <= 0;
                        average_valid <= 0; squares_valid <= 0;
                        state <= CALC;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
