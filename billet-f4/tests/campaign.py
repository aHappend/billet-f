"""Executable admitted matrix, public-pin checks, and disposable defects.

Every case records actual comparisons, not attempted stimulus labels. Large
matrices use compiled Verilator; Icarus is confined to startup/compact leaves.
"""

import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import shutil

from oracle import Legacy, Resampler, lowpass, mel, pwl_error, rne, sat, spectrum, tables, token
from simulator import (ROOT, Simulator, compare, input_records, interface,
                       output_records, source_files, unique)

SEEDS = (11, 29, 47)
TAPS = (1, 8, 16, 32, 64)


def pcm(value, channel):
    return (value & 65535, channel, 0, 0)


def tagged(values, channel):
    return [(value, channel, band, int(band == len(values) - 1))
            for band, value in enumerate(values)]


def ledger(evidence, case, **values):
    evidence.receipt["results"].append({"case": case, **values})
    evidence.write()


def finish_program(program, stream, count, tail=512):
    if count:
        program.drain(stream, count)
    program.random(0)
    program.wait(tail)


def configure_legacy(program, fir, sos, mode=1, sections=None):
    for channel in range(4):
        for tap, value in enumerate(fir[channel]):
            program.send("cfg", channel, tap, value)
        program.send("commit", channel)
        for section, coefficients in enumerate(sos[channel]):
            for address, value in enumerate(coefficients):
                program.send("sos_cfg", channel, section, address, value)
        program.send("sos_commit", channel)
    program.send("chain", mode, 0 if mode < 2 else (sections or len(sos[0])))


def configure_src(program, banks, l, m, top=True):
    cfg, commit = ("src_cfg", "src_commit") if top else ("cfg", "commit")
    for channel in range(4):
        for phase in range(l):
            for tap, value in enumerate(banks[channel][phase]):
                program.send(cfg, channel, phase, tap, value)
        program.send(commit, channel, l, m)


def src_banks(taps, l, seed):
    rng = random.Random(seed)
    result = []
    for channel in range(4):
        phases = []
        for phase in range(l):
            coefficients = [0] * taps
            coefficients[0] = 32768 + 1024 * phase + 257 * channel
            for k in range(1, min(taps, 8)):
                coefficients[k] = rng.randint(-4096, 4096)
            phases.append(coefficients)
        result.append(phases)
    return result


def legacy_banks(taps=32, sections=2):
    fir = []
    sos = []
    for channel in range(4):
        row = [0] * taps
        row[0] = 16384 + 513 * channel
        for k in range(1, min(taps, 6)):
            row[k] = (-1 if k & 1 else 1) * (2048 + 31 * k + channel)
        fir.append(row)
        sos.append([[49152 + channel * 17, 8192, -2048, -12288, 2048]
                    for _ in range(sections)])
    return fir, sos


def sample_patterns(count, seed):
    rng = random.Random(seed)
    directed = [0, 1, -1, 3, -3, 32767, -32768, 16384, -16384]
    return [directed[n] if n < len(directed) else rng.randint(-32768, 32767)
            for n in range(count)]


def assert_latencies(records, sim, input_stream, output_stream, latency):
    ins = input_records(records, sim, input_stream)
    pubs = output_records(records, sim, output_stream, True)
    assert len(ins) == len(pubs) and ins, "latency case did not have one output per input"
    for n, (accepted, published) in enumerate(zip(ins, pubs)):
        assert published[0] - accepted[0] == latency, (
            f"CYCLE {input_stream}/{output_stream}[{n}] accepted={accepted[0]} "
            f"published={published[0]} expected latency={latency}")


def assert_directed_stalls(records, sim, stream, required=(1, 7, 127)):
    index = list(sim.outputs).index(stream)
    starts = [r for r in records if r[1] == 201 + index]
    ends = [r for r in records if r[1] == 211 + index]
    assert len(starts) == len(ends), "unclosed public output stall"
    for start, end in zip(starts, ends):
        assert end[0] - start[0] == start[2], "wrong observed stalled-cycle count"
    assert set(required).issubset({r[2] for r in starts}), "required output holds absent"


def icarus_startup(evidence, params=None, illegal=False):
    evidence.tool("iverilog")
    evidence.tool("vvp")
    params = params or {"TAP_COUNT": 1, "MAC_LANES": 4, "SOS_COUNT": 1, "SRC_TAPS": 1}
    _, widths, inputs, outputs = interface("top", params)
    stem = unique("icarus-startup")
    tb = evidence.path / f"{stem}.sv"
    signals = []
    connections = []
    for name, width in widths.items():
        if name == "clk":
            signals.append("reg clk=0; always #5 clk=~clk;")
        elif name == "rst_n":
            signals.append("reg rst_n;")
        else:
            signals.append(f"reg [{width-1}:0] {name}=0;")
        connections.append(f".{name}({name})")
    out_widths = {"s_ready": 1, "cfg_ready": 1, "commit_ready": 1, "sos_cfg_ready": 1,
                  "sos_commit_ready": 1, "chain_ready": 1, "feature_ready": 1,
                  "src_cfg_ready": 1, "src_commit_ready": 1, "p_ready": 1,
                  "m_valid": 1, "m_data": 16, "m_channel": 2, "t_valid": 1,
                  "t_data": 16, "t_channel": 2, "t_band": 6, "t_last": 1, "feature_error": 1}
    for name, width in out_widths.items():
        signals.append(f"wire [{width-1}:0] {name};")
        connections.append(f".{name}({name})")
    check = " || ".join(f"{name} !== {w}'d0" for name, w in out_widths.items())
    overrides = ",".join(f".{name}({value})" for name, value in params.items())
    # This initial assertion is at time zero with no prior high-to-low drive.
    body = f"""
module tb;
{chr(10).join(signals)}
billet_f4_top #({overrides}) dut ({','.join(connections)});
task automatic check_reset;
begin if({check}) $fatal(1,"RESET X/Z or nonzero public output"); end
endtask
always @(posedge clk) if(!rst_n) check_reset();
always @(negedge clk) if(!rst_n) check_reset();
initial begin
rst_n=0; #0; check_reset();
#21; rst_n=1;
@(negedge clk); chain_valid=1;chain_mode=0;chain_sections=0;
@(posedge clk); if(chain_ready !== 1'b1) $fatal(1,"legal bypass descriptor");
@(negedge clk);chain_valid=0;s_data=16'h8123;s_channel=3;s_valid=1;
@(posedge clk);if(s_ready !== 1'b1) $fatal(1,"bypass not ready");
#1;if(m_valid !== 1'b1 || m_data !== 16'h8123 || m_channel !== 2'd3)
    $fatal(1,"bypass post E0 value/latency");
@(negedge clk);s_valid=0;
repeat(7) begin @(posedge clk);
if(m_valid !== 1'b1 || m_data !== 16'h8123 || m_channel !== 2'd3)
    $fatal(1,"bypass stall retention"); end
@(negedge clk);m_ready=1;
@(posedge clk);#1;if(m_valid !== 1'b0 || m_data !== 16'd0 || m_channel !== 2'd0)
    $fatal(1,"bypass retirement");
@(negedge clk);s_valid=1;s_data=12;
@(posedge clk);#1;rst_n=0;#0;check_reset();
#11;check_reset();
$display("DONE reset_edges bypass_value stall=7 compared=1");$finish;
end
initial begin repeat(1000) @(posedge clk);$fatal(1,"cycle bound");end
endmodule
"""
    if illegal:
        body = body[:body.index("initial begin\nrst_n=0;")] + """
initial begin rst_n=0; #20; $fatal(1,"missing PARAMETER ERROR"); end
endmodule
"""
    tb.write_text(body)
    binary = evidence.path / f"{stem}.vvp"
    evidence.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", evidence.relative(binary),
                      evidence.relative(tb), *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "rtl").glob("*.sv"))]],
                     "icarus-compile")
    entry = evidence.command(["vvp", evidence.relative(binary)], "icarus-run",
                             allowed=(1,) if illegal else (0,))
    stdout = (ROOT / entry["stdout"]).read_text()
    if illegal:
        assert "PARAMETER ERROR" in stdout, f"wrong illegal-parameter rejection: {stdout}"
    else:
        assert "DONE reset_edges" in stdout
    ledger(evidence, "icarus-startup", parameters=params, illegal=illegal,
           comparisons=0 if illegal else 1, cycles_bound=1000)


def startup(evidence):
    icarus_startup(evidence)
    sim = Simulator(evidence, "log").build()
    program = sim.program()
    energies = [0, 1, 2, 3, (1 << 30), (1 << 30) - 1, (1 << 48) - 1]
    expected = []
    for n, energy in enumerate(energies):
        program.send("energy", energy, n % 4, n, n == len(energies) - 1)
        expected.append((token(energy), n % 4, n, int(n == len(energies) - 1)))
    finish_program(program, "log", len(expected))
    records = sim.execute(program, "startup-log")
    compare(records, sim, "log", expected)
    assert_latencies(records, sim, "energy", "log", 7)
    ledger(evidence, "startup-log", comparisons=len(expected), latency=7, pwl=pwl_error())


def leaves(evidence):
    startup(evidence)
    for taps in (1, 32):
        sim = Simulator(evidence, "src", {"SRC_TAPS": taps}).build()
        program = sim.program()
        expected = []
        for n, value in enumerate(sample_patterns(32, 11)):
            program.send("s", value, n % 4)
            expected.append(pcm(value, n % 4))
        finish_program(program, "m", len(expected))
        records = sim.execute(program, f"identity-src-{taps}")
        compare(records, sim, "m", expected)
        assert_latencies(records, sim, "s", "m", taps + 3)
        ledger(evidence, "src-identity", taps=taps, comparisons=len(expected), latency=taps+3)


def behavior(evidence):
    leaves(evidence)
    elaborate_matrix(evidence)
    legacy_matrix(evidence)
    ratio_matrix(evidence)
    feature_matrix(evidence)
    spectral_matrix(evidence)
    log_matrix(evidence)
    control_matrix(evidence)
    anti_alias(evidence)


def sensitivity(evidence):
    mutation_matrix(evidence)


def synthesis(evidence):
    synthesis_matrix(evidence)


def reproduction(evidence):
    clean_reproduction(evidence)


def portable_smoke(evidence):
    icarus_arithmetic(evidence)
    leaves(evidence)
    complete_frame_smoke(evidence)


def elaborate_matrix(evidence):
    evidence.tool("iverilog")
    rtl = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "rtl").glob("*.sv"))]
    count = 0
    for taps, lanes, sections in itertools.product(TAPS, (1, 2, 4), (1, 2, 3, 4)):
        params = {"TAP_COUNT": taps, "MAC_LANES": lanes, "SOS_COUNT": sections}
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "billet_f4_top", "-t", "null",
                          *[f"-Pbillet_f4_top.{k}={v}" for k, v in params.items()], *rtl],
                         "elaboration")
        ledger(evidence, "legal-elaboration", parameters=params)
        count += 1
    assert count == 60
    for taps in TAPS:
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "billet_f4_top", "-t", "null",
                          f"-Pbillet_f4_top.SRC_TAPS={taps}", *rtl], "src-elaboration")
    for sections in (1, 4):
        icarus_startup(evidence, {"TAP_COUNT": 1, "MAC_LANES": 4,
                                  "SOS_COUNT": sections, "SRC_TAPS": 1})
    for key, value in (("TAP_COUNT", 0), ("TAP_COUNT", 2), ("MAC_LANES", 0),
                       ("MAC_LANES", 3), ("SOS_COUNT", 0), ("SOS_COUNT", 5),
                       ("SRC_TAPS", 0), ("SRC_TAPS", 2)):
        params = {"TAP_COUNT": 1, "MAC_LANES": 4, "SOS_COUNT": 1, "SRC_TAPS": 1}
        params[key] = value
        icarus_startup(evidence, params, illegal=True)
    icarus_arithmetic(evidence)


def icarus_arithmetic(evidence):
    """Real compact log and SRC leaf arithmetic, with strict four-state checks."""
    evidence.tool("iverilog")
    evidence.tool("vvp")
    for kind in ("log", "src"):
        stem = unique(f"icarus-{kind}")
        tb, binary = evidence.path / f"{stem}.sv", evidence.path / f"{stem}.vvp"
        samples = sample_patterns(32, 11)
        if kind == "log":
            energies = [0, 1, 3, 32768, (1 << 30) - 1, 1 << 30, (1 << 48) - 1]
            declared = """reg [47:0] s_energy=0; reg [5:0] s_band=0;reg s_last=0;
wire [5:0] m_band;wire m_last;wire idle;
billet_f4_log dut(.*);"""
            commands = "\n".join(
                f"send(48'd{value},2'd{n%4},6'd{n},1'b{int(n==len(energies)-1)});"
                for n, value in enumerate(energies))
            expected = [(token(e), n % 4, n, int(n == len(energies)-1))
                        for n, e in enumerate(energies)]
            task = """task automatic send(input [47:0] e,input [1:0] ch,input [5:0] b,input last);
begin @(negedge clk);s_energy=e;s_channel=ch;s_band=b;s_last=last;s_valid=1;
@(posedge clk);while(s_ready !== 1'b1) @(posedge clk);
@(negedge clk);s_valid=0;end endtask"""
            payload = "{m_data,m_channel,m_band,m_last}"
            payload_bits = 25
        else:
            declared = """reg signed [15:0] s_data=0;
reg clear_all=0,cfg_valid=0,commit_valid=0;wire cfg_ready,commit_ready,idle;
reg [1:0] cfg_channel=0,commit_channel=0;reg [2:0] cfg_phase=0;
reg [0:0] cfg_tap=0;reg signed [17:0] cfg_data=0;reg [3:0] ratio_l=1,ratio_m=1;
billet_f4_src #(.SRC_TAPS(1)) dut(.*);"""
            commands = "\n".join(f"send(16'h{value&65535:04x},2'd{n%4});"
                                  for n, value in enumerate(samples))
            expected = [(v & 65535, n % 4, 0, 0) for n, v in enumerate(samples)]
            task = """task automatic send(input [15:0] value,input [1:0] ch);
begin @(negedge clk);s_data=value;s_channel=ch;s_valid=1;
@(posedge clk);while(s_ready !== 1'b1) @(posedge clk);
@(negedge clk);s_valid=0;end endtask"""
            payload = "{m_data,m_channel}"
            payload_bits = 18
        values = []
        for data, ch, band, last in expected:
            encoded = ((data << 9) | (ch << 7) | (band << 1) | last) if kind == "log" else (data << 2) | ch
            values.append(encoded)
        assignments = "\n".join(f"expected[{n}]={payload_bits}'d{value};" for n, value in enumerate(values))
        tb.write_text(f"""
module tb;
reg clk=0;always #5 clk=~clk;
reg rst_n;reg s_valid=0,m_ready=0;reg [1:0] s_channel=0;
wire s_ready,m_valid;wire [15:0] m_data;wire [1:0] m_channel;
{declared}
reg [{payload_bits-1}:0] expected[0:{len(expected)-1}];
reg [{payload_bits-1}:0] held_payload;reg held=0;
integer compared=0,cycles=0;
always @(posedge clk) begin
cycles=cycles+1;if(cycles>100000) $fatal(1,"cycle bound");
if(!rst_n) begin
if(s_ready !== 1'b0 || m_valid !== 1'b0 || {payload} !== {payload_bits}'d0)
  $fatal(1,"RESET leaf X/Z");held=0;
end else begin
if(m_valid !== 1'b0 && m_valid !== 1'b1) $fatal(1,"active X/Z valid");
if(held && (m_valid !== 1'b1 || {payload} !== held_payload)) $fatal(1,"stalled payload");
if(m_valid && m_ready) begin
if(compared>={len(expected)} || {payload} !== expected[compared])
  $fatal(1,"NUMERICAL leaf comparison index=%0d",compared);
compared=compared+1;end
held=m_valid && !m_ready;held_payload={payload};
end end
{task}
initial begin
{assignments}
rst_n=0;#0;if(s_ready !== 1'b0 || m_valid !== 1'b0) $fatal(1,"time0 RESET");
#21;rst_n=1;
fork
begin {commands} end
begin
repeat(20) @(negedge clk);m_ready=1;
repeat(1) @(negedge clk);m_ready=0;
repeat(7) @(negedge clk);m_ready=1;
repeat(16) @(negedge clk);m_ready=0;
repeat(127) @(negedge clk);m_ready=1;
end
join
while(compared<{len(expected)}) @(negedge clk);
repeat(20) @(negedge clk);
if(compared!={len(expected)}) $fatal(1,"missing/extra outputs");
$display("DONE compared=%0d",compared);$finish;
end
endmodule
""")
        evidence.command(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", evidence.relative(binary),
                          evidence.relative(tb),
                          *[str(p.relative_to(ROOT)) for p in sorted((ROOT / "rtl").glob("*.sv"))]],
                         f"icarus-{kind}-compile")
        entry = evidence.command(["vvp", evidence.relative(binary)], f"icarus-{kind}-run")
        assert f"DONE compared={len(expected)}" in (ROOT / entry["stdout"]).read_text()
        ledger(evidence, "icarus-arithmetic", leaf=kind, comparisons=len(expected), cycle_bound=100000)


def legacy_matrix(evidence):
    sim = Simulator(evidence, "legacy").build()
    parent = Simulator(evidence, "parent").build()
    fir, sos = legacy_banks()
    for seed, mode in itertools.product(SEEDS, range(5)):
        model = Legacy(fir, sos, mode)
        program, parent_program = sim.program(), parent.program()
        for p in (program, parent_program):
            configure_legacy(p, fir, sos, mode)
            p.random(seed)
        expected = []
        for n, value in enumerate(sample_patterns(512 * 4, seed)):
            channel = n % 4
            for p in (program, parent_program):
                if n % 53 == 0:
                    p.wait(7)
                p.send("s", value, channel)
            expected.append(pcm(model.sample(value, channel), channel))
        for p in (program, parent_program):
            finish_program(p, "m", len(expected))
        actual = sim.execute(program, f"legacy-mode{mode}-seed{seed}")
        historical = parent.execute(parent_program, f"parent-mode{mode}-seed{seed}")
        compare(actual, sim, "m", expected)
        compare(historical, parent, "m", expected)
        assert [r[2:] for r in output_records(actual, sim, "m")] == [
            r[2:] for r in output_records(historical, parent, "m")]
        overlaps = len({r[0] for r in input_records(actual, sim, "s")} & {
            r[0] for r in output_records(actual, sim, "m")})
        if mode == 0:
            assert overlaps > 0, "no simultaneous bypass retirement/admission observed"
        ledger(evidence, "legacy-matrix", seed=seed, mode=mode, samples_per_channel=512,
               simultaneous_retirement_admission=overlaps,
               oracle_comparisons=len(expected), parent_comparisons=len(expected))
    # Exact publication schedules, no output stalls, with each section boundary.
    for taps, lanes, sections in ((1, 4, 1), (1, 4, 4), (8, 2, 2),
                                  (16, 2, 3), (32, 1, 2), (64, 4, 4)):
        params = {"TAP_COUNT": taps, "MAC_LANES": lanes, "SOS_COUNT": sections}
        small = Simulator(evidence, "legacy", params).build()
        banks, feedback = legacy_banks(taps, sections)
        for mode in range(5):
            p, expected = small.program(), []
            configure_legacy(p, banks, feedback, mode, sections)
            model = Legacy(banks, feedback, mode, sections)
            for n, value in enumerate(sample_patterns(16, 47)):
                p.send("s", value, n % 4)
                p.wait(128)
                expected.append(pcm(model.sample(value, n % 4), n % 4))
            finish_program(p, "m", len(expected))
            records = small.execute(p, f"legacy-cycle-{taps}-{lanes}-{sections}-{mode}")
            compare(records, small, "m", expected)
            groups = (taps + lanes - 1) // lanes
            latency = (0, groups + 4, 7 * sections + 1,
                       groups + 7 * sections + 6, groups + 7 * sections + 6)[mode]
            assert_latencies(records, small, "s", "m", latency)
            ledger(evidence, "legacy-cycle", taps=taps, lanes=lanes, sections=sections, mode=mode,
                   latency=latency, comparisons=len(expected))


def ratio_case(sim, taps, l, m, seed, samples=256, stalls=True):
    banks = src_banks(taps, l, seed)
    p = sim.program()
    top = sim.kind == "top"
    if top:
        p.send("chain", 0, 0)
        p.send("feature", 1)
    configure_src(p, banks, l, m, top)
    if stalls:
        p.random(seed)
    model, expected = Resampler(banks, l, m), []
    phases = [set() for _ in range(4)]
    j = [0] * 4
    rng = random.Random(seed)
    for n in range(samples):
        for channel in range(4):
            pattern = n % 5
            value = (12000 if pattern == 0 else (32767 if n & 1 else -32768)
                     if pattern == 1 else (12000 if n == 2 else 0)
                     if pattern == 2 else (-16000 if n & 1 else 16000)
                     if pattern == 3 else rng.randint(-32768, 32767))
            if n % 23 == 0:
                p.wait(1 + channel)
            p.send("s", value, channel)
            for output in model.sample(value, channel):
                phases[channel].add((j[channel] * m) % l)
                j[channel] += 1
                expected.append(pcm(output, channel))
    finish_program(p, "m", len(expected), tail=4096)
    records = sim.execute(p, f"src-{taps}-{l}-{m}-seed{seed}")
    compare(records, sim, "m", expected)
    if top:
        assert not output_records(records, sim, "t"), "tokens leaked in SRC PCM mode"
    reached = {(j * m) % l for j in range(l)}
    assert all(active == reached for active in phases), f"unreached active phase: {phases} {reached}"
    assert len(input_records(records, sim, "s")) == samples * 4
    return records, expected, phases


def assert_src_scheduler(records, sim, taps, l, m):
    """Infer publication/retirement from pins; do not read phase/gap registers."""
    inputs = input_records(records, sim, "s")
    publications = output_records(records, sim, "m", True)
    outputs = output_records(records, sim, "m")
    counts, j = [0] * 4, [0] * 4
    output_index = 0
    for accepted in inputs:
        channel = accepted[3]
        n = counts[channel]
        counts[channel] += 1
        previous_retirement = None
        while (j[channel] * m) // l == n:
            published = publications[output_index]
            retired = outputs[output_index]
            if previous_retirement is None:
                # This routine uses a SRC leaf; accepted is precisely anchor E0.
                assert published[0] == accepted[0] + taps + 3, (
                    f"SRC anchor latency n={n}, L/M={l}/{m}: {published[0]-accepted[0]}")
            else:
                # For same-anchor interpolation, q=0 and wrap commit at R1.
                assert published[0] == previous_retirement + 1 + taps + 3, "SRC reused-anchor scheduler"
            assert published[3] == channel and retired[0] >= published[0] + 1
            previous_retirement = retired[0]
            j[channel] += 1
            output_index += 1
    assert output_index == len(outputs) == len(publications)
    # Normalization after the final output determines the next accepted slot;
    # source gaps can delay it, but never make it earlier than q+2.
    for index in range(len(inputs) - 1):
        current, following = inputs[index], inputs[index + 1]
        retiring = [r for r in outputs if current[0] < r[0] < following[0]]
        if retiring:
            last = retiring[-1]
            ch = current[3]
            prior = sum(1 for r in outputs if r[3] == ch and r[0] < last[0])
            phase = (prior * m) % l
            q = (phase + m) // l
            assert following[0] >= last[0] + q + 2, "SRC bounded-subtraction acceptance sequence"


def ratio_matrix(evidence):
    sim = Simulator(evidence, "top").build()
    for seed, l, m in itertools.product(SEEDS, range(1, 9), range(1, 9)):
        _, expected, phases = ratio_case(sim, 32, l, m, seed)
        ledger(evidence, "ratio-matrix", seed=seed, l=l, m=m, taps=32,
               samples_per_channel=256, comparisons=len(expected),
               reached_phases=[sorted(p) for p in phases])
    for taps in TAPS:
        leaf = Simulator(evidence, "src", {"SRC_TAPS": taps}).build()
        for l, m in ((1, 1), (2, 3), (8, 1), (1, 8)):
            records, expected, _ = ratio_case(leaf, taps, l, m, 47, 256)
            assert_src_scheduler(records, leaf, taps, l, m)
            ledger(evidence, "src-tap-boundary", taps=taps, l=l, m=m,
                   comparisons=len(expected), samples_per_channel=256)
        src_pattern_cases(evidence, leaf, taps)
    scheduler = Simulator(evidence, "src", {"SRC_TAPS": 1}).build()
    for l, m in itertools.product(range(1, 9), repeat=2):
        records, expected, _ = ratio_case(scheduler, 1, l, m, 11, samples=32, stalls=False)
        assert_src_scheduler(records, scheduler, 1, l, m)
        ledger(evidence, "all-ratio-cycle-scheduler", l=l, m=m, comparisons=len(expected))
    src_reconfiguration(evidence)


def audio_frame(index, length, rate, seed):
    rng = random.Random(seed + index * 7919)
    if index % 8 == 0:
        return [0] * length
    if index % 8 == 1:
        return [24000] + [0] * (length - 1)
    if index % 8 == 2:
        return [12000] * length
    if index % 8 == 3:
        return [round(12000 * math.sin(2 * math.pi * 1000 * n / rate)) for n in range(length)]
    if index % 8 == 4:
        return [round(7000 * math.sin(2 * math.pi * 500 * n / rate)
                      + 5000 * math.sin(2 * math.pi * 3500 * n / rate))
                for n in range(length)]
    if index % 8 == 5:
        return [rng.randint(-32768, 32767) for _ in range(length)]
    if index % 8 == 6:
        return [32767 if n & 1 else -32768 for n in range(length)]
    return [round(16000 * math.sin(2 * math.pi * (rate / length * 7) * n / rate))
            for n in range(length)]


def feature_matrix(evidence):
    top = Simulator(evidence, "top").build()
    dft = Simulator(evidence, "spectrum").build()
    energy = Simulator(evidence, "mel").build()
    for seed, (rate, l, m) in itertools.product(SEEDS, (
            (8000, 2, 1), (16000, 1, 1), (24000, 2, 3),
            (32000, 1, 2), (48000, 1, 3))):
        top_p, dft_p, mel_p = top.program(), dft.program(), energy.program()
        fir, sos = legacy_banks()
        chain_mode = {11: 1, 29: 3, 47: 4}[seed]
        configure_legacy(top_p, fir, sos, chain_mode)
        top_p.send("feature", 2)
        coefficients = lowpass(32, min(0.4, 0.4 * l / m))
        banks = [[coefficients[:] for _ in range(l)] for _ in range(4)]
        configure_src(top_p, banks, l, m)
        model = Resampler(banks, l, m)
        legacy_model = Legacy(fir, sos, chain_mode)
        expected_tokens, expected_powers, expected_energies = [], [], []
        for p in (top_p, dft_p, mel_p):
            p.random(seed)
        for channel in range(4):
            for frame in range(8):
                incoming = audio_frame(frame, 512 * m // l, rate, seed + channel * 41)
                converted = []
                for n, value in enumerate(incoming):
                    if n % 127 == 0:
                        top_p.wait(7)
                    top_p.send("s", value, channel)
                    converted += model.sample(legacy_model.sample(value, channel), channel)
                assert len(converted) == 512
                powers = spectrum(converted)
                energies = mel(powers)
                expected_tokens += tagged([token(e) for e in energies], channel)
                expected_powers += tagged(powers, channel)
                expected_energies += tagged(energies, channel)
                for value in converted:
                    dft_p.send("s", value, channel)
                for k, power in enumerate(powers):
                    mel_p.send("p", power, channel, k, k == 256)
                if frame in (0, 1, 2):
                    hold = (1, 7, 127)[frame]
                    top_p.stall("t", hold)
                    dft_p.stall("p", hold)
                    mel_p.stall("e", hold)
                top_p.drain("t", len(expected_tokens))
                dft_p.drain("p", len(expected_powers))
                mel_p.drain("e", len(expected_energies))
        for p, stream, expected in ((top_p, "t", expected_tokens),
                                    (dft_p, "p", expected_powers), (mel_p, "e", expected_energies)):
            finish_program(p, stream, len(expected))
        tr = top.execute(top_p, f"pcm-top-{rate}-seed{seed}")
        dr = dft.execute(dft_p, f"dft-powers-{rate}-seed{seed}")
        mr = energy.execute(mel_p, f"mel-energies-{rate}-seed{seed}")
        compare(tr, top, "t", expected_tokens)
        compare(dr, dft, "p", expected_powers)
        compare(mr, energy, "e", expected_energies)
        assert_directed_stalls(tr, top, "t")
        assert_directed_stalls(dr, dft, "p")
        assert_directed_stalls(mr, energy, "e")
        assert not output_records(tr, top, "m"), "PCM leaked in mode2"
        ledger(evidence, "pcm-feature-matrix", rate=rate, l=l, m=m, seed=seed,
               chain_mode=chain_mode,
               frames_per_channel=8, channels=4, power_comparisons=len(expected_powers),
               energy_comparisons=len(expected_energies), token_comparisons=len(expected_tokens),
               source_inputs_per_channel=8 * 512 * m // l)
    # A no-stall leaf schedule is distinct from arithmetic with injected stalls.
    spectrum_cycles(evidence, dft)
    mel_cycles(evidence, energy)


def spectrum_cycles(evidence, sim):
    p = sim.program()
    samples = audio_frame(4, 512, 16000, 11)
    for value in samples:
        p.send("s", value, 2)
    finish_program(p, "p", 257)
    records = sim.execute(p, "dft-cycle-table")
    compare(records, sim, "p", tagged(spectrum(samples), 2))
    accepted = input_records(records, sim, "s")
    published = output_records(records, sim, "p", True)
    retired = output_records(records, sim, "p")
    # Final window write E2 seals the buffer; dispatch D0 is the next edge.
    assert published[0][0] == accepted[-1][0] + 2 + 1 + 517, "window E2 / DFT D517"
    for n in range(1, 257):
        assert published[n][0] == retired[n-1][0] + 517, "DFT next-bin D0 / D517"
    for left, right in zip(accepted, accepted[1:]):
        assert right[0] - left[0] == 3, "window sample interval E0,E1,E2"
    ledger(evidence, "spectrum-cycle", power_comparisons=257, bin_publication=517, window_write=2)


def mel_cycles(evidence, sim):
    p = sim.program()
    powers = [((k * 2654435761) & 0xffffffff) for k in range(257)]
    for k, value in enumerate(powers):
        p.send("p", value, 1, k, k == 256)
    finish_program(p, "e", 64)
    records = sim.execute(p, "mel-cycle-table")
    compare(records, sim, "e", tagged(mel(powers), 1))
    accepted = input_records(records, sim, "p")
    published = output_records(records, sim, "e", True)
    retired = output_records(records, sim, "e")
    assert published[0][0] == accepted[-1][0] + 1 + 260, "Mel B0/B260"
    for n in range(1, 64):
        assert published[n][0] == retired[n-1][0] + 260, "Mel next-band B0/B260"
    ledger(evidence, "mel-cycle", energy_comparisons=64, publication=260)


def complete_frame_smoke(evidence):
    sim = Simulator(evidence, "top", {"TAP_COUNT": 1, "MAC_LANES": 4,
                                    "SOS_COUNT": 1, "SRC_TAPS": 1}).build()
    p = sim.program()
    p.send("chain", 0, 0)
    p.send("feature", 2)
    reject_descriptor(p, "src_cfg_ready", "src_cfg_valid",
                      {"src_cfg_channel": 0, "src_cfg_phase": 0, "src_cfg_tap": 1,
                       "src_cfg_data": 65536})
    banks = [[[65536], [49152]] for _ in range(4)]
    configure_src(p, banks, 2, 3)
    reference = Resampler(banks, 2, 3)
    converted = []
    for value in audio_frame(4, 768, 24000, 11):
        p.send("s", value, 3)
        converted += reference.sample(value, 3)
    assert len(converted) == 512
    p.stall("t", 127)
    finish_program(p, "t", 64)
    records = sim.execute(p, "portable-src-complete-token-frame")
    compare(records, sim, "t", tagged([token(e) for e in mel(spectrum(converted))], 3))
    assert not output_records(records, sim, "m")
    ledger(evidence, "portable-src-complete-frame", source_inputs=768, converted_samples=512,
           token_comparisons=64, l=2, m=3, channel=3)


def spectral_matrix(evidence):
    top = Simulator(evidence, "top").build()
    leaf = Simulator(evidence, "mel").build()
    p, q = top.program(), leaf.program()
    p.send("feature", 3)
    p.check("s_ready", 0)
    for program in (p, q):
        program.random(29)
    expected_tokens, expected_energies = [], []
    frames = [[0] * 257, [0xffffffff] * 257]
    for k in range(257):
        values = [0] * 257
        values[k] = 1 << 30
        frames.append(values)
    rng = random.Random(47)
    frames += [[rng.getrandbits(32) for _ in range(257)] for _ in range(4)]
    support = set()
    for index, powers in enumerate(frames):
        channel = index % 4
        energies = mel(powers)
        support |= {b for b, e in enumerate(energies) if e}
        expected_energies += tagged(energies, channel)
        expected_tokens += tagged([token(e) for e in energies], channel)
        for k, power in enumerate(powers):
            p.send("p", power, channel, k, k == 256)
            q.send("p", power, channel, k, k == 256)
        if index < 3:
            p.stall("t", (1, 7, 127)[index])
            q.stall("e", (1, 7, 127)[index])
        p.drain("t", len(expected_tokens))
        q.drain("e", len(expected_energies))
    assert support == set(range(64)), "spectral matrix did not exercise every band"
    # Each malformed frame must be suppressed, followed by a successful frame.
    malformed = [
        [(3, 0, 1, 1)],                       # initial wrong bin, immediate last
        [(3, 0, 0, 1)],                       # early last
        [(3, 0, 0, 0), (3, 1, 1, 0), (3, 1, 2, 1)],  # channel switch / discard
        [(3, 0, 0, 0), (3, 0, 2, 0), (3, 0, 255, 1)],  # skipped bin
        [(3, 0, k, 0) for k in range(257)] + [(3, 0, 0, 1)],  # missing final last
    ]
    for index, bad in enumerate(malformed):
        for beat in bad:
            p.send("p", *beat)
            q.send("p", *beat)
        for program in (p, q):
            program.check("feature_error", 1)
            program.wait(300)
        recovery = [((k + index) * 65537) & 0xffffffff for k in range(257)]
        energies = mel(recovery)
        expected_tokens += tagged([token(e) for e in energies], 2)
        expected_energies += tagged(energies, 2)
        for k, value in enumerate(recovery):
            p.send("p", value, 2, k, k == 256)
            q.send("p", value, 2, k, k == 256)
        p.drain("t", len(expected_tokens))
        q.drain("e", len(expected_energies))
    p.send("feature", 0)
    p.check("feature_error", 0)
    p.check("p_ready", 0)
    for program, stream, expected in ((p, "t", expected_tokens), (q, "e", expected_energies)):
        finish_program(program, stream, len(expected))
    top_records = top.execute(p, "spectral-all-impulses-recovery")
    leaf_records = leaf.execute(q, "spectral-all-energies-recovery")
    compare(top_records, top, "t", expected_tokens)
    compare(leaf_records, leaf, "e", expected_energies)
    assert_directed_stalls(top_records, top, "t")
    assert_directed_stalls(leaf_records, leaf, "e")
    assert not output_records(top_records, top, "m")
    ledger(evidence, "direct-spectral", impulses=257, bands=64, malformed_cases=len(malformed),
           recovery_frames=len(malformed), token_comparisons=len(expected_tokens),
           energy_comparisons=len(expected_energies))


def log_energies():
    values = {0, 1, (1 << 48) - 1}
    for exponent in range(48):
        power = 1 << exponent
        values.update(v for v in (power - 1, power, power + 1) if 0 <= v < (1 << 48))
        for segment in range(17):
            boundary = power + (power * segment // 16)
            values.update(v for v in (boundary - 1, boundary, boundary + 1)
                          if 0 <= v < (1 << 48))
    rng = random.Random(47)
    values.update(rng.getrandbits(48) for _ in range(4096))
    assert len(values) >= 4096
    return sorted(values)


def log_matrix(evidence):
    sim = Simulator(evidence, "log").build()
    energies = log_energies()
    p, expected = sim.program(), []
    for n, e in enumerate(energies):
        p.send("energy", e, n % 4, n % 64, n % 64 == 63)
        expected.append((token(e), n % 4, n % 64, int(n % 64 == 63)))
    finish_program(p, "log", len(expected))
    records = sim.execute(p, "log-boundaries-random")
    compare(records, sim, "log", expected)
    assert_latencies(records, sim, "energy", "log", 7)
    assert any(t[0] == 0 for t in expected) and any(t[0] == 65535 for t in expected)
    # Independent boundary run with randomized readiness and directed holds.
    p, expected = sim.program(), []
    p.random(11)
    for n, energy in enumerate(energies[:1024]):
        p.send("energy", energy, n % 4, n % 64, n % 64 == 63)
        expected.append((token(energy), n % 4, n % 64, int(n % 64 == 63)))
        if n in (0, 64, 512):
            p.stall("log", (1, 7, 127)[(0, 64, 512).index(n)])
    p.drain("log", len(expected))
    p.random(0)
    p.send("energy", 8919191, 1, 7, 0)
    p.wait(2)
    p.check("m_valid", 0)
    p.reset()
    p.check("m_valid", 0)
    p.send("energy", 1 << 30, 3, 63, 1)
    expected.append((65535, 3, 63, 1))
    finish_program(p, "log", len(expected))
    stalled = sim.execute(p, "log-stalled-boundaries")
    compare(stalled, sim, "log", expected)
    assert_directed_stalls(stalled, sim, "log")
    ledger(evidence, "log-matrix", random_energies=4096, exponent_transitions=48,
           pwl_segments=16, comparisons=len(energies)+len(expected), pwl=pwl_error())
    p, expected = sim.program(), []
    p.set("m_ready", 0)
    for n in range(8):
        energy = (1 << 23) + n * 73129
        p.send("energy", energy, n % 4, n, n == 7)
        expected.append((token(energy), n % 4, n, int(n == 7)))
    p.check("m_valid", 1)
    p.check("s_ready", 0)
    p.wait(127)
    p.check("s_ready", 0)
    p.set("m_ready", 1)
    p.send("energy", 1 << 30, 2, 63, 1)
    expected.append((65535, 2, 63, 1))
    finish_program(p, "log", len(expected))
    full = sim.execute(p, "log-full-eight-register-stall")
    compare(full, sim, "log", expected)
    accepted = input_records(full, sim, "energy")
    retired = output_records(full, sim, "log")
    assert len(accepted) == 9 and accepted[8][0] == retired[0][0], "log retirement/admission coincidence"
    ledger(evidence, "log-full-pipeline-stall", comparisons=9, occupied_registers=8,
           stalled_cycles=127, simultaneous_retirement_admission=1)


def anti_alias(evidence):
    sim = Simulator(evidence, "src").build()
    coefficients = lowpass(32, 0.12)
    metrics = {}
    for frequency in (1000, 12000):
        p = sim.program()
        banks = [[coefficients[:]] for _ in range(4)]
        configure_src(p, banks, 1, 3, False)
        model, expected = Resampler(banks, 1, 3), []
        for n in range(12288):
            value = round(12000 * math.sin(2 * math.pi * frequency * n / 48000))
            p.send("s", value, 0)
            expected += [pcm(v, 0) for v in model.sample(value, 0)]
        finish_program(p, "m", len(expected))
        records = sim.execute(p, f"anti-alias-{frequency}hz")
        compare(records, sim, "m", expected)
        signed = [r[2] - 65536 if r[2] & 32768 else r[2] for r in output_records(records, sim, "m")]
        settled = signed[32:]
        assert len(settled) == 4064
        metrics[str(frequency)] = {
            "input_samples": 12288, "output_samples": len(signed),
            "transient_output_samples": 32, "transient_input_anchors": 96,
            "measured_samples": len(settled), "rms": math.sqrt(sum(v*v for v in settled)/len(settled))}
    pass_rms, stop_rms = metrics["1000"]["rms"], metrics["12000"]["rms"]
    rejection = math.inf if stop_rms == 0 else 20 * math.log10(pass_rms / stop_rms)
    amplitude_error = abs(20 * math.log10(pass_rms / (12000 / math.sqrt(2))))
    ledger(evidence, "measured-anti-alias", coefficients_q16=coefficients, l=1, m=3,
           metrics=metrics, stop_rejection_db=rejection, pass_amplitude_error_db=amplitude_error,
           required_rejection_db=35, maximum_amplitude_error_db=1)
    assert rejection >= 35, f"ANTI_ALIAS rejection {rejection} dB <35"
    assert amplitude_error <= 1, f"ANTI_ALIAS pass amplitude error {amplitude_error} dB >1"


def reject_descriptor(p, ready, valid, fields, cycles=7):
    for name, value in fields.items():
        p.set(name, value)
    p.set(valid, 1)
    p.check(ready, 0)
    p.wait(cycles)
    p.check(ready, 0)
    p.set(valid, 0)


def control_matrix(evidence):
    """Directed update, reset and reservation interactions, all through pins."""
    sim = Simulator(evidence, "top", {"TAP_COUNT": 8, "MAC_LANES": 4,
                                    "SOS_COUNT": 4, "SRC_TAPS": 8}).build()
    p, expected = sim.program(), []
    fir, sos = legacy_banks(8, 4)
    reset_model = Legacy([[0] * 8 for _ in range(4)],
                         [[[0] * 5 for _ in range(4)] for _ in range(4)])
    for ch, value in enumerate((123, -32768, 32767, -3)):
        p.send("s", value, ch)
        expected.append(pcm(reset_model.sample(value, ch), ch))
    p.drain("m", len(expected))
    # Empty inactive banks must not commit, including repeated commit after swap.
    reject_descriptor(p, "commit_ready", "commit_valid", {"commit_channel": 0})
    reject_descriptor(p, "sos_commit_ready", "sos_commit_valid", {"sos_commit_channel": 0})
    reject_descriptor(p, "src_commit_ready", "src_commit_valid",
                      {"src_commit_channel": 0, "src_l": 1, "src_m": 1})
    configure_legacy(p, fir, sos, 1)
    model = Legacy(fir, sos, 1)
    model.history = reset_model.history
    reject_descriptor(p, "commit_ready", "commit_valid", {"commit_channel": 0})
    reject_descriptor(p, "sos_commit_ready", "sos_commit_valid", {"sos_commit_channel": 0})
    for mode, sections in ((0, 1), (1, 1), (2, 0), (3, 5), (4, 0), (5, 1), (7, 7)):
        reject_descriptor(p, "chain_ready", "chain_valid",
                          {"chain_mode": mode, "chain_sections": sections})
    reject_descriptor(p, "sos_cfg_ready", "sos_cfg_valid",
                      {"sos_cfg_channel": 0, "sos_cfg_section": 0, "sos_cfg_addr": 5, "sos_cfg_data": 3})
    for value in (1, 3, -1, -3, 32767, -32768):
        p.send("s", value, 0)
        expected.append(pcm(model.sample(value, 0), 0))
    p.drain("m", len(expected))
    # Push another sample, then write a complete inactive FIR bank during its
    # live reservation. Its result must still use the old active bank.
    p.send("s", 12345, 0)
    expected.append(pcm(model.sample(12345, 0), 0))
    replacement = [16384, 16384, 0, 0, 0, 0, 0, 0]
    for tap, value in enumerate(replacement):
        p.send("cfg", 0, tap, value)
    p.drain("m", len(expected))
    p.send("commit", 0)
    model.fir[0] = replacement
    for value in (1, 3, -1, -3):
        p.send("s", value, 0)
        expected.append(pcm(model.sample(value, 0), 0))
    p.drain("m", len(expected))
    # Rewrite an entry twice before commit. Both banks require fresh masks.
    for tap in range(8):
        p.send("cfg", 0, tap, 0)
    p.send("cfg", 0, 0, 32767)
    p.send("cfg", 0, 0, -32768)
    p.send("commit", 0)
    model.fir[0] = [-32768] + [0] * 7
    for value in (32767, -32768, 1, -1):
        p.send("s", value, 0)
        expected.append(pcm(model.sample(value, 0), 0))
    p.drain("m", len(expected))
    # True ties and signed saturation in FIR, not only patterned input labels.
    for bank in ([16384] + [0] * 7, [32767] * 8, [-32768] * 8):
        for tap, value in enumerate(bank):
            p.send("cfg", 1, tap, value)
        p.send("commit", 1)
        model.fir[1] = bank
        for value in (1, 3, -1, -3, 32767, -32768, 32767, 32767, -32768, -32768):
            p.send("s", value, 1)
            expected.append(pcm(model.sample(value, 1), 1))
        p.drain("m", len(expected))
    # Prepare complete FIR and SOS staging banks for simultaneous priority.
    for tap, value in enumerate(fir[2]):
        p.send("cfg", 2, tap, value)
    for section, bank in enumerate(sos[2]):
        for addr, value in enumerate(bank):
            p.send("sos_cfg", 2, section, addr, value)
    p.set("commit_channel", 2)
    p.set("sos_commit_channel", 2)
    p.set("chain_mode", 3)
    p.set("chain_sections", 4)
    for valid in ("chain_valid", "sos_commit_valid", "commit_valid"):
        p.set(valid, 1)
    p.check("chain_ready", 1)
    p.check("sos_commit_ready", 0)
    p.check("commit_ready", 0)
    p.wait(1)
    p.set("chain_valid", 0)
    p.check("sos_commit_ready", 1)
    p.check("commit_ready", 0)
    p.wait(1)
    p.set("sos_commit_valid", 0)
    p.check("commit_ready", 1)
    p.wait(1)
    p.set("commit_valid", 0)
    model.mode = 3
    model.clear_sos()
    model.fir[2] = fir[2]
    model.clear_sos(2)
    for n, value in enumerate(sample_patterns(64, 29)):
        p.send("s", value, n % 4)
        expected.append(pcm(model.sample(value, n % 4), n % 4))
    p.drain("m", len(expected))
    # Reset aborts busy legacy work, not a fabricated output comparison.
    p.set("m_ready", 0)
    p.send("s", 17000, 3)
    p.wait(2)
    p.check("m_valid", 0)
    p.reset()
    p.check("m_valid", 0)
    p.set("m_ready", 1)
    p.send("chain", 0, 0)
    p.send("s", -7, 3)
    expected.append(pcm(-7, 3))
    p.drain("m", len(expected))
    # Outstanding published output is also canceled by asynchronous reset.
    p.set("m_ready", 0)
    p.send("s", 99, 0)
    p.check("m_valid", 1)
    p.reset()
    p.set("m_ready", 1)
    p.send("chain", 0, 0)
    p.send("feature", 1)
    banks = src_banks(8, 2, 11)
    configure_src(p, banks, 2, 3)
    reject_descriptor(p, "src_commit_ready", "src_commit_valid",
                      {"src_commit_channel": 0, "src_l": 2, "src_m": 3})
    for l, m in ((0, 1), (9, 1), (1, 0), (1, 9)):
        reject_descriptor(p, "src_commit_ready", "src_commit_valid",
                          {"src_commit_channel": 0, "src_l": l, "src_m": m})
    src_model = Resampler(banks, 2, 3)
    for n, value in enumerate(sample_patterns(32, 11)):
        p.send("s", value, n % 4)
        expected += [pcm(v, n % 4) for v in src_model.sample(value, n % 4)]
    p.drain("m", len(expected))
    p.wait(32)
    # Common eligible boundary: feature request wins and does not accidentally
    # consume the complete SRC descriptor underneath it.
    for phase in range(2):
        for tap, value in enumerate(banks[0][phase]):
            p.send("src_cfg", 0, phase, tap, value)
    p.set("src_commit_channel", 0)
    p.set("src_l", 2)
    p.set("src_m", 3)
    p.set("src_commit_valid", 1)
    p.set("feature_mode", 2)
    p.set("feature_valid", 1)
    p.check("feature_ready", 1)
    p.check("src_commit_ready", 0)
    p.check("s_ready", 0)
    p.wait(1)
    p.set("feature_valid", 0)
    p.check("src_commit_ready", 1)
    p.wait(1)
    p.set("src_commit_valid", 0)
    # A partial PCM frame must not deadlock mode change; inherited history
    # persists, new SRC history and feature framing are discarded explicitly.
    for value in range(20):
        p.send("s", value, 0)
    p.wait(64)
    p.send("feature", 3)
    p.send("p", 17, 1, 0, 0)
    p.send("feature", 0)
    p.check("feature_error", 0)
    p.check("p_ready", 0)
    p.send("s", -199, 2)
    expected.append(pcm(-199, 2))
    p.drain("m", len(expected))
    p.wait(32)
    for tap in range(8):
        p.send("src_cfg", 3, 0, tap, 65536 if tap == 0 else 0)
    p.set("src_commit_channel", 3)
    p.set("src_l", 1)
    p.set("src_m", 1)
    p.set("src_commit_valid", 1)
    p.set("chain_mode", 0)
    p.set("chain_sections", 0)
    p.set("chain_valid", 1)
    p.check("src_commit_ready", 1)
    p.check("chain_ready", 0)
    p.wait(1)
    p.set("src_commit_valid", 0)
    p.check("chain_ready", 1)
    p.wait(1)
    p.set("chain_valid", 0)
    finish_program(p, "m", len(expected))
    records = sim.execute(p, "controls-priorities-banks-reset")
    compare(records, sim, "m", expected)
    assert not output_records(records, sim, "t"), "discarded partial frame produced tokens"
    commits = input_records(records, sim, "commit")
    sos_commits = input_records(records, sim, "sos_commit")
    chains = input_records(records, sim, "chain")
    prioritized = [r for r in chains if r[2] == 3 and r[3] == 4]
    assert len(prioritized) == 1
    boundary = prioritized[0][0]
    assert any(r[0] == boundary + 1 and r[2] == 2 for r in sos_commits)
    assert any(r[0] == boundary + 2 and r[2] == 2 for r in commits)
    features = input_records(records, sim, "feature")
    src_commits = input_records(records, sim, "src_commit")
    feature_boundary = [r for r in features if r[2] == 2]
    assert len(feature_boundary) == 1
    assert any(r[0] == feature_boundary[0][0] + 1 and r[2] == 0 for r in src_commits)
    samples = input_records(records, sim, "s")
    writes = input_records(records, sim, "cfg")
    retirements = output_records(records, sim, "m")
    busy_write_edges = sum(
        any(sample[0] < write[0] < retirement[0]
            for sample, retirement in zip(samples[:11], retirements[:11]))
        for write in writes)
    assert busy_write_edges > 0, "inactive-bank writes were not accepted during live work"
    src_priority = [r for r in src_commits if r[2] == 3 and r[3] == 1 and r[4] == 1]
    assert len(src_priority) == 1
    assert any(r[0] == src_priority[0][0]+1 and r[2] == 0 for r in chains)
    ledger(evidence, "control-matrix", pcm_comparisons=len(expected),
           explicit_busy_reset_cancellations=1, explicit_stalled_reset_cancellations=1,
           mode_discard_pcm_samples=20, mode_discard_spectral_beats=1,
           simultaneous_priority_edges=7, illegal_chain_descriptors=7,
           observed_busy_inactive_writes=busy_write_edges)
    reset_feature_engines(evidence)
    round_robin_frames(evidence)
    legacy_arithmetic_extremes(evidence)
    mode_history_preservation(evidence, sim)
    channel_frame_clear(evidence)


def reset_feature_engines(evidence):
    sim = Simulator(evidence, "top", {"TAP_COUNT": 1, "MAC_LANES": 4,
                                    "SOS_COUNT": 1, "SRC_TAPS": 1}).build()
    p = sim.program()
    # Reset within SRC work, after the public legacy bypass has retired into it.
    p.send("chain", 0, 0)
    p.send("feature", 1)
    p.send("s", 777, 2)
    p.wait(2)
    p.check("m_valid", 0)
    p.reset()
    # Reset after completing a PCM buffer while DFT is still computing.
    p.send("chain", 0, 0)
    p.send("feature", 2)
    for value in audio_frame(3, 512, 16000, 11):
        p.send("s", value, 1)
    p.wait(100)
    p.check("t_valid", 0)
    p.reset()
    # Reset Mel work during a complete spectral frame; no partial tokens allowed.
    p.send("feature", 3)
    for k in range(257):
        p.send("p", 1 << 30, 0, k, k == 256)
    p.wait(100)
    p.check("t_valid", 0)
    p.reset()
    # A published stalled token must persist for 127 cycles and then be aborted.
    p.send("feature", 3)
    p.set("t_ready", 0)
    for k in range(257):
        p.send("p", 1 << 30, 3, k, k == 256)
    p.wait(300)
    p.check("t_valid", 1)
    p.wait(127)
    p.reset()
    p.set("t_ready", 1)
    p.send("feature", 3)
    powers = [0] * 257
    for k, value in enumerate(powers):
        p.send("p", value, 2, k, k == 256)
    finish_program(p, "t", 64)
    records = sim.execute(p, "reset-src-dft-mel-token")
    compare(records, sim, "t", tagged([0] * 64, 2))
    assert not output_records(records, sim, "m")
    ledger(evidence, "reset-feature-engines", token_comparisons=64,
           aborted_src_anchors=1, aborted_dft_frames=1, aborted_mel_frames=1,
           aborted_stalled_token_frames=1, stalled_cycles=127)


def round_robin_frames(evidence):
    sim = Simulator(evidence, "spectrum").build()
    p = sim.program()
    frames = [audio_frame(ch + 2, 512, 16000, 29) for ch in range(4)]
    # Near-simultaneous completeness while first frame's DFT is busy leaves
    # all four buffers reserved. Public bin/channel sequence proves arbitration.
    for n in range(512):
        for ch in range(4):
            p.send("s", frames[ch][n], ch)
    expected = [item for ch in range(4) for item in tagged(spectrum(frames[ch]), ch)]
    finish_program(p, "p", len(expected))
    records = sim.execute(p, "round-robin-four-complete-frames")
    compare(records, sim, "p", expected)
    ledger(evidence, "round-robin", channels=[0, 1, 2, 3], power_comparisons=len(expected))


def synthesis_matrix(evidence, only=None):
    evidence.tool("yosys")
    configurations = [
        ("default", {}),
        ("minimum", {"TAP_COUNT": 1, "MAC_LANES": 1, "SOS_COUNT": 1, "SRC_TAPS": 1}),
        ("maximum", {"TAP_COUNT": 64, "MAC_LANES": 4, "SOS_COUNT": 4, "SRC_TAPS": 64}),
    ]
    required = ("billet_f3_top", "billet_f4_src", "billet_f4_spectrum", "billet_f4_mel", "billet_f4_log")
    forbidden = {"$div", "$mod", "$divfloor", "$modfloor", "$dlatch", "$adlatch", "$dlatchsr"}
    for name, parameters in configurations:
        if only and name != only:
            continue
        stem = unique(f"yosys-{name}")
        script, netlist = evidence.path / f"{stem}.ys", evidence.path / f"{stem}.json"
        rtl = " ".join(str(p.relative_to(ROOT)) for p in sorted((ROOT / "rtl").glob("*.sv")))
        change = ("chparam " + " ".join(f"-set {k} {v}" for k, v in parameters.items())
                  + " billet_f4_top;\n") if parameters else ""
        script.write_text(
            f"read_verilog -sv {rtl};\n{change}"
            "hierarchy -check -top billet_f4_top;\n"
            "proc; opt_expr; opt_clean; check -assert;\n"
            "memory -nomap; opt_clean; check -assert;\n"
            "select -assert-none t:$div t:$mod t:$divfloor t:$modfloor "
            "t:$dlatch t:$adlatch t:$dlatchsr;\n"
            f"stat;\nwrite_json {evidence.relative(netlist)};\n")
        evidence.command(["yosys", "-s", evidence.relative(script)], f"synthesis-{name}")
        document = json.loads(netlist.read_text())
        modules = document["modules"]
        assert "billet_f4_top" in modules
        instances = {}
        cell_counts = {}

        def original_name(key):
            value = modules[key].get("attributes", {}).get("hdlname", key)
            return str(value).split()[0].lstrip("\\")

        def walk(key, lineage=()):
            assert key not in lineage, "recursive synthesis hierarchy"
            counts = {"state": 0, "arithmetic": 0, "memory_bits": 0}
            for cell_name, cell in modules[key].get("cells", {}).items():
                cell_type = cell["type"]
                assert cell_type not in forbidden, f"forbidden synthesized cell {key}.{cell_name}: {cell_type}"
                if cell_type in modules:
                    child = walk(cell_type, lineage + (key,))
                    child_name = original_name(cell_type)
                    if child_name in required:
                        instances.setdefault(child_name, []).append({"instance": cell_name, **child})
                        # All streaming signals must remain connected, not dummy
                        # leaf implementations with constant-only data/metadata.
                        ports = modules[cell_type]["ports"]
                        for port, info in ports.items():
                            if port in ("clk", "rst_n"):
                                continue
                            bits = cell["connections"].get(port, [])
                            assert bits, f"missing connection {child_name}.{port}"
                            if info["direction"] == "output" and port.endswith(("_data", "_power", "_energy", "_valid")):
                                assert any(isinstance(bit, int) for bit in bits), f"constant output {child_name}.{port}"
                    for field in counts:
                        counts[field] += child[field]
                else:
                    cell_counts[cell_type] = cell_counts.get(cell_type, 0) + 1
                    if "dff" in cell_type.lower():
                        counts["state"] += len(cell["connections"].get("Q", []))
                    if cell_type in ("$mul", "$add", "$sub"):
                        counts["arithmetic"] += 1
                    if cell_type.startswith("$mem"):
                        params = cell.get("parameters", {})
                        width = int(params.get("WIDTH", "0"), 2)
                        size = int(params.get("SIZE", "0"), 2)
                        counts["memory_bits"] += width * size
            return counts

        total = walk("billet_f4_top")
        for module in required:
            assert len(instances.get(module, [])) == 1, f"missing/duplicated substantive module: {module}"
            counts = instances[module][0]
            assert counts["state"] > 0, f"no connected state: {module}"
            assert counts["arithmetic"] > 0, f"no connected arithmetic: {module}"
        assert total["memory_bits"] >= 2048 * 16 + 257 * 32, "feature frame storage disappeared"
        ledger(evidence, "synthesis", configuration=name, parameters=parameters,
               hierarchy=instances, totals=total, cell_counts=cell_counts,
               forbidden_cells=0, netlist=evidence.relative(netlist))


def mutation_fixture(sim, defect):
    p = sim.program()
    expected, stream = [], "m"
    if defect == "fir-rounding":
        fir = [[16384] + [0] * 7 for _ in range(4)]
        sos = [[[65536, 0, 0, 0, 0]] for _ in range(4)]
        configure_legacy(p, fir, sos, 1)
        model = Legacy(fir, sos, 1)
        for n, value in enumerate([1, 3, -1, -3, 32767, -32768] * 4):
            p.send("s", value, n % 4)
            expected.append(pcm(model.sample(value, n % 4), n % 4))
    elif defect == "sos-feedback":
        fir, sos = legacy_banks(8, 1)
        configure_legacy(p, fir, sos, 2)
        model = Legacy(fir, sos, 2)
        for n, value in enumerate([16000] + [0] * 63):
            p.send("s", value, 0)
            expected.append(pcm(model.sample(value, 0), 0))
    elif defect == "src-phase":
        p.send("chain", 0, 0)
        p.send("feature", 1)
        banks = [[[65536] + [0] * 7, [32768] + [0] * 7] for _ in range(4)]
        configure_src(p, banks, 2, 1)
        model = Resampler(banks, 2, 1)
        for value in [12000, -10000, 8000, 7] * 8:
            p.send("s", value, 0)
            expected += [pcm(v, 0) for v in model.sample(value, 0)]
    elif defect == "mel-weight":
        stream = "t"
        p.send("feature", 3)
        powers = [0] * 257
        powers[15] = 1 << 30
        for k, value in enumerate(powers):
            p.send("p", value, 1, k, k == 256)
        expected = tagged([token(e) for e in mel(powers)], 1)
    else:
        stream = "log"
        energies = [(1 << 23) + (1 << 21), (1 << 25) + (1 << 20) + 73129,
                    (1 << 29) + (1 << 26) + 91919, 1937131, 0, 1 << 31]
        if defect == "stalled-token":
            p.set("m_ready", 0)
        for n, energy in enumerate(energies):
            p.send("energy", energy, n % 4, n, n == len(energies) - 1)
            expected.append((token(energy), n % 4, n, int(n == len(energies)-1)))
        if defect == "stalled-token":
            p.wait(8)
            p.check("m_valid", 1)
            p.wait(127)
            p.set("m_ready", 1)
    finish_program(p, stream, len(expected))
    return p, stream, expected


def mutation_matrix(evidence):
    import difflib
    mutations = [
        ("fir-rounding", "billet_f2_round_sat.sv",
         "assign rounded = floor_quotient + (round_up ? ONE : ZERO);",
         "assign rounded = floor_quotient;", "top"),
        ("sos-feedback", "billet_f3_sos_engine.sv",
         "acc_q <= sum_b - sum_a;", "acc_q <= sum_b + sum_a;", "top"),
        ("src-phase", "billet_f4_src.sv",
         "channel*8 + phase[channel])*SRC_TAPS + tap]",
         "channel*8 + 3'd0)*SRC_TAPS + tap]", "top"),
        ("mel-weight", "billet_f4_mel.sv",
         "if (read_valid) product_q <= power_q * weight;",
         "if (read_valid) product_q <= power_q * (weight >> 1);", "top"),
        ("pwl-interpolation", "billet_f4_log.sv",
         "interpolation_q <= difference * fraction_q[11:0];",
         "interpolation_q <= 0;", "log"),
        ("stalled-token", "billet_f4_log.sv",
         "end else if (enable) begin", "end else begin", "log"),
    ]
    params = {"TAP_COUNT": 8, "MAC_LANES": 4, "SOS_COUNT": 1, "SRC_TAPS": 8}
    originals = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (ROOT / "rtl").glob("*.sv")}
    baseline_top = Simulator(evidence, "top", params).build()
    baseline_log = Simulator(evidence, "log").build()
    killed = []
    for defect, file, before, after, kind in mutations:
        baseline = baseline_log if kind == "log" else baseline_top
        program, stream, expected = mutation_fixture(baseline, defect)
        actual = baseline.execute(program, f"baseline-{defect}")
        compare(actual, baseline, stream, expected)
        copied = evidence.path / unique(f"mutant-{defect}")
        copied.mkdir(exist_ok=False)
        for path in sorted((ROOT / "rtl").glob("*.sv")):
            shutil.copy2(path, copied / path.name)
        target = copied / file
        original = target.read_text()
        assert original.count(before) == 1, f"mutation target changed: {defect}"
        corrupted = original.replace(before, after, 1)
        target.write_text(corrupted)
        diff = evidence.path / f"{unique(defect)}.diff"
        diff.write_text("".join(difflib.unified_diff(original.splitlines(True), corrupted.splitlines(True),
                                                   fromfile=f"rtl/{file}", tofile=f"mutant/{file}")))
        mutant = Simulator(evidence, kind, params if kind == "top" else {}, copied).build()
        program, stream, expected = mutation_fixture(mutant, defect)
        rejection = None
        try:
            actual = mutant.execute(program, f"mutant-{defect}")
            compare(actual, mutant, stream, expected)
        except AssertionError as error:
            detail = str(error)
            # A compiled mutant counts only when a concrete numerical/protocol
            # assertion rejects it. Compilation, liveness and tool errors escape.
            if "NUMERICAL " in detail or "PROTOCOL stalled output changed" in detail:
                rejection = detail
            else:
                raise
        assert rejection is not None, f"undetected compiled mutant: {defect}"
        killed.append(defect)
        ledger(evidence, "sensitivity", defect=defect, compiled=True,
               passing_baseline_comparisons=len(expected), kill=rejection,
               mutation_diff=evidence.relative(diff),
               mutant_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    assert len(killed) == 6
    assert originals == {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (ROOT / "rtl").glob("*.sv")}, "accepted RTL changed during sensitivity"


def clean_reproduction(evidence):
    copied = evidence.path / unique("reproduction-root")
    copied.mkdir(exist_ok=False)
    originals = {}
    for path in source_files():
        relative = path.relative_to(ROOT)
        target = copied / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        originals[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == originals[str(relative)]
    # No original-import path, prebuilt executable, evidence, or runtime state.
    assert sorted(str(p.relative_to(copied)) for p in copied.rglob("*") if p.is_file()) == sorted(originals)
    # The command has a project-relative cwd and a fresh interpreter namespace.
    # Store separate child logs/receipt without relying on the original tree.
    import os
    import subprocess
    import time
    from simulator import now
    out = evidence.path / f"{unique('reproduction')}.stdout.txt"
    err = out.with_name(out.name.replace("stdout", "stderr"))
    entry = {"argv": ["python3", "tests/verify.py", "--suite", "portable-smoke"],
             "cwd": evidence.relative(copied), "start": now(), "timeout_s": 3600,
             "timed_out": False, "stdout": evidence.relative(out), "stderr": evidence.relative(err)}
    evidence.receipt["commands"].append(entry)
    evidence.write()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with out.open("wb") as stdout, err.open("wb") as stderr:
        process = subprocess.Popen(entry["argv"], cwd=copied, env=environment, stdout=stdout, stderr=stderr)
        start, heartbeat = time.monotonic(), time.monotonic()
        while process.poll() is None:
            if time.monotonic() - heartbeat >= 30:
                print("progress: fresh-source portable-smoke running", flush=True)
                heartbeat = time.monotonic()
            if time.monotonic() - start >= 3600:
                entry["timed_out"] = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                break
            time.sleep(0.1)
        entry["exit"], entry["end"] = process.wait(), now()
    evidence.write()
    assert entry["exit"] == 0 and not entry["timed_out"], (
        f"fresh reproduction failed: {entry['stdout']} {entry['stderr']}")
    receipts = list((copied / "evidence").glob("portable-smoke-*/receipt.json"))
    assert len(receipts) == 1
    child = json.loads(receipts[0].read_text())
    assert child["exit"] == 0 and child["source_sha256"] == originals
    assert any(r.get("token_comparisons") == 64 for r in child["results"])
    ledger(evidence, "fresh-source-reproduction", source_sha256=originals,
           child_receipt=evidence.relative(receipts[0]), prebuilt_files=0,
           complete_frame_token_comparisons=64)


def synthesis_probe(evidence):
    synthesis_matrix(evidence, only="minimum")


def integration_probe(evidence):
    icarus_arithmetic(evidence)
    sim = Simulator(evidence, "legacy", {"TAP_COUNT": 1, "MAC_LANES": 4, "SOS_COUNT": 1}).build()
    fir, sos = legacy_banks(1, 1)
    for mode in range(5):
        p, expected = sim.program(), []
        configure_legacy(p, fir, sos, mode)
        model = Legacy(fir, sos, mode)
        for n, value in enumerate(sample_patterns(16, 11)):
            p.send("s", value, n % 4)
            p.wait(32)
            expected.append(pcm(model.sample(value, n % 4), n % 4))
        finish_program(p, "m", len(expected))
        records = sim.execute(p, f"probe-legacy-mode{mode}")
        compare(records, sim, "m", expected)
        assert_latencies(records, sim, "s", "m", (0, 5, 8, 14, 14)[mode])
    src = Simulator(evidence, "src", {"SRC_TAPS": 8}).build()
    records, _, _ = ratio_case(src, 8, 2, 3, 29, 32, False)
    assert_src_scheduler(records, src, 8, 2, 3)
    complete_frame_smoke(evidence)
    spectrum_cycles(evidence, Simulator(evidence, "spectrum").build())
    mel_cycles(evidence, Simulator(evidence, "mel").build())


def src_pattern_cases(evidence, sim, taps):
    rng = random.Random(29)
    patterns = {
        "impulse": [32767] + [0] * 255,
        "dc": [12000] * 256,
        "positive-full-scale": [32767] * 256,
        "negative-full-scale": [-32768] * 256,
        "alternating": [32767 if n & 1 else -32768 for n in range(256)],
        "random": [rng.randint(-32768, 32767) for _ in range(256)],
    }
    for name, samples in patterns.items():
        banks = src_banks(taps, 2, 29)
        p, expected = sim.program(), []
        configure_src(p, banks, 2, 3, False)
        p.random(29)
        model = Resampler(banks, 2, 3)
        for value in samples:
            for ch in range(4):
                p.send("s", value, ch)
                expected += [pcm(v, ch) for v in model.sample(value, ch)]
        finish_program(p, "m", len(expected))
        records = sim.execute(p, f"src-pattern-{taps}-{name}")
        compare(records, sim, "m", expected)
        ledger(evidence, "src-pattern", taps=taps, pattern=name, l=2, m=3,
               samples_per_channel=256, comparisons=len(expected))
    banks = [[[131071] + [0] * (taps-1), [32768] + [0] * (taps-1)] for _ in range(4)]
    p, expected = sim.program(), []
    configure_src(p, banks, 2, 1, False)
    model = Resampler(banks, 2, 1)
    for value in (1, 3, -1, -3, 32767, -32768):
        p.send("s", value, 0)
        expected += [pcm(v, 0) for v in model.sample(value, 0)]
    finish_program(p, "m", len(expected))
    records = sim.execute(p, f"src-ties-saturation-{taps}")
    compare(records, sim, "m", expected)
    assert {0, 2, 65534, 32767, 32768}.issubset({v[0] for v in expected})
    ledger(evidence, "src-ties-saturation", taps=taps, comparisons=len(expected),
           positive_saturation=True, negative_saturation=True, positive_negative_ties=True)


def src_reconfiguration(evidence):
    sim = Simulator(evidence, "src", {"SRC_TAPS": 8}).build()
    p, expected = sim.program(), []
    banks = src_banks(8, 2, 29)
    configure_src(p, banks, 2, 3, False)
    models = [Resampler(banks, 2, 3) for _ in range(4)]
    for n, value in enumerate(sample_patterns(128, 11)):
        for ch in range(4):
            p.send("s", value + ch if -32768 <= value + ch <= 32767 else value, ch)
            actual_value = value + ch if -32768 <= value + ch <= 32767 else value
            expected += [pcm(v, ch) for v in models[ch].sample(actual_value, ch)]
    p.drain("m", len(expected))
    p.wait(32)
    next_banks = src_banks(8, 3, 47)
    # This write overlaps a held output; current work must use old coefficients.
    p.set("m_ready", 0)
    # Anchor128 is skipped at 2/3, while anchor129 emits and reserves the output.
    for value in (17000, 17001):
        p.send("s", value, 1)
        expected += [pcm(v, 1) for v in models[1].sample(value, 1)]
    p.wait(16)
    p.check("m_valid", 1)
    for phase in range(3):
        for tap in range(7):
            p.send("cfg", 0, phase, tap, next_banks[0][phase][tap])
    p.set("m_ready", 1)
    p.drain("m", len(expected))
    p.wait(32)
    reject_descriptor(p, "commit_ready", "commit_valid",
                      {"commit_channel": 0, "ratio_l": 3, "ratio_m": 2})
    for phase in range(3):
        p.send("cfg", 0, phase, 7, next_banks[0][phase][7])
    p.send("commit", 0, 3, 2)
    models[0] = Resampler(next_banks, 3, 2)
    p.random(47)
    for value in sample_patterns(96, 47):
        for ch in range(4):
            p.send("s", value, ch)
            expected += [pcm(v, ch) for v in models[ch].sample(value, ch)]
    finish_program(p, "m", len(expected))
    records = sim.execute(p, "src-live-bank-reconfiguration-isolation")
    compare(records, sim, "m", expected)
    ledger(evidence, "src-reconfiguration", comparisons=len(expected), reconfigured_channel=0,
           old_ratio=[2, 3], new_ratio=[3, 2], other_channels_history_retained=3)


def legacy_arithmetic_extremes(evidence):
    sim = Simulator(evidence, "legacy", {"TAP_COUNT": 1, "MAC_LANES": 4, "SOS_COUNT": 4}).build()
    p, expected = sim.program(), []
    fir = [[16384] for _ in range(4)]
    sos = [[[32768, 0, 0, 0, 0]] + [[65536, 0, 0, 0, 0]] * 3 for _ in range(4)]
    configure_legacy(p, fir, sos, 2, 1)
    model = Legacy(fir, sos, 2, 1)
    for ch in range(4):
        for value in (1, 3, -1, -3, 32767, -32768):
            p.send("s", value, ch)
            expected.append(pcm(model.sample(value, ch), ch))
    p.drain("m", len(expected))
    state_saturations = 0
    for ch in (0, 1):
        nonlinear = [65536, 0, 0, -131072, 0]
        for section in range(4):
            coefficients = nonlinear if section == 0 else [65536, 0, 0, 0, 0]
            for addr, value in enumerate(coefficients):
                p.send("sos_cfg", ch, section, addr, value)
            model.sos[ch][section] = coefficients
        p.send("sos_commit", ch)
        model.clear_sos(ch)
    for n in range(32):
        for ch in (0, 1):
            value = 20000 if ch == 0 else -20000
            p.send("s", value, ch)
            expected.append(pcm(model.sample(value, ch), ch))
            if model.state[ch][0][2] in (-(1 << 31), (1 << 31)-1):
                state_saturations += 1
    assert state_saturations > 0
    assert 32767 in {v[0] for v in expected} and 32768 in {v[0] for v in expected}
    p.drain("m", len(expected))
    p.send("s", 919, 2)
    p.wait(3)
    p.check("m_valid", 0)
    p.reset()
    p.send("chain", 0, 0)
    p.send("s", -77, 2)
    expected.append(pcm(-77, 2))
    finish_program(p, "m", len(expected))
    records = sim.execute(p, "sos-rne-ties-state-output-saturation")
    compare(records, sim, "m", expected)
    ledger(evidence, "sos-arithmetic-extremes", comparisons=len(expected),
           signed32_state_saturations=state_saturations, signed16_output_saturation=True,
           positive_negative_final_rounding_ties=True, aborted_busy_sos_samples=1)


def datapath_probe(evidence):
    """One default-geometry filtered PCM frame, not the full acceptance matrix."""
    top = Simulator(evidence, "top").build()
    parent = Simulator(evidence, "parent").build()
    legacy = Simulator(evidence, "legacy").build()
    fir, sos = legacy_banks()
    source = audio_frame(4, 768, 24000, 29)
    model = Legacy(fir, sos, 3)
    filtered = [model.sample(value, 2) for value in source]
    expected_pcm = [pcm(value, 2) for value in filtered]
    for sim in (parent, legacy):
        p = sim.program()
        configure_legacy(p, fir, sos, 3)
        for value in source:
            p.send("s", value, 2)
        finish_program(p, "m", len(filtered))
        records = sim.execute(p, f"probe-default-{sim.kind}")
        compare(records, sim, "m", expected_pcm)
        if sim.kind == "legacy":
            assert_latencies(records, sim, "s", "m", 32 + 14 + 6)
    p = top.program()
    configure_legacy(p, fir, sos, 3)
    p.send("feature", 2)
    banks = [[lowpass(32, 0.25), lowpass(32, 0.24)] for _ in range(4)]
    configure_src(p, banks, 2, 3)
    src_model = Resampler(banks, 2, 3)
    converted = [output for value in filtered for output in src_model.sample(value, 2)]
    assert len(converted) == 512
    for value in source:
        p.send("s", value, 2)
    p.stall("t", 127)
    finish_program(p, "t", 64)
    records = top.execute(p, "probe-default-filtered-src-tokens")
    compare(records, top, "t", tagged([token(e) for e in mel(spectrum(converted))], 2))
    src_reconfiguration(evidence)
    leaf = Simulator(evidence, "src", {"SRC_TAPS": 1}).build()
    src_pattern_cases(evidence, leaf, 1)
    ledger(evidence, "default-datapath-probe", parent_comparisons=768,
           retimed_legacy_comparisons=768, token_comparisons=64,
           chain_mode=3, l=2, m=3, converted_samples=512)


def mode_history_preservation(evidence, sim):
    p, expected = sim.program(), []
    fir, sos = legacy_banks(8, 4)
    configure_legacy(p, fir, sos, 3, 4)
    model = Legacy(fir, sos, 3, 4)
    for value in sample_patterns(32, 11):
        for ch in range(4):
            p.send("s", value, ch)
            expected.append(pcm(model.sample(value, ch), ch))
    p.drain("m", len(expected))
    p.send("feature", 1)
    for value in sample_patterns(16, 29):
        for ch in range(4):
            p.send("s", value, ch)
            expected.append(pcm(model.sample(value, ch), ch))
    p.drain("m", len(expected))
    p.send("feature", 2)
    for value in sample_patterns(16, 47):
        for ch in range(4):
            p.send("s", value, ch)
            model.sample(value, ch)
    p.send("feature", 0)
    for value in sample_patterns(16, 11):
        for ch in range(4):
            p.send("s", value, ch)
            expected.append(pcm(model.sample(value, ch), ch))
    finish_program(p, "m", len(expected))
    records = sim.execute(p, "mode-change-preserves-fir-sos-history-coefficients")
    compare(records, sim, "m", expected)
    assert not output_records(records, sim, "t")
    ledger(evidence, "mode-history-preservation", pcm_comparisons=len(expected),
           chain_mode=3, sections=4, explicitly_discarded_feature_samples_per_channel=16)


def channel_frame_clear(evidence):
    sim = Simulator(evidence, "top", {"TAP_COUNT": 1, "MAC_LANES": 4,
                                    "SOS_COUNT": 1, "SRC_TAPS": 1}).build()
    p, expected = sim.program(), []
    p.send("chain", 0, 0)
    p.send("feature", 2)
    preserved = audio_frame(4, 512, 16000, 29)
    for n in range(256):
        p.send("s", 12000, 0)
        p.send("s", preserved[n], 1)
    p.wait(32)
    p.send("src_cfg", 0, 0, 0, 65536)
    p.send("src_commit", 0, 1, 1)
    for value in preserved[256:]:
        p.send("s", value, 1)
    expected += tagged([token(e) for e in mel(spectrum(preserved))], 1)
    p.drain("t", len(expected))
    fresh = audio_frame(5, 512, 16000, 47)
    for value in fresh:
        p.send("s", value, 0)
    expected += tagged([token(e) for e in mel(spectrum(fresh))], 0)
    p.drain("t", len(expected))
    p.send("feature", 3)
    powers = [((k + 1) * 131071) & 0xffffffff for k in range(257)]
    # Commit on another channel retains the partial spectral frame.
    for k in range(128):
        p.send("p", powers[k], 1, k, 0)
    p.send("src_cfg", 0, 0, 0, 65536)
    p.send("src_commit", 0, 1, 1)
    for k in range(128, 257):
        p.send("p", powers[k], 1, k, k == 256)
    expected += tagged([token(e) for e in mel(powers)], 1)
    p.drain("t", len(expected))
    # Matching-channel commit discards just that partial frame without error.
    for k in range(128):
        p.send("p", powers[k], 0, k, 0)
    p.send("src_cfg", 0, 0, 0, 65536)
    p.send("src_commit", 0, 1, 1)
    p.check("feature_error", 0)
    for k in range(257):
        p.send("p", powers[k], 0, k, k == 256)
    expected += tagged([token(e) for e in mel(powers)], 0)
    p.drain("t", len(expected))
    p.send("p", 3, 0, 2, 0)
    p.check("feature_error", 1)
    p.send("src_cfg", 0, 0, 0, 65536)
    p.send("src_commit", 0, 1, 1)
    p.check("feature_error", 1)
    # This entire well-formed-looking frame remains in discard-until-last.
    for k in range(257):
        p.send("p", powers[k], 0, k, k == 256)
    p.wait(512)
    for k in range(257):
        p.send("p", powers[k], 2, k, k == 256)
    expected += tagged([token(e) for e in mel(powers)], 2)
    finish_program(p, "t", len(expected))
    records = sim.execute(p, "src-commit-partial-frame-channel-isolation")
    compare(records, sim, "t", expected)
    assert not output_records(records, sim, "m")
    ledger(evidence, "src-commit-frame-isolation", token_comparisons=len(expected),
           preserved_partial_pcm_samples=256, discarded_partial_pcm_samples=256,
           preserved_partial_spectral_bins=128, discarded_partial_spectral_bins=128,
           retained_error_discard_frames=1)
