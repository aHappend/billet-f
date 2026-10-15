"""Generate a file-driven public-pin transactor, not a translated RTL model."""


def testbench(taps, lanes, compatibility):
    parent_decl = """
wire f_sr, f_cr, f_kr, f_mv;
wire signed [15:0] f_md;
billet_f1_top #(.TAP_COUNT(T)) parent (
 .clk(clk), .rst_n(rst_n), .s_data(s_data), .s_valid(s_valid), .s_ready(f_sr),
 .m_data(f_md), .m_valid(f_mv), .m_ready(m_ready),
 .cfg_valid(cfg_valid), .cfg_ready(f_cr), .cfg_addr(cfg_addr), .cfg_data(cfg_data),
 .commit_valid(commit_valid), .commit_ready(f_kr));
""" if compatibility else ""
    parent_check = """
if ((^{f_sr,f_cr,f_kr,f_mv,f_md}) === 1'bx)
  $fatal(1, "PARENT X/Z");
if ({s_ready,cfg_ready,commit_ready,m_valid,m_data} !==
    {f_sr,f_cr,f_kr,f_mv,f_md})
  $fatal(1, "F1 LOCKSTEP cycle=%0d", count);
if (m_channel !== 2'd0) $fatal(1, "F1 channel mismatch");
""" if compatibility else ""
    return f"""
`timescale 1ns/1ps
module tb;
localparam integer T={taps}, L={lanes};
localparam integer AW=(T>1)?$clog2(T):1;
reg clk=0;
reg rst_n;
reg signed [15:0] s_data=0, cfg_data=0;
reg [1:0] s_channel=0, cfg_channel=0, commit_channel=0;
reg s_valid=0, m_ready=0, cfg_valid=0, commit_valid=0;
reg [AW-1:0] cfg_addr=0;
wire s_ready,cfg_ready,commit_ready,m_valid;
wire signed [15:0] m_data;
wire [1:0] m_channel;
billet_f2_top #(.TAP_COUNT(T),.MAC_LANES(L)) device (
 .clk(clk),.rst_n(rst_n),.s_data(s_data),.s_channel(s_channel),
 .s_valid(s_valid),.s_ready(s_ready),.m_data(m_data),.m_channel(m_channel),
 .m_valid(m_valid),.m_ready(m_ready),.cfg_valid(cfg_valid),.cfg_ready(cfg_ready),
 .cfg_channel(cfg_channel),.cfg_addr(cfg_addr),.cfg_data(cfg_data),
 .commit_valid(commit_valid),.commit_ready(commit_ready),.commit_channel(commit_channel));
{parent_decl}
integer fd,trace,rc,count=0;
integer rn,sd,sc,sv,mr,cv,cc,ca,cd,kv,kc;
reg [4095:0] vector_path,trace_path;
task automatic known;
begin
 if ((^{{s_ready,cfg_ready,commit_ready,m_valid,m_data,m_channel}}) === 1'bx)
   $fatal(1,"PUBLIC X/Z cycle=%0d",count);
 if (!rst_n && ({{s_ready,cfg_ready,commit_ready,m_valid,m_data,m_channel}} !== 22'd0))
   $fatal(1,"RESET pins cycle=%0d",count);
 if (!m_valid && ({{m_data,m_channel}} !== 18'd0))
   $fatal(1,"INVALID output payload cycle=%0d",count);
 {parent_check}
end
endtask
initial begin
 rst_n=0;
 #1; known();
 #4; clk=1; known(); // Active-region observation at the first t=5 edge.
 #1; known();
 #4; clk=0; known();
 // Exercise a real 1->0 asynchronous assertion before vector execution.
 #1; rst_n=1;
 #1; rst_n=0;
 #1; known();
 if (!$value$plusargs("vectors=%s",vector_path)) $fatal(1,"missing vectors");
 if (!$value$plusargs("trace=%s",trace_path)) $fatal(1,"missing trace");
 fd=$fopen(vector_path,"r"); trace=$fopen(trace_path,"w");
 if (!fd || !trace) $fatal(1,"file open failed");
 while (!$feof(fd)) begin
   rc=$fscanf(fd,"%d %d %d %d %d %d %d %d %d %d %d\\n",
              rn,sd,sc,sv,mr,cv,cc,ca,cd,kv,kc);
   if (rc != 11) $fatal(1,"partial vector count=%0d rc=%0d",count,rc);
   rst_n=rn; s_data=sd; s_channel=sc; s_valid=sv; m_ready=mr;
   cfg_valid=cv; cfg_channel=cc; cfg_addr=ca; cfg_data=cd;
   commit_valid=kv; commit_channel=kc;
   #1; known(); // Asynchronous reset observation before any active edge.
   #3; known();
   $fwrite(trace,"R %0d %0d %0d %0d %0d %0d %0d ",
     count,s_ready,cfg_ready,commit_ready,m_valid,$signed(m_data),m_channel);
   clk=1;
   if (!rst_n) known();
   #1; known();
   $fwrite(trace,"%0d %0d %0d %0d %0d %0d\\n",
     s_ready,cfg_ready,commit_ready,m_valid,$signed(m_data),m_channel);
   #4; clk=0;
   if (!rst_n) known();
   #1; count=count+1;
 end
 $fclose(fd); $fclose(trace);
 $display("DONE %0d",count);
 $finish(0);
end
endmodule
"""
