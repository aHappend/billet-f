module billet_f1_coeff_bank #(
    parameter integer TAP_COUNT = 32,
    localparam integer ADDR_W = (TAP_COUNT > 1) ? $clog2(TAP_COUNT) : 1
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    cfg_write,
    input  logic [ADDR_W-1:0]       cfg_addr,
    input  logic signed [15:0]      cfg_data,
    input  logic                    commit,
    output logic                    staging_complete,
    input  logic [ADDR_W-1:0]       read_addr,
    output logic signed [15:0]      read_data
);

    logic signed [15:0] bank_zero [0:TAP_COUNT-1];
    logic signed [15:0] bank_one [0:TAP_COUNT-1];
    logic active_bank;
    logic [TAP_COUNT-1:0] staging_written;
    integer i;

    assign staging_complete = &staging_written;
    assign read_data = active_bank ? bank_one[read_addr] : bank_zero[read_addr];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            active_bank <= 1'b0;
            staging_written <= {TAP_COUNT{1'b0}};
            for (i = 0; i < TAP_COUNT; i = i + 1) begin
                bank_zero[i] <= 16'sd0;
                bank_one[i] <= 16'sd0;
            end
        end else if (commit) begin
            active_bank <= !active_bank;
            staging_written <= {TAP_COUNT{1'b0}};
        end else if (cfg_write) begin
            if (active_bank)
                bank_zero[cfg_addr] <= cfg_data;
            else
                bank_one[cfg_addr] <= cfg_data;
            staging_written[cfg_addr] <= 1'b1;
        end
    end

endmodule
