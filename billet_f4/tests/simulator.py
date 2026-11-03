"""Portable compiled public-pin transactor and source-bound execution receipts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
RECORD = struct.Struct("<QIQIII")


def now():
    return datetime.now(timezone.utc).isoformat()


def unique(stem):
    return f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def source_files(root=ROOT):
    return sorted(p for pattern in ("rtl/*.sv", "design/*.md", "tests/*.py",
                                    "billet_f3_reference/rtl/*.sv")
                  for p in root.glob(pattern))


class Evidence:
    def __init__(self, suite, preparation=False, repair_of=None):
        base = ROOT / "evidence"
        if preparation:
            base /= "preparation"
        self.path = base / unique(suite)
        self.path.mkdir(parents=True, exist_ok=False)
        self.receipt = {
            "argv": ["python3", "tests/verify.py", "--suite", suite],
            "cwd": ".", "start": now(), "end": None, "exit": None,
            "timeout_s": None, "execution_budget_s": 2700, "timed_out": False,
            "stdout": self.relative(self.path / "stdout.txt"),
            "stderr": self.relative(self.path / "stderr.txt"),
            "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in source_files()},
            "commands": [], "tools": {}, "results": [],
        }
        if repair_of:
            self.receipt["repair_of"] = repair_of
        self.write()

    def relative(self, path):
        return str(Path(path).relative_to(ROOT))

    def write(self):
        (self.path / "receipt.json").write_text(json.dumps(self.receipt, indent=2) + "\n")

    def tool(self, tool):
        if tool in self.receipt["tools"]:
            return
        if not shutil.which(tool):
            raise RuntimeError(f"required installed tool missing: {tool}")
        args = {"iverilog": ["-V"], "vvp": ["-V"], "verilator": ["--version"],
                "yosys": ["-V"], "python3": ["--version"]}[tool]
        completed = self.command([tool, *args], f"version-{tool}", timeout=3600)
        self.receipt["tools"][tool] = completed
        self.write()

    def command(self, argv, name, timeout=3600, allowed=(0,)):
        name = unique(name)
        stdout, stderr = self.path / f"{name}.stdout.txt", self.path / f"{name}.stderr.txt"
        entry = {"argv": list(map(str, argv)), "cwd": ".", "start": now(),
                 "timeout_s": timeout, "timed_out": False,
                 "stdout": self.relative(stdout), "stderr": self.relative(stderr)}
        self.receipt["commands"].append(entry)
        self.write()
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=out, stderr=err)
            started, heartbeat = time.monotonic(), time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic() - heartbeat >= 30:
                        print(f"progress: {name} running", flush=True)
                        heartbeat = time.monotonic()
                    if time.monotonic() - started > timeout:
                        entry["timed_out"] = True
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        break
                    time.sleep(0.1)
            finally:
                entry["exit"] = process.wait()
                entry["end"] = now()
                self.write()
        if entry["timed_out"] or entry["exit"] not in allowed:
            detail = stderr.read_text(errors="replace")[-3000:] + stdout.read_text(errors="replace")[-3000:]
            raise RuntimeError(f"tool failure: {entry['argv']} exit={entry['exit']} "
                               f"timeout={entry['timed_out']}; {detail}")
        return entry

    def finish(self, error=None):
        self.receipt.update(end=now(), exit=int(error is not None))
        if error is not None:
            self.receipt["error"] = str(error)
        self.write()


# (valid, ready, payload names); payload tuples are data/channel/index/last.
INPUT_STREAMS = {
    "s": ("s_valid", "s_ready", ["s_data", "s_channel"]),
    "cfg": ("cfg_valid", "cfg_ready", ["cfg_channel", "cfg_addr", "cfg_data"]),
    "commit": ("commit_valid", "commit_ready", ["commit_channel"]),
    "sos_cfg": ("sos_cfg_valid", "sos_cfg_ready",
                ["sos_cfg_channel", "sos_cfg_section", "sos_cfg_addr", "sos_cfg_data"]),
    "sos_commit": ("sos_commit_valid", "sos_commit_ready", ["sos_commit_channel"]),
    "chain": ("chain_valid", "chain_ready", ["chain_mode", "chain_sections"]),
    "feature": ("feature_valid", "feature_ready", ["feature_mode"]),
    "src_cfg": ("src_cfg_valid", "src_cfg_ready",
                ["src_cfg_channel", "src_cfg_phase", "src_cfg_tap", "src_cfg_data"]),
    "src_commit": ("src_commit_valid", "src_commit_ready",
                   ["src_commit_channel", "src_l", "src_m"]),
    "p": ("p_valid", "p_ready", ["p_power", "p_channel", "p_bin", "p_last"]),
    "energy": ("s_valid", "s_ready", ["s_energy", "s_channel", "s_band", "s_last"]),
}
OUTPUT_STREAMS = {
    "m": ("m_valid", "m_ready", ["m_data", "m_channel", None, None]),
    "t": ("t_valid", "t_ready", ["t_data", "t_channel", "t_band", "t_last"]),
    "p": ("p_valid", "p_ready", ["p_power", "p_channel", "p_bin", "p_last"]),
    "e": ("e_valid", "e_ready", ["e_energy", "e_channel", "e_band", "e_last"]),
    "log": ("m_valid", "m_ready", ["m_data", "m_channel", "m_band", "m_last"]),
}


def interface(kind, params=None):
    params = params or {}
    taps, src_taps = params.get("TAP_COUNT", 32), params.get("SRC_TAPS", 32)
    aw, saw = max(1, (max(1, taps) - 1).bit_length()), max(1, (max(1, src_taps) - 1).bit_length())
    widths = {"clk": 1, "rst_n": 1, "s_data": 16, "s_channel": 2,
              "s_valid": 1, "m_ready": 1}
    inputs, outputs = {}, {}
    if kind in ("top", "legacy", "parent"):
        inputs.update({k: INPUT_STREAMS[k] for k in ("s", "cfg", "commit", "sos_cfg",
                                                    "sos_commit", "chain")})
        widths.update(cfg_valid=1, cfg_channel=2, cfg_addr=aw, cfg_data=16,
                      commit_valid=1, commit_channel=2, sos_cfg_valid=1,
                      sos_cfg_channel=2, sos_cfg_section=2, sos_cfg_addr=3,
                      sos_cfg_data=18, sos_commit_valid=1, sos_commit_channel=2,
                      chain_valid=1, chain_mode=3, chain_sections=3)
        outputs["m"] = OUTPUT_STREAMS["m"]
        module = "billet_f4_top" if kind == "top" else "billet_f3_top"
        if kind == "top":
            inputs.update({k: INPUT_STREAMS[k] for k in ("feature", "src_cfg", "src_commit", "p")})
            widths.update(feature_valid=1, feature_mode=2, src_cfg_valid=1,
                          src_cfg_channel=2, src_cfg_phase=3, src_cfg_tap=saw,
                          src_cfg_data=18, src_commit_valid=1, src_commit_channel=2,
                          src_l=4, src_m=4, p_valid=1, p_power=32, p_channel=2,
                          p_bin=9, p_last=1, t_ready=1)
            outputs["t"] = OUTPUT_STREAMS["t"]
    elif kind == "src":
        inputs = {k: INPUT_STREAMS[k] for k in ("s", "cfg", "commit")}
        inputs["cfg"] = ("cfg_valid", "cfg_ready", ["cfg_channel", "cfg_phase", "cfg_tap", "cfg_data"])
        inputs["commit"] = ("commit_valid", "commit_ready", ["commit_channel", "ratio_l", "ratio_m"])
        widths.update(clear_all=1, cfg_valid=1, cfg_channel=2, cfg_phase=3,
                      cfg_tap=saw, cfg_data=18, commit_valid=1, commit_channel=2,
                      ratio_l=4, ratio_m=4)
        outputs["m"], module = OUTPUT_STREAMS["m"], "billet_f4_src"
    elif kind == "spectrum":
        inputs = {"s": INPUT_STREAMS["s"]}
        widths.pop("m_ready")
        widths.update(clear_all=1, clear_channel_valid=1, clear_channel=2, p_ready=1)
        outputs["p"], module = OUTPUT_STREAMS["p"], "billet_f4_spectrum"
    elif kind == "mel":
        widths = {"clk": 1, "rst_n": 1, "clear_all": 1, "clear_channel_valid": 1,
                  "clear_channel": 2, "p_valid": 1, "p_power": 32, "p_channel": 2,
                  "p_bin": 9, "p_last": 1, "e_ready": 1}
        inputs = {"p": INPUT_STREAMS["p"]}
        outputs["e"], module = OUTPUT_STREAMS["e"], "billet_f4_mel"
    elif kind == "log":
        widths = {"clk": 1, "rst_n": 1, "s_valid": 1, "s_energy": 48,
                  "s_channel": 2, "s_band": 6, "s_last": 1, "m_ready": 1}
        inputs = {"energy": INPUT_STREAMS["energy"]}
        outputs["log"], module = OUTPUT_STREAMS["log"], "billet_f4_log"
    else:
        raise ValueError(kind)
    return module, widths, inputs, outputs


def driver_source(module, widths, inputs, outputs):
    names = list(widths)
    names += [r for _, r, _ in inputs.values()]
    names += [v for v, _, _ in outputs.values()]
    names += [p for _, _, ps in outputs.values() for p in ps if p]
    names = list(dict.fromkeys(names))
    if module in ("billet_f4_top", "billet_f4_mel"):
        names += ["feature_error"]
    ids = {name: i for i, name in enumerate(names)}
    setters = "\n".join(f"case {ids[n]}: d.{n}=v & {((1 << w)-1)}ULL; break;"
                        for n, w in widths.items())
    getters = "\n".join(f"case {i}: return d.{n};" for n, i in ids.items())
    transfer = []
    acceptcode = []
    for i, (v, r, ps) in enumerate(inputs.values()):
        transfer.append(f"case {i}: valid={ids[v]}; ready={ids[r]}; payload={{{','.join(str(ids[p]) for p in ps)}}}; break;")
        values = [f"uint64_t(d.{p})" for p in ps[:4]]
        values += ["0"] * (4 - len(values))
        acceptcode.append(f"accepted[{i}]=d.{v} && d.{r};"
                          f"if(accepted[{i}])record({101+i},std::array<uint64_t,4>{{{','.join(values)}}});")
    checks = []
    publications = []
    resetchecks = []
    for i, (v, r, ps) in enumerate(outputs.values()):
        values = ",".join(f"uint64_t(d.{p})" if p else "0" for p in ps)
        checks.append(f"""
        {{
            std::array<uint64_t,4> p={{{values}}};
            bool valid=d.{v};
            if (held[{i}] && (!valid || p!=held_payload[{i}])) fail("PROTOCOL stalled output changed");
            if (!valid && p!=std::array<uint64_t,4>{{0,0,0,0}}) fail("PROTOCOL invalid payload nonzero");
            retired[{i}]=valid && d.{r};
            if(retired[{i}]) {{ record({i+1},p); counts[{i}]++; }}
            held[{i}]=valid && !d.{r};
            held_payload[{i}]=p;
        }}""")
        publications.append(f"""
        if(d.{v} && (!prevalid[{i}] || retired[{i}]))
            record({i+21},std::array<uint64_t,4>{{{values}}});
        prevalid[{i}]=d.{v};
        """)
        resetchecks += [v] + [p for p in ps if p]
    resetchecks += [r for _, r, _ in inputs.values()]
    if "feature_error" in ids:
        resetchecks += ["feature_error"]
    resetcode = " || ".join(f"d.{n}!=0" for n in resetchecks)
    source = r'''
#include "V@MODULE@.h"
#include "verilated.h"
#include <array>
#include <vector>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <sstream>
#include <random>
#include <cstdint>
V@MODULE@ d;
std::ofstream trace;
uint64_t cycle=0;
std::array<uint64_t,@NOUT@> counts{};
std::array<bool,@NOUT@> held{},prevalid{},retired{};
std::array<bool,@NIN@> accepted{};
std::array<std::array<uint64_t,4>,@NOUT@> held_payload{};
std::mt19937 randomizer;
bool random_ready=false;
uint64_t commands=0;
void fail(const char* msg) {
    throw std::runtime_error(std::string(msg)+" cycle="+std::to_string(cycle)+" command="+std::to_string(commands));
}
void record(uint32_t kind,const std::array<uint64_t,4>& p) {
    uint32_t ch=p[1],ix=p[2],last=p[3];
    trace.write((char*)&cycle,8); trace.write((char*)&kind,4); trace.write((char*)&p[0],8);
    trace.write((char*)&ch,4);trace.write((char*)&ix,4);trace.write((char*)&last,4);
}
void set(int id,uint64_t v) { switch(id) { @SETTERS@ default: fail("invalid setter"); } }
uint64_t get(int id) { switch(id) { @GETTERS@ default: fail("invalid getter"); } return 0; }
void reset_check() { if(@RESET@) fail("RESET public output nonzero"); }
void tick() {
    d.clk=0; d.eval();
    if(random_ready && d.rst_n) { @RANDOM_READY@ d.eval(); }
    if(!d.rst_n) {
        reset_check(); held.fill(false); prevalid.fill(false);
    } else { @ACCEPT@ @CHECKS@ }
    d.clk=1; d.eval();
    if(!d.rst_n) reset_check();
    else { @PUBLICATIONS@ }
    d.clk=0; d.eval(); if(!d.rst_n) reset_check();
    cycle++;
    if(Verilated::gotFinish()) fail("unexpected finish");
}
int main(int argc,char**argv) {
    Verilated::commandArgs(argc,argv);
    if(argc!=3) return 2;
    std::ifstream commands_file(argv[1]);trace.open(argv[2],std::ios::binary);
    if(!commands_file || !trace) return 2;
    try {
        @INITIAL@
        d.clk=0; d.rst_n=1;d.eval();d.rst_n=0;d.eval();tick();tick();d.rst_n=1;d.eval();
        int op;
        while(commands_file>>op) {
            commands++;
            if(op==0) {int id;uint64_t value;commands_file>>id>>value;set(id,value);d.eval();}
            else if(op==1) {uint64_t n;commands_file>>n;while(n--)tick();}
            else if(op==2) {
                int stream;uint64_t bound;commands_file>>stream>>bound;
                int valid=-1,ready=-1;std::vector<int> payload;
                switch(stream) { @TRANSFER@ default: fail("invalid transfer"); }
                for(auto id:payload) {uint64_t value;commands_file>>value;set(id,value);}
                set(valid,1);d.eval();uint64_t waited=0;
                while(true) {
                    tick();
                    if(accepted[stream]) break;
                    if(++waited>bound)fail("LIVENESS transfer bound");
                }
                set(valid,0);d.eval();
            }
            else if(op==3) {
                int stream;uint64_t target,bound;commands_file>>stream>>target>>bound;
                uint64_t waited=0;while(counts[stream]<target) {
                    tick();if(++waited>bound)fail("LIVENESS drain bound");
                }
            }
            else if(op==4) {
                int id;uint64_t expected;commands_file>>id>>expected;d.eval();
                if(get(id)!=expected)fail("PUBLIC assertion");
            }
            else if(op==5) {
                uint32_t seed;commands_file>>seed;randomizer.seed(seed);random_ready=seed!=0;
            }
            else if(op==6) {
                d.rst_n=0;d.eval();held.fill(false);prevalid.fill(false);tick();tick();
                d.rst_n=1;d.eval();
            }
            else if(op==7) {
                int stream;uint64_t hold,bound;commands_file>>stream>>hold>>bound;
                bool saved=random_ready;random_ready=false;
                int valid=-1,ready=-1;
                switch(stream) { @STALL_SWITCH@ default:fail("invalid output");}
                set(ready,0);d.eval();uint64_t waited=0;
                while(!get(valid)){tick();if(++waited>bound)fail("LIVENESS stall precondition");}
                record(201+stream,std::array<uint64_t,4>{hold,0,0,0});
                while(hold--)tick();
                record(211+stream,std::array<uint64_t,4>{0,0,0,0});
                set(ready,1);d.eval();tick();random_ready=saved;
            }
            else fail("invalid command");
        }
        trace.close();d.final();
        std::cout<<"DONE cycles="<<cycle<<" commands="<<commands;
        for(auto n:counts)std::cout<<" compared_candidates="<<n;
        std::cout<<"\n";
    } catch(const std::exception& e) {
        std::cerr<<"ASSERTION "<<e.what()<<"\n";trace.close();return 1;
    }
    return 0;
}
'''
    replacements = {
        "@MODULE@": module, "@NOUT@": str(len(outputs)), "@SETTERS@": setters,
        "@NIN@": str(len(inputs)), "@ACCEPT@": "\n".join(acceptcode),
        "@GETTERS@": getters, "@RESET@": resetcode, "@CHECKS@": "\n".join(checks),
        "@PUBLICATIONS@": "\n".join(publications), "@TRANSFER@": "\n".join(transfer),
        "@RANDOM_READY@": "\n".join(f"d.{r}=(randomizer()%8)!=0;" for _, r, _ in outputs.values()),
        "@INITIAL@": "\n".join(f"d.{n}=0;" for n in widths if n not in ("clk", "rst_n")),
        "@STALL_SWITCH@": "\n".join(f"case {i}: valid={ids[v]};ready={ids[r]};break;"
                                    for i, (v, r, _) in enumerate(outputs.values())),
    }
    for key, value in replacements.items():
        source = source.replace(key, value)
    return source, ids


class Simulator:
    def __init__(self, evidence, kind="top", params=None, rtl=None):
        self.evidence, self.kind, self.params = evidence, kind, params or {}
        self.module, self.widths, self.inputs, self.outputs = interface(kind, self.params)
        self.source, self.ids = driver_source(self.module, self.widths, self.inputs, self.outputs)
        self.rtl = sorted((rtl or (ROOT / ("billet_f3_reference/rtl" if kind == "parent" else "rtl"))).glob("*.sv"))
        self.executable = None

    def build(self):
        self.evidence.tool("verilator")
        stem = unique(f"compiled-{self.kind}")
        driver = self.evidence.path / f"{stem}.cpp"
        driver.write_text(self.source)
        objects = self.evidence.path / unique("compile-obj")
        # Verilator invokes make in Mdir: use an absolute driver only in the live
        # tool invocation. Its persisted reproduction argv is rooted with cwd.
        argv = ["verilator", "--cc", "--exe", "--build", "-j", "2", "-Wno-fatal",
                "--unroll-count", "256",
                "--top-module", self.module, "--Mdir", self.evidence.relative(objects)]
        argv += [f"-G{k}={v}" for k, v in self.params.items()]
        argv += [self.evidence.relative(p) for p in self.rtl]
        # A project-relative make path reaches the driver from the object dir.
        import os
        argv += [os.path.relpath(driver, objects)]
        self.evidence.command(argv, f"compile-{self.kind}")
        self.executable = self.evidence.path / f"{stem}.bin"
        shutil.copy2(objects / f"V{self.module}", self.executable)
        shutil.rmtree(objects)
        return self

    def program(self):
        return Program(self)

    def execute(self, program, name):
        if self.executable is None:
            self.build()
        commands = self.evidence.path / f"{unique(name)}.commands.txt"
        commands.write_text("\n".join(program.commands) + "\n")
        trace = commands.with_suffix(".trace.bin")
        entry = self.evidence.command([self.evidence.relative(self.executable),
                                       self.evidence.relative(commands),
                                       self.evidence.relative(trace)],
                                      name, allowed=(0, 1))
        if entry["exit"]:
            raise AssertionError(f"simulation assertion: {entry['stderr']}: "
                                 f"{(ROOT / entry['stderr']).read_text()[-2000:]}")
        records = []
        with trace.open("rb") as f:
            while data := f.read(RECORD.size):
                if len(data) != RECORD.size:
                    raise AssertionError("truncated simulator record")
                records.append(RECORD.unpack(data))
        self.evidence.receipt["results"].append({
            "case": name, "trace": self.evidence.relative(trace),
            "commands": self.evidence.relative(commands), "records": len(records)})
        self.evidence.write()
        return records


class Program:
    def __init__(self, simulator):
        self.simulator, self.commands = simulator, []
        self.counts = {name: 0 for name in simulator.outputs}
        for _, ready, _ in simulator.outputs.values():
            self.set(ready, 1)

    def set(self, name, value):
        width = self.simulator.widths[name]
        self.commands.append(f"0 {self.simulator.ids[name]} {value & ((1 << width)-1)}")

    def wait(self, cycles):
        self.commands.append(f"1 {cycles}")

    def send(self, stream, *values, bound=2000000):
        index = list(self.simulator.inputs).index(stream)
        payload = self.simulator.inputs[stream][2]
        assert len(payload) == len(values)
        masked = [v & ((1 << self.simulator.widths[p])-1) for p, v in zip(payload, values)]
        self.commands.append(f"2 {index} {bound} " + " ".join(map(str, masked)))

    def drain(self, stream, total, bound=20000000):
        index = list(self.simulator.outputs).index(stream)
        self.commands.append(f"3 {index} {total} {bound}")
        self.counts[stream] = total

    def check(self, name, value):
        self.commands.append(f"4 {self.simulator.ids[name]} {value}")

    def random(self, seed):
        self.commands.append(f"5 {seed}")

    def reset(self):
        self.commands.append("6")

    def stall(self, stream, cycles):
        self.commands.append(f"7 {list(self.simulator.outputs).index(stream)} {cycles} 2000000")


def output_records(records, simulator, stream, publication=False):
    kind = list(simulator.outputs).index(stream) + (21 if publication else 1)
    return [r for r in records if r[1] == kind]


def input_records(records, simulator, stream):
    kind = list(simulator.inputs).index(stream) + 101
    return [r for r in records if r[1] == kind]


def compare(records, simulator, stream, expected):
    actual = output_records(records, simulator, stream)
    assert len(actual) == len(expected), f"{stream} count actual={len(actual)} expected={len(expected)}"
    for index, (record, want) in enumerate(zip(actual, expected)):
        got = record[2:]
        assert got == tuple(want), f"NUMERICAL {stream}[{index}] cycle={record[0]} actual={got} expected={want}"
    assert actual, f"zero {stream} comparisons"
    return len(actual)
