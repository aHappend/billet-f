module round_bridge;
    parameter integer IN_W=53, SHIFT=16, OUT_W=32;
    reg signed [IN_W-1:0] value;
    wire signed [OUT_W-1:0] result;
    reg [4095:0] vectors, trace;
    integer vf, tf, rc;
    billet_f3_round_sat #(.IN_W(IN_W),.SHIFT(SHIFT),.OUT_W(OUT_W)) dut (.*);
    initial begin
        if (!$value$plusargs("vectors=%s",vectors) ||
            !$value$plusargs("trace=%s",trace)) $fatal(1,"missing paths");
        vf=$fopen(vectors,"r"); tf=$fopen(trace,"w");
        if (!vf || !tf) $fatal(1,"open failed");
        while (!$feof(vf)) begin
            rc=$fscanf(vf,"%h\n",value);
            if (rc != 1) $fatal(1,"bad vector");
            #1;
            $fdisplay(tf,"%h",result);
        end
        $fclose(vf); $fclose(tf); $finish;
    end
endmodule
