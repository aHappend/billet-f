module billet_f2_history #(
    parameter integer TAP_COUNT=32,
    parameter integer MAC_LANES=1,
    localparam integer ADDR_W=(TAP_COUNT>1)?$clog2(TAP_COUNT):1
) (
    input wire clk,rst_n,push,
    input wire [1:0] push_channel,
    input wire signed [15:0] push_data,
    input wire [1:0] read_channel,
    input wire [ADDR_W-1:0] read_base,
    output wire [16*MAC_LANES-1:0] sample_lanes
);
    (* ramstyle = "M20K", nomem2reg *)
    reg signed [15:0] history [0:4*TAP_COUNT-1];
    reg [ADDR_W-1:0] head [0:3];
    reg [ADDR_W:0] fill [0:3];
    wire [ADDR_W-1:0] next_head = (TAP_COUNT==1)?0:head[push_channel]+1'b1;
    genvar lane;
    generate
    for (lane=0;lane<MAC_LANES;lane=lane+1) begin : g_read
        wire [ADDR_W:0] tap_address={1'b0,read_base}+lane;
        wire [ADDR_W-1:0] address=head[read_channel]-tap_address[ADDR_W-1:0];
        reg signed [15:0] sample_q;
        assign sample_lanes[16*lane+:16]=sample_q;
        always @(posedge clk) begin
            sample_q <= ((tap_address<TAP_COUNT) && (tap_address<fill[read_channel])) ?
                history[read_channel*TAP_COUNT+((TAP_COUNT==1)?0:address)] : 16'sd0;
        end
    end
    endgenerate
    always @(posedge clk) begin
        if (rst_n && push) history[push_channel*TAP_COUNT+next_head]<=push_data;
    end
    integer c;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (c=0;c<4;c=c+1) begin head[c]<=0; fill[c]<=0; end
        end else if (push) begin
            head[push_channel]<=next_head;
            if (fill[push_channel]<TAP_COUNT) fill[push_channel]<=fill[push_channel]+1'b1;
        end
    end
endmodule
