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

    logic [ADDR_W:0] tap_index;
    logic signed [ACC_W-1:0] accumulator;
    wire signed [ACC_W-1:0] reduction [0:2*MAC_LANES-2];
    logic [16*MAC_LANES-1:0] samples_q, coeffs_q;
    logic signed [31:0] products_q [0:MAC_LANES-1];
    logic signed [ACC_W-1:0] reduction_q;
    logic read_valid, product_valid, reduction_valid;
    logic read_last, product_last, reduction_last;
    logic done_q;
    genvar lane;
    genvar node;

    assign read_base = tap_index[ADDR_W-1:0];
    assign final_channel = read_channel;
    assign final_valid = done_q;
    assign final_sum = accumulator;

    generate
        for (lane = 0; lane < MAC_LANES; lane = lane + 1) begin : g_lane
            wire signed [15:0] sample_operand;
            wire signed [15:0] coeff_operand;
            wire signed [31:0] product;
            wire signed [ACC_W-1:0] extended_product;

            assign sample_operand = samples_q[16*lane +: 16];
            assign coeff_operand = coeffs_q[16*lane +: 16];
            assign product = sample_operand * coeff_operand;
            assign extended_product = {{(ACC_W-32){products_q[lane][31]}}, products_q[lane]};
            assign reduction[MAC_LANES-1+lane] = extended_product;
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    products_q[lane] <= 32'sd0;
                else
                    products_q[lane] <= product;
            end
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
            samples_q <= 0;
            coeffs_q <= 0;
            read_valid <= 0;
            product_valid <= 0;
            reduction_valid <= 0;
            read_last <= 0;
            product_last <= 0;
            reduction_last <= 0;
            reduction_q <= 0;
            done_q <= 0;
        end else if (start) begin
            busy <= 1'b1;
            tap_index <= {ADDR_W{1'b0}};
            accumulator <= {ACC_W{1'b0}};
            read_channel <= sample_channel;
            read_bank <= sample_bank;
            read_valid <= 0;
            product_valid <= 0;
            reduction_valid <= 0;
            done_q <= 0;
        end else begin
            read_valid <= busy && (tap_index < TAP_COUNT);
            read_last <= (tap_index + MAC_LANES >= TAP_COUNT);
            product_valid <= read_valid;
            product_last <= read_last;
            reduction_valid <= product_valid;
            reduction_last <= product_last;
            reduction_q <= reduction[0];
            done_q <= reduction_valid && reduction_last;
            if (busy && (tap_index < TAP_COUNT)) begin
                samples_q <= sample_lanes;
                coeffs_q <= coeff_lanes;
                tap_index <= tap_index + MAC_LANES;
            end
            if (reduction_valid)
                accumulator <= accumulator + reduction_q;
            if (done_q)
                busy <= 0;
        end
    end

endmodule
