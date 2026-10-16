module bridge;
    parameter integer TAP_COUNT=32, MAC_LANES=1, SOS_COUNT=2;
    localparam integer ADDR_W=(TAP_COUNT>1)?$clog2(TAP_COUNT):1;
    reg clk=0;
    reg rst_n=0;
    reg signed [15:0] s_data=0, cfg_data=0;
    reg [1:0] s_channel=0, cfg_channel=0, commit_channel=0;
    reg s_valid=0, m_ready=0, cfg_valid=0, commit_valid=0;
    reg sos_cfg_valid=0, sos_commit_valid=0, chain_valid=0;
    reg [1:0] sos_cfg_channel=0, sos_cfg_section=0, sos_commit_channel=0;
    reg [2:0] sos_cfg_addr=0, chain_mode=0, chain_sections=0;
    reg signed [17:0] sos_cfg_data=0;
    reg [6:0] cfg_addr_vector=0;
    wire [ADDR_W-1:0] cfg_addr=cfg_addr_vector;
    wire s_ready, m_valid, cfg_ready, commit_ready;
    wire sos_cfg_ready, sos_commit_ready, chain_ready;
    wire signed [15:0] m_data;
    wire [1:0] m_channel;
    wire [24:0] observed={s_ready,cfg_ready,commit_ready,sos_cfg_ready,
        sos_commit_ready,chain_ready,m_valid,m_channel,m_data};
    wire p_s_ready, p_m_valid, p_cfg_ready, p_commit_ready;
    wire signed [15:0] p_m_data;
    wire [1:0] p_m_channel;
    wire [21:0] parent_observed={p_s_ready,p_cfg_ready,p_commit_ready,
        p_m_valid,p_m_channel,p_m_data};
    reg [255:0] vector;
    reg [24:0] before_edge;
    reg [21:0] parent_before;
    integer vf, tf, rc, cycles=0, compat=0;
    reg [4095:0] vector_path, trace_path;

    billet_f3_top #(.TAP_COUNT(TAP_COUNT),.MAC_LANES(MAC_LANES),
        .SOS_COUNT(SOS_COUNT)) dut (.*);
    parent_billet_f2_top #(.TAP_COUNT(TAP_COUNT),.MAC_LANES(MAC_LANES)) parent (
        .clk(clk),.rst_n(rst_n),.s_data(s_data),.s_channel(s_channel),
        .s_valid(s_valid),.s_ready(p_s_ready),.m_data(p_m_data),
        .m_channel(p_m_channel),.m_valid(p_m_valid),.m_ready(m_ready),
        .cfg_valid(cfg_valid),.cfg_ready(p_cfg_ready),.cfg_channel(cfg_channel),
        .cfg_addr(cfg_addr),.cfg_data(cfg_data),.commit_valid(commit_valid),
        .commit_ready(p_commit_ready),.commit_channel(commit_channel)
    );
    task automatic reset_observation;
        begin
            if (observed !== 25'd0)
                $fatal(1,"RESET_XZ_OR_LEAK t=%0t observed=%h",$time,observed);
        end
    endtask
    task automatic compare_parent;
        begin
            if ({s_ready,cfg_ready,commit_ready,m_valid,m_channel,m_data}
                !== parent_observed)
                $fatal(1,"PARENT_MISMATCH cycle=%0d",cycles);
        end
    endtask
    initial begin
        #0;
        reset_observation();
        #5;
        clk=1;
        reset_observation();
        #1;
        reset_observation();
        #4;
        clk=0;
        reset_observation();
        if (!$value$plusargs("vectors=%s",vector_path) ||
            !$value$plusargs("trace=%s",trace_path))
            $fatal(1,"missing vector/trace argument");
        rc=$value$plusargs("compat=%d",compat);
        vf=$fopen(vector_path,"r"); tf=$fopen(trace_path,"w");
        if (!vf || !tf) $fatal(1,"cannot open vectors/trace");
        while (!$feof(vf)) begin
            rc=$fscanf(vf,"%h\n",vector);
            if (rc != 1) $fatal(1,"bad vector");
            {rst_n,s_data,s_channel,s_valid,m_ready,cfg_valid,cfg_channel,
             cfg_addr_vector,cfg_data,commit_valid,commit_channel,
             sos_cfg_valid,sos_cfg_channel,sos_cfg_section,sos_cfg_addr,
             sos_cfg_data,sos_commit_valid,sos_commit_channel,
             chain_valid,chain_mode,chain_sections}=vector;
            #2;
            if (!rst_n) reset_observation();
            before_edge=observed;
            parent_before=parent_observed;
            if (compat) compare_parent();
            #2; clk=1;
            if (!rst_n) reset_observation();
            #1;
            if (!rst_n) reset_observation();
            if (compat) compare_parent();
            $fdisplay(tf,"%h %h",before_edge,observed);
            cycles=cycles+1;
            #4; clk=0;
            if (!rst_n) reset_observation();
            #1;
        end
        $fclose(vf); $fclose(tf);
        $display("TRACE_CYCLES %0d",cycles);
        $finish;
    end
endmodule
