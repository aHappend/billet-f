"""Real generic Yosys synthesis with retained memories and structural assertions."""

import gzip
import hashlib
import json
from collections import Counter


def synthesize(evidence, name, parameters):
    work=evidence.path/name
    work.mkdir(exist_ok=False)
    netlist=work/"netlist.json"
    script=["read_verilog -sv "+" ".join(
        p.relative_to(evidence.root).as_posix()
        for p in sorted((evidence.root/"rtl").glob("*.sv")))]
    if parameters:
        script += ["chparam "+" ".join(f"-set {k} {v}" for k,v in parameters.items())+
                   " billet_f5_top"]
    script += ["hierarchy -check -top billet_f5_top",
               "proc -norom",
               "synth -top billet_f5_top -run begin:fine",
               "opt_clean", "check -assert", "stat",
               "select -assert-none t:$dlatch t:$adlatch t:$dlatchsr "
               "t:$div t:$mod t:$divfloor t:$modfloor",
               "write_json "+evidence.relative(netlist)]
    script_path=work/"synthesis.ys"
    script_path.write_text(";\n".join(script)+";\n")
    evidence.artifact(script_path)
    evidence.run(["yosys","-s",evidence.relative(script_path)],name+"-synthesis")
    if not netlist.is_file():
        raise AssertionError(f"{name}: missing synthesized structure")
    data=json.loads(netlist.read_text())
    modules=data["modules"]
    top=modules.get("billet_f5_top")
    if not top:
        raise AssertionError("synthesized top missing")
    required=["u_legacy","u_beamformer","u_aec","u_audio_fork",
              "u_acoustic_spectrum","u_vad","u_acoustic_mel","u_acoustic_log"]
    instances={cell_name.split(".")[-1]:cell for cell_name,cell in top["cells"].items()}
    if any(instance not in instances for instance in required):
        raise AssertionError("required functional hierarchy optimized away")
    primitive_counts=Counter()
    memory_bits=0
    register_bits=0
    seen=set()
    def visit(module_name):
        nonlocal memory_bits,register_bits
        module=modules[module_name]
        if int(module.get("attributes",{}).get("blackbox","0"),2):
            raise AssertionError("unresolved blackbox "+module_name)
        seen.add(module_name)
        for cell in module["cells"].values():
            kind=cell["type"]
            if kind in modules:
                visit(kind)
            elif kind.startswith("$"):
                primitive_counts[kind]+=1
                if "latch" in kind or kind in {"$div","$mod","$divfloor","$modfloor"}:
                    raise AssertionError("forbidden synthesis primitive "+kind)
                if kind.startswith("$mem"):
                    params=cell["parameters"]
                    memory_bits+=int(params["WIDTH"],2)*int(params["SIZE"],2)
                if "dff" in kind:
                    register_bits+=int(cell["parameters"]["WIDTH"],2)
            else:
                raise AssertionError("unresolved cell "+kind)
    visit("billet_f5_top")
    if sum(primitive_counts.values())<100 or register_bits<1000 or memory_bits<32768:
        raise AssertionError("non-substantive synthesized datapath or missing frame storage")
    for instance in required:
        module=modules[instances[instance]["type"]]
        if len(module["cells"])<2:
            raise AssertionError("constant/empty functional engine "+instance)
    aec_module=modules[instances["u_aec"]["type"]]
    if not any(cell_name.endswith("u_divider") for cell_name in aec_module["cells"]):
        raise AssertionError("serial NLMS divider missing after synthesis")
    if not any("weights" in wire_name for wire_name in aec_module["netnames"]):
        raise AssertionError("adaptive weight state missing after synthesis")
    original_hash=hashlib.sha256(netlist.read_bytes()).hexdigest()
    original_bytes=netlist.stat().st_size
    compressed=netlist.with_suffix(".json.gz")
    with netlist.open("rb") as source, gzip.open(compressed,"wb",compresslevel=6) as target:
        while chunk:=source.read(1<<20):
            target.write(chunk)
    with gzip.open(compressed,"rb") as source:
        digest=hashlib.sha256()
        byte_count=0
        while chunk:=source.read(1<<20):
            digest.update(chunk); byte_count+=len(chunk)
    if digest.hexdigest()!=original_hash or byte_count!=original_bytes:
        raise AssertionError("synthesis lossless decompression failed")
    evidence.artifact(compressed)
    netlist.unlink()
    metrics={"parameters":parameters,"primitive_cells":dict(primitive_counts),
             "register_bits":register_bits,"memory_bits":memory_bits,
             "reachable_module_types":len(seen),"zero_latches":True,
             "zero_division_modulo":True,"zero_unresolved_blackboxes":True,
             "uncompressed_sha256":original_hash,"uncompressed_bytes":original_bytes,
             "netlist":evidence.relative(compressed),
             "scope":"generic memory-preserving synthesis, not Arria post-fit PPA"}
    evidence.record["metrics"][name]=metrics
    evidence.compared(name,sum(primitive_counts.values()),kind="structural")
    return metrics


def run_synthesis(evidence):
    evidence.versions([("yosys","-V")])
    for name,parameters in [
            ("default",{}),
            ("singleton",{"DELAY_DEPTH":1,"AEC_TAPS":1}),
            ("maximum",{"DELAY_DEPTH":32,"AEC_TAPS":32})]:
        synthesize(evidence,name,parameters)
