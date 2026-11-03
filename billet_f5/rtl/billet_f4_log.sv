module billet_f4_log (
    input wire clk, rst_n,
    input wire s_valid,
    output wire s_ready,
    input wire [47:0] s_energy,
    input wire [1:0] s_channel,
    input wire [5:0] s_band,
    input wire s_last,
    output wire m_valid,
    input wire m_ready,
    output wire [15:0] m_data,
    output wire [1:0] m_channel,
    output wire [5:0] m_band,
    output wire m_last,
    output wire idle
);
    reg [7:0] valid_q, zero_q, last_q;
    reg [1:0] channels [0:7];
    reg [5:0] bands [0:7];
    reg [47:0] energy_q;
    reg [5:0] exponent_q;
    reg [15:0] fraction_q;
    reg [16:0] endpoint_q;
    reg [28:0] interpolation_q;
    reg [5:0] exponent_2;
    reg signed [31:0] log_q;
    reg signed [50:0] db_product_q;
    reg signed [31:0] clipped_q;
    reg [46:0] normalization_q;
    reg [15:0] token_q;
    wire enable = !valid_q[7] || m_ready;
    reg [5:0] exponent;
    reg [63:0] normalized;
    wire [5:0] occupied;
    reg [2:0] group_index, local_index;
    reg [7:0] group_bits;
    genvar g;
    for (g=0;g<6;g=g+1) begin : g_exponent
        assign occupied[g]=|energy_q[8*g+:8];
    end
    always @* begin
        casez (occupied)
            6'b1?????: group_index=5;
            6'b01????: group_index=4;
            6'b001???: group_index=3;
            6'b0001??: group_index=2;
            6'b00001?: group_index=1;
            default: group_index=0;
        endcase
        group_bits=energy_q[8*group_index+:8];
        casez (group_bits)
            8'b1???????: local_index=7;
            8'b01??????: local_index=6;
            8'b001?????: local_index=5;
            8'b0001????: local_index=4;
            8'b00001???: local_index=3;
            8'b000001??: local_index=2;
            8'b0000001?: local_index=1;
            default: local_index=0;
        endcase
        exponent={group_index,local_index};
        normalized = {16'd0,energy_q} << (47-exponent);
    end
    wire [16:0] endpoint, next_endpoint;
    billet_f4_log_rom u_lut (.address(fraction_q[15:12]),
        .base(endpoint),.following(next_endpoint));
    wire [16:0] difference = next_endpoint - endpoint;
    wire [16:0] interpolation_rounded = (interpolation_q >> 12) +
        ((interpolation_q[11:0] > 12'd2048) ||
         ((interpolation_q[11:0] == 12'd2048) && interpolation_q[12]));
    wire signed [31:0] interpolated_log = $signed({10'd0,exponent_2,16'd0}) +
        $signed({15'd0,endpoint_q}) + $signed({15'd0,interpolation_rounded});
    wire signed [31:0] relative_log = log_q - 32'sd1966080;
    wire signed [50:0] db_floor = db_product_q >>> 16;
    wire signed [50:0] db_rounded = db_floor +
        (((db_product_q[15:0] > 16'd32768) ||
          ((db_product_q[15:0] == 16'd32768) && db_floor[0])) ? 51'sd1 : 51'sd0);
    wire [31:0] positive_db = clipped_q + 32'sd6291456;
    wire [26:0] token_rounded = (normalization_q >> 21) +
        ((normalization_q[20:0] > 21'd1048576) ||
         ((normalization_q[20:0] == 21'd1048576) && normalization_q[21]));
    assign s_ready = rst_n && enable;
    assign m_valid = rst_n && valid_q[7];
    assign m_data = m_valid ? token_q : 16'd0;
    assign m_channel = m_valid ? channels[7] : 2'd0;
    assign m_band = m_valid ? bands[7] : 6'd0;
    assign m_last = m_valid && last_q[7];
    assign idle = rst_n && !(|valid_q);
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_q <= 0; zero_q <= 0; last_q <= 0;
            energy_q <= 0; exponent_q <= 0; fraction_q <= 0;
            endpoint_q <= 0; interpolation_q <= 0; exponent_2 <= 0;
            log_q <= 0; db_product_q <= 0; clipped_q <= 0;
            normalization_q <= 0; token_q <= 0;
            for (i=0;i<8;i=i+1) begin channels[i]<=0; bands[i]<=0; end
        end else if (enable) begin
            valid_q <= {valid_q[6:0],s_valid};
            zero_q <= {zero_q[6:0],s_energy == 0};
            last_q <= {last_q[6:0],s_last};
            channels[0] <= s_channel;
            bands[0] <= s_band;
            for (i=1;i<8;i=i+1) begin
                channels[i] <= channels[i-1];
                bands[i] <= bands[i-1];
            end
            energy_q <= s_energy;
            exponent_q <= exponent;
            fraction_q <= normalized[46:31];
            endpoint_q <= endpoint;
            interpolation_q <= difference * fraction_q[11:0];
            exponent_2 <= exponent_q;
            log_q <= interpolated_log;
            db_product_q <= relative_log * 19'sd197283;
            if (db_rounded < -51'sd6291456)
                clipped_q <= -32'sd6291456;
            else if (db_rounded > 0)
                clipped_q <= 0;
            else
                clipped_q <= db_rounded[31:0];
            normalization_q <= positive_db * 16'd21845;
            if (zero_q[6]) token_q <= 0;
            else if (token_rounded > 65535) token_q <= 16'hffff;
            else token_q <= token_rounded[15:0];
        end
    end
endmodule
