module bridge;
    parameter integer T = 32;
    localparam integer AW = (T > 1) ? $clog2(T) : 1;
    reg clk = 0;
    reg rst_n;
    reg signed [15:0] s_data = 0, cfg_data = 0;
    reg s_valid = 0, m_ready = 0, cfg_valid = 0, commit_valid = 0;
    reg [AW-1:0] cfg_addr = 0;
    wire s_ready, m_valid, cfg_ready, commit_ready;
    wire signed [15:0] m_data;
    integer fd, trace_fd, count, line_no = 0;
    integer ri, sv, sd, mr, cv, ca, cd, cm;
    reg [4095:0] vectors_path, trace_path;

    billet_f1_top #(.TAP_COUNT(T)) dut (
        .clk(clk), .rst_n(rst_n), .s_data(s_data), .s_valid(s_valid),
        .s_ready(s_ready), .m_data(m_data), .m_valid(m_valid), .m_ready(m_ready),
        .cfg_valid(cfg_valid), .cfg_ready(cfg_ready), .cfg_addr(cfg_addr),
        .cfg_data(cfg_data), .commit_valid(commit_valid), .commit_ready(commit_ready)
    );

    task automatic reset_outputs;
        begin
            if ({s_ready, m_valid, cfg_ready, commit_ready, m_data} !== 20'd0)
                $fatal(1, "RESET_XZ_OR_NONZERO time=%0t", $time);
        end
    endtask

    task automatic observe(input integer phase);
        begin
            if ((^{s_ready, m_valid, cfg_ready, commit_ready, m_data}) === 1'bx)
                $fatal(1, "ACTIVE_XZ line=%0d phase=%0d", line_no, phase);
            $fwrite(trace_fd, "%0d %0d %0d %0d %0d %0d %0d\n",
                line_no, phase, s_ready, m_valid, cfg_ready, commit_ready, m_data);
        end
    endtask

    initial begin
        rst_n = 0;
        #0;
        reset_outputs();
        #5;
        clk = 1;
        reset_outputs();
        #1;
        reset_outputs();
        #4;
        clk = 0;
        reset_outputs();
        if (!$value$plusargs("vectors=%s", vectors_path) ||
            !$value$plusargs("trace=%s", trace_path))
            $fatal(1, "missing vectors/trace arguments");
        fd = $fopen(vectors_path, "r");
        trace_fd = $fopen(trace_path, "w");
        if (!fd || !trace_fd) $fatal(1, "file open failure");
        count = $fscanf(fd, "%d %d %d %d %d %d %d %d\n", ri, sv, sd, mr, cv, ca, cd, cm);
        while (count == 8) begin
            rst_n = (ri == 2) ? 0 : ri;
            s_valid = sv; s_data = sd; m_ready = mr;
            cfg_valid = cv; cfg_addr = ca; cfg_data = cd; commit_valid = cm;
            #1;
            if (ri == 2) begin
                reset_outputs();
                rst_n = 1;
            end
            #3;
            observe(0);
            #1;
            clk = 1;
            observe(1);
            #1;
            observe(2);
            #4;
            clk = 0;
            observe(3);
            line_no = line_no + 1;
            count = $fscanf(fd, "%d %d %d %d %d %d %d %d\n", ri, sv, sd, mr, cv, ca, cd, cm);
        end
        if (count != -1 || !$feof(fd)) $fatal(1, "malformed or truncated vector at %0d", line_no);
        $fclose(fd);
        $fclose(trace_fd);
        $display("SIMULATION_COMPLETE cycles=%0d", line_no);
        $finish;
    end
endmodule
