#!/usr/bin/env python3
"""BILLET-F1 independent, source-bound verification entry point.

Requirements map:
  behavior: every public pin, integer/floating convolution, protocol, timing,
            legal/illegal configurations, bank fixtures and frequency response.
  defects: unchanged black-box oracle rejects three compiled/executed RTL copies.
  synthesis: literal hierarchy, signed multiplier, connected state, no latches.
  reproduce: fresh declared-source copy, fresh T=1/32 subset, defects, default synth.

The shared runtime executes the four admitted commands. No final report is an input.
"""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reference import Contract, LEGAL, bank_document, response_data
from scenarios import run_scenario, witness

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 3600
RTL = tuple(f"rtl/billet_f1_{name}.sv" for name in ("top", "coeff_bank", "mac", "round_sat"))
DESIGN = (*RTL, "design/SPEC.md", "design/ARCHITECTURE.md", "README.md")


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sources():
    return sorted((*DESIGN, *(relative(p) for p in (ROOT / "tests").rglob("*")
                              if p.is_file() and "__pycache__" not in p.parts
                              and p.suffix != ".pyc")))


def hashes(paths=None):
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in (sources() if paths is None else paths)}


def require(names):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError("Missing executable prerequisites (not installed): " + ", ".join(missing))


def run_tool(argv, directory, *, expected_exit=0, cwd=ROOT):
    directory.mkdir(parents=True, exist_ok=False)
    tool_inputs = {}
    for arg in argv[1:]:
        candidate = arg.split("=", 1)[1] if arg.startswith("+vectors=") else arg
        path = cwd / candidate
        if path.is_file():
            tool_inputs[relative(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = {
        "argv": argv, "cwd": relative(cwd), "started_at": stamp(),
        "timeout_seconds": TIMEOUT, "source_hashes": hashes(),
        "tool_input_hashes": tool_inputs,
        "stdout": relative(directory / "stdout.log"),
        "stderr": relative(directory / "stderr.log"),
    }
    start = time.monotonic()
    with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
        try:
            result = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                    timeout=TIMEOUT, check=False)
            receipt.update(exit_code=result.returncode, timed_out=False)
        except subprocess.TimeoutExpired:
            receipt.update(exit_code=None, timed_out=True)
    receipt.update(ended_at=stamp(), elapsed_seconds=time.monotonic() - start)
    write_json(directory / "receipt.json", receipt)
    if receipt["timed_out"]:
        raise RuntimeError(f"Tool timeout; receipt: {relative(directory / 'receipt.json')}")
    if expected_exit is not None and receipt["exit_code"] != expected_exit:
        raise RuntimeError(f"Tool exit {receipt['exit_code']}; receipt: {relative(directory / 'receipt.json')}")
    return receipt


def initialize(out, tools):
    require(tools)
    out.mkdir(parents=True, exist_ok=False)
    original = hashes()
    snapshot = out / "sources"
    for name in original:
        dest = snapshot / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    write_json(out / "source_inventory.json", original)
    versions = {}
    for tool in tools:
        args = [tool, "--version"] if tool == "python3" else [tool, "-V"]
        rec = run_tool(args, out / ("version-" + tool), expected_exit=None)
        # Icarus' version driver can produce warnings on stderr; capture both.
        versions[tool] = {"receipt": relative(out / ("version-" + tool) / "receipt.json"),
                          "exit_code": rec["exit_code"]}
    write_json(out / "tool_versions.json", versions)
    return original


def finish(out, original, results, claims):
    assert hashes() == original, "Accepted design/verifier sources changed during execution"
    result = {"status": "passed", "source_hashes": original, "results": results,
              "claims": claims, "limitations": [
                  "Simulation of a bounded configuration/seed matrix, not formal proof.",
                  "Generic Yosys cells are not physical area or timing closure.",
                  "No silicon security, FPGA or board measurement.",
                  "Final product PDF is authored by the shared runtime after engineering acceptance."]}
    write_json(out / "result.json", result)
    write_json(out / "claim_to_evidence.json", {
        "source_inventory": relative(out / "source_inventory.json"), "claims": claims})
    print(json.dumps({"status": "passed", "result": relative(out / "result.json")}))
    return result


def make_vectors(folder, taps, seed, banks, full=True, mutation=None):
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / "vectors.txt").open("w") as vec, \
         (folder / "expected.txt").open("w") as expected, \
         (folder / "reference_outputs.jsonl").open("w") as outputs:
        c = Contract(taps, vec, expected, outputs)
        if mutation:
            witness(c, banks, mutation)
            summary = {"cycles": c.cycle, "completed": c.completed, "witness": mutation}
        else:
            summary = run_scenario(c, banks, seed, full)
    write_json(folder / "coverage_expected.json", summary)
    return summary


def simulate(folder, taps, rtl=RTL):
    binary = relative(folder / "simulation.vvp")
    compile_rec = run_tool(
        ["iverilog", "-g2012", "-Wall", "-s", "bridge", f"-Pbridge.T={taps}",
         "-o", binary, "tests/bridge.sv", *rtl], folder / "compile")
    sim_rec = run_tool(
        ["vvp", binary, "+vectors=" + relative(folder / "vectors.txt"),
         "+trace=" + relative(folder / "actual.txt")], folder / "simulate")
    comparisons = 0
    bit_mismatches = 0
    output_mismatch_observations = 0
    first = None
    numerical = None
    with (folder / "expected.txt").open() as expected, (folder / "actual.txt").open() as actual:
        for line in expected:
            got = actual.readline()
            if not got:
                raise AssertionError(f"Truncated simulator trace at {comparisons}; {relative(folder)}")
            e, a = tuple(map(int, line.split())), tuple(map(int, got.split()))
            assert len(a) == len(e) == 7
            assert a[:2] == e[:2], "Trace cycle/phase mismatch"
            if a != e:
                if e[3] and a[3] and e[6] != a[6]:
                    bit_mismatches += ((e[6] & 65535) ^ (a[6] & 65535)).bit_count()
                    output_mismatch_observations += 1
                mismatch = {"cycle": e[0], "phase": e[1], "expected": e[2:], "actual": a[2:],
                            "kind": "output_mismatch" if e[3] and a[3] and e[6] != a[6]
                            else "protocol_mismatch"}
                if first is None:
                    first = mismatch
                if mismatch["kind"] == "output_mismatch" and numerical is None:
                    numerical = mismatch
            comparisons += 1
        assert not actual.read(), "Extra simulator output rows"
    result = {"comparisons": comparisons, "first_mismatch": first,
              "first_numerical_mismatch": numerical, "bit_mismatches": bit_mismatches,
              "bit_mismatch_scope": "all four public trace observation phases, not distinct outputs",
              "output_value_mismatch_observations": output_mismatch_observations,
              "compile_exit": compile_rec["exit_code"], "simulation_exit": sim_rec["exit_code"]}
    write_json(folder / "comparison.json", result)
    return result


def legal_list(text):
    values = [int(s) for s in text.split(",")]
    if not values or any(t not in LEGAL for t in values) or len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("Supported TAP_COUNT values are 1,8,16,32,64")
    return values


def reject_illegal(out):
    records = [{"taps": t, "status": "not_run"} for t in (0, 2, 3, 65)]
    write_json(out / "illegal_parameters.json", records)
    for index, taps in enumerate((0, 2, 3, 65)):
        records[index]["status"] = "running"
        write_json(out / "illegal_parameters.json", records)
        folder = out / f"illegal-T{taps}"
        folder.mkdir()
        binary = relative(folder / "illegal.vvp")
        compile_rec = run_tool(
            ["iverilog", "-g2012", "-Wall", "-s", "bridge", f"-Pbridge.T={taps}",
             "-o", binary, "tests/bridge.sv", *RTL], folder / "compile", expected_exit=None)
        diagnostic = (folder / "compile/stderr.log").read_text()
        if compile_rec["exit_code"] == 0:
            rec = run_tool(["vvp", binary], folder / "simulate", expected_exit=None)
            diagnostic += (folder / "simulate/stdout.log").read_text()
            diagnostic += (folder / "simulate/stderr.log").read_text()
            assert rec["exit_code"] != 0
        else:
            assert compile_rec["exit_code"] != 0
        assert "PARAMETER ERROR" in diagnostic, f"No explicit parameter diagnostic for T={taps}"
        records[index] = {"taps": taps, "status": "passed", "rejected": True, "evidence": relative(folder)}
        write_json(out / "illegal_parameters.json", records)
    write_json(out / "illegal_parameters.json", records)
    return records


def behavior(out, taps, seeds, partial=False):
    original = initialize(out, ["python3", "iverilog", "vvp"])
    ledger = [{"taps": t, "seed": seed, "status": "not_run",
               "required_completed": 0 if partial else 4096,
               "required_random": 0 if partial else 1024} for t in taps for seed in seeds]
    write_json(out / "ledger.json", ledger)
    write_json(out / "illegal_parameters.json",
               [{"taps": t, "status": "not_run"} for t in (0, 2, 3, 65)])
    banks = bank_document()
    write_json(out / "coefficient_banks.json", banks)
    response = []
    index = 0
    for t in taps:
        tbanks = banks["taps"][str(t)]
        response.extend(response_data(t, tbanks))
        for seed in seeds:
            folder = out / f"T{t}-seed{seed}"
            coverage = make_vectors(folder, t, seed, tbanks, full=not partial)
            comp = simulate(folder, t)
            assert comp["first_mismatch"] is None, f"Behavior mismatch: {relative(folder / 'comparison.json')}"
            write_json(folder / "coverage_observed.json", coverage)
            ledger[index] = {"taps": t, "seed": seed, "status": "passed",
                           "required_completed": 0 if partial else 4096,
                           "required_random": 0 if partial else 1024,
                           **coverage, **comp, "raw_receipts": [
                               relative(folder / "compile/receipt.json"),
                               relative(folder / "simulate/receipt.json")],
                           "output_trace": relative(folder / "reference_outputs.jsonl")}
            index += 1
            write_json(out / "ledger.json", ledger)
    illegal = reject_illegal(out)
    write_json(out / "frequency_response.json", response)
    write_json(out / "ledger.json", ledger)
    return finish(out, original, {
        "scope": "partial reproduction subset" if partial else "full requested matrix",
        "expected_cells": len(taps) * len(seeds), "passed_cells": len(ledger),
        "completed_outputs": sum(r["completed"] for r in ledger),
        "bit_mismatches": sum(r["bit_mismatches"] for r in ledger),
        "illegal_parameters": illegal,
    }, [
        {"claim": "Bit-exact public-pin integer and independent floating convolution",
         "evidence": relative(out / "ledger.json")},
        {"claim": "Fully enumerated coefficient banks, ideal definitions and hashes",
         "evidence": relative(out / "coefficient_banks.json")},
        {"claim": "Ideal/quantized/measured complex response and analytical error bounds",
         "evidence": relative(out / "frequency_response.json")},
        {"claim": "Explicit unsupported parameter rejection",
         "evidence": relative(out / "illegal_parameters.json")},
    ])


def mutate(copied, mutation):
    if mutation == "tap_order":
        path = copied / "billet_f1_mac.sv"
        old = "$signed(history[tap_index])"
        new = "$signed(history[TAP_COUNT-1-tap_index])"
    elif mutation == "rounding":
        path = copied / "billet_f1_round_sat.sv"
        old = "floor_quotient + (round_up ? ONE : ZERO)"
        new = "floor_quotient"
    elif mutation == "coefficient_update":
        path = copied / "billet_f1_coeff_bank.sv"
        old = "staging_written[cfg_addr] <= 1'b1;"
        new = """staging_written[cfg_addr] <= 1'b1;
            if (active_bank)
                bank_one[cfg_addr] <= cfg_data;
            else
                bank_zero[cfg_addr] <= cfg_data;"""
    else:
        raise ValueError(mutation)
    before = path.read_text()
    assert before.count(old) == 1
    after = before.replace(old, new)
    path.write_text(after)
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       fromfile="accepted/" + path.name, tofile="mutant/" + path.name))


def defects(out):
    original = initialize(out, ["python3", "iverilog", "vvp"])
    banks = bank_document()["taps"]["8"]
    mutations = ("tap_order", "rounding", "coefficient_update")
    results = [{"mutation": mutation, "status": "not_run"} for mutation in mutations]
    write_json(out / "ledger.json", results)
    for index, mutation in enumerate(mutations):
        folder = out / mutation
        folder.mkdir()
        copied = folder / "rtl"
        copied.mkdir()
        for path in RTL:
            shutil.copyfile(ROOT / path, copied / Path(path).name)
        patch = mutate(copied, mutation)
        (folder / "mutation.patch").write_text(patch)
        copied_sources = [relative(copied / Path(p).name) for p in RTL]
        write_json(folder / "mutant_hashes.json", hashes(copied_sources))
        # Same witness and unchanged oracle must pass on the accepted source first.
        stimulus = folder / "baseline"
        make_vectors(stimulus, 8, 17, banks, mutation=mutation)
        baseline = simulate(stimulus, 8)
        assert baseline["first_mismatch"] is None, f"Invalid witness baseline: {mutation}"
        mutant = folder / "mutant"
        mutant.mkdir()
        for name in ("vectors.txt", "expected.txt", "reference_outputs.jsonl", "coverage_expected.json"):
            shutil.copyfile(stimulus / name, mutant / name)
        comparison = simulate(mutant, 8, copied_sources)
        assert comparison["first_numerical_mismatch"] is not None, f"Surviving mutant: {mutation}"
        assert comparison["first_mismatch"]["kind"] == "output_mismatch", "Not a numerical mutant kill"
        assert comparison["compile_exit"] == comparison["simulation_exit"] == 0
        write_json(folder / "first_mismatch.json", comparison["first_numerical_mismatch"])
        results[index] = {"mutation": mutation, "status": "killed_by_output_mismatch",
                        "baseline_outputs": json.loads((stimulus / "coverage_expected.json").read_text())["completed"],
                        "first_mismatch": comparison["first_numerical_mismatch"],
                        "evidence": relative(folder)}
        write_json(out / "ledger.json", results)
    write_json(out / "ledger.json", results)
    return finish(out, original, results, [{
        "claim": "All three separately compiled/executed mutants fail the unchanged numerical oracle",
        "evidence": relative(out / "ledger.json")}])


def module_counts(netlist):
    modules = netlist["modules"]
    result = {}
    for name, mod in modules.items():
        assert not int(mod.get("attributes", {}).get("blackbox", "0"), 2), f"Blackbox {name}"
        cells = mod.get("cells", {})
        latches = [c for c in cells.values() if "latch" in c["type"].lower()]
        assert not latches, f"Latch cells in {name}"
        for inst, cell in cells.items():
            kind = cell["type"]
            assert kind in modules or kind.startswith("$"), f"Unresolved cell {inst}: {kind}"
            assert cell["connections"] and any(cell["connections"].values()), f"Disconnected cell {inst}"
        seq = [c for c in cells.values() if "dff" in c["type"].lower()
               or c["type"] in ("$ff", "$_FF_")]
        result[name] = {
            "cells": len(cells), "wire_count": len(mod.get("netnames", {})),
            "wire_bits": sum(len(v["bits"]) for v in mod.get("netnames", {}).values()),
            "sequential_cells": len(seq),
            "state_bits": sum(len(c["connections"].get("Q", [])) for c in seq),
            "latches": len(latches),
        }
    assert sum(v["state_bits"] for v in result.values()) > 0
    assert sum(v["cells"] for v in result.values()) > 10
    return result


def inspect_hierarchy(data, taps):
    modules = data["modules"]
    top = modules["billet_f1_top"]
    expected = {"g_supported.u_coeff_bank": "billet_f1_coeff_bank",
                "g_supported.u_mac": "billet_f1_mac",
                "g_supported.u_round_sat": "billet_f1_round_sat"}
    for inst, base in expected.items():
        cell = top["cells"][inst]
        mod = modules[cell["type"]]
        hdlname = mod.get("attributes", {}).get("hdlname", "").lstrip("\\").split()
        assert base == cell["type"] or base in hdlname or base in cell["type"], (inst, cell["type"])
        assert mod.get("cells"), f"Empty implementation module {inst}"
        for port in mod["ports"]:
            assert cell["connections"].get(port), f"Missing port connection {inst}.{port}"
        assert not mod.get("attributes", {}).get("blackbox")
    mac = modules[top["cells"]["g_supported.u_mac"]["type"]]
    multipliers = [c for c in mac["cells"].values() if c["type"] == "$mul"]
    assert len(multipliers) == 1
    p = multipliers[0]["parameters"]
    assert int(p["A_SIGNED"], 2) == int(p["B_SIGNED"], 2) == 1
    for port in ("A", "B"):
        width = int(p[port + "_WIDTH"], 2)
        bits = multipliers[0]["connections"][port]
        assert width >= 16 and len(bits) == width, f"Malformed multiplier operand {port}"
        # Yosys propagates the 32-bit product context into its signed operands.
        # Explicitly prove that any extra bits are only sign extensions.
        assert all(bit == bits[15] for bit in bits[16:]), f"Non-int16 operand {port}"
    assert int(p["Y_WIDTH"], 2) == 32, "Product must retain all 32 bits"
    # Full-width accumulation and actual state are checked before technology mapping.
    adders = [c for c in mac["cells"].values() if c["type"] == "$add"]
    assert any(int(c["parameters"]["Y_WIDTH"], 2) == 32 + (taps - 1).bit_length() for c in adders)
    assert any("dff" in c["type"] for c in mac["cells"].values())
    assert not any("latch" in c["type"].lower() for m in modules.values() for c in m.get("cells", {}).values())
    return {"instances": expected, "signed_16x16_multiplier_count": 1,
            "accumulator_width": 32 + (taps - 1).bit_length(),
            "datapath_connectivity": datapath_connectivity(data)}


def datapath_connectivity(data):
    """Walk real hierarchical cell connections, including state data inputs."""
    modules = data["modules"]
    graph = {}

    def expand(module_name, path, bindings):
        mod = modules[module_name]

        def node(bit):
            return bindings.get(bit, (path, bit)) if isinstance(bit, int) else ("constant", bit)

        for name, cell in mod.get("cells", {}).items():
            if cell["type"] in modules:
                child = modules[cell["type"]]
                mapping = {}
                for port, desc in child["ports"].items():
                    for internal, external in zip(desc["bits"], cell["connections"][port]):
                        if isinstance(internal, int):
                            mapping[internal] = node(external)
                expand(cell["type"], path + "/" + name, mapping)
            else:
                inputs, outputs = [], []
                for port, direction in cell["port_directions"].items():
                    if direction == "input" and port not in ("CLK", "ARST", "SRST", "CLR", "SET"):
                        inputs.extend(node(bit) for bit in cell["connections"][port])
                    elif direction == "output":
                        outputs.extend(node(bit) for bit in cell["connections"][port])
                for out in outputs:
                    graph.setdefault(out, set()).update(inputs)

    expand("billet_f1_top", "top", {})
    top = modules["billet_f1_top"]
    pending = [("top", bit) for bit in top["ports"]["m_data"]["bits"] if isinstance(bit, int)]
    visited = set()
    while pending:
        bit = pending.pop()
        if bit not in visited:
            visited.add(bit)
            pending.extend(graph.get(bit, ()))
    evidence = {}
    for port in ("s_data", "cfg_data"):
        count = sum(("top", bit) in visited for bit in top["ports"][port]["bits"])
        assert count == 16, f"Not all {port} bits reach output datapath through state"
        evidence[port + "_bits_reaching_m_data"] = count
    return evidence


def synthesis(out, taps):
    original = initialize(out, ["python3", "yosys"])
    ledger = [{"taps": t, "status": "not_run"} for t in taps]
    write_json(out / "ledger.json", ledger)
    for index, t in enumerate(taps):
        folder = out / f"T{t}"
        folder.mkdir()
        prefix = relative(folder)
        lines = ["read_verilog -sv " + " ".join(RTL)]
        if t != 32:
            lines.append(f"chparam -set TAP_COUNT {t} billet_f1_top")
        script = "\n".join((*lines,
            "hierarchy -check -top billet_f1_top",
            "proc", "check -assert", f"write_json {prefix}/hierarchy.json",
            "select -assert-none t:$dlatch t:$adlatch t:$_DLATCH_* t:$_DLATCHSR_*",
            "synth -top billet_f1_top",
            "check -assert",
            "select -assert-none t:$dlatch t:$adlatch t:$_DLATCH_* t:$_DLATCHSR_*",
            f"tee -o {prefix}/statistics.json stat -json",
            f"write_json {prefix}/netlist.json",
            f"write_verilog -noattr {prefix}/netlist.v", ""
        ))
        (folder / "synthesis.ys").write_text(script)
        run_tool(["yosys", "-Q", "-T", "-s", prefix + "/synthesis.ys"], folder / "yosys")
        hierarchy = inspect_hierarchy(json.loads((folder / "hierarchy.json").read_text()), t)
        stats = module_counts(json.loads((folder / "netlist.json").read_text()))
        item = {"taps": t, "uses_default_parameter": t == 32,
                "status": "passed", "hierarchy": hierarchy, "generic_module_statistics": stats,
                "receipt": relative(folder / "yosys/receipt.json")}
        write_json(folder / "structural_assertions.json", item)
        ledger[index] = item
        write_json(out / "ledger.json", ledger)
    sizes = {}
    for name in RTL:
        text = (ROOT / name).read_text()
        sizes[name] = {"lines": len(text.splitlines()),
                       "module_definitions": len(re.findall(r"^\s*module\s+", text, re.M))}
    assert sum(s["module_definitions"] for s in sizes.values()) == 4
    write_json(out / "source_sizes.json", sizes)
    write_json(out / "ledger.json", ledger)
    return finish(out, original, ledger, [
        {"claim": "Five legal parameter structural/synthesis checks (or explicitly listed partial subset)",
         "evidence": relative(out / "ledger.json")},
        {"claim": "Actual source/module size; no physical implementation claims",
         "evidence": relative(out / "source_sizes.json")}])


def reproduce(out):
    original = initialize(out, ["python3", "iverilog", "vvp", "yosys"])
    copied = out / "fresh"
    copied.mkdir()
    for name in original:
        dest = copied / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    inventory = {name: hashlib.sha256((copied / name).read_bytes()).hexdigest() for name in original}
    assert inventory == original
    write_json(out / "copied_source_inventory.json", inventory)
    commands = [
        ["python3", "tests/verify.py", "behavior", "--taps", "1,32", "--seeds", "17",
         "--partial", "--out", "evidence/behavior"],
        ["python3", "tests/verify.py", "defects", "--out", "evidence/defects"],
        ["python3", "tests/verify.py", "synthesis", "--taps", "32", "--out", "evidence/synthesis"],
    ]
    results = [{"argv": cmd, "status": "not_run"} for cmd in commands]
    write_json(out / "ledger.json", results)
    for i, cmd in enumerate(commands):
        rec = run_tool(cmd, out / f"command-{i}", cwd=copied)
        stdout = (out / f"command-{i}/stdout.log").read_text().strip().splitlines()
        announced = json.loads(stdout[-1])
        assert announced["status"] == "passed"
        result_path = copied / announced["result"]
        assert result_path.is_relative_to(copied)
        fresh = json.loads(result_path.read_text())
        assert fresh["source_hashes"] == original
        assert fresh["status"] == "passed"
        results[i] = {"argv": cmd, "cwd": relative(copied), "status": "passed",
                      "exit_code": rec["exit_code"],
                      "fresh_result": relative(result_path), "outcome": fresh["results"]}
        write_json(out / "ledger.json", results)
    assert {name: hashlib.sha256((copied / name).read_bytes()).hexdigest() for name in original} == original
    write_json(out / "ledger.json", results)
    return finish(out, original, {"scope": "partial reproduction, not full admitted matrix", "commands": results}, [{
        "claim": "Fresh copied-source simulation, all defect witnesses and default-T synthesis",
        "evidence": relative(out / "ledger.json")}])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("behavior", "defects", "synthesis", "reproduce"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--taps", type=legal_list, default=list(LEGAL))
    parser.add_argument("--seeds", default="17,89,203")
    parser.add_argument("--partial", action="store_true",
                        help="Explicitly partial behavior subset for portable reproduction only")
    args = parser.parse_args()
    os.chdir(ROOT)
    requested = Path(args.out)
    if requested.is_absolute() or ".." in requested.parts:
        parser.error("--out must be a project-relative path without parent traversal")
    if not requested.parts or requested.parts[0] != "evidence":
        parser.error("--out must be beneath evidence/")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = ROOT / requested / run_id
    try:
        if args.command == "behavior":
            behavior(out, args.taps, [int(s) for s in args.seeds.split(",")], args.partial)
        elif args.command == "defects":
            defects(out)
        elif args.command == "synthesis":
            synthesis(out, args.taps)
        else:
            reproduce(out)
    except Exception as exc:
        if out.is_dir():
            for filename in ("ledger.json", "illegal_parameters.json"):
                ledger_path = out / filename
                if ledger_path.is_file():
                    ledger = json.loads(ledger_path.read_text())
                    failed_row = next((row for row in ledger if row["status"] in ("running", "not_run")), None)
                    if failed_row is not None:
                        failed_row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                        write_json(ledger_path, ledger)
                        break
            write_json(out / "failure.json", {
                "status": "failed", "command": sys.argv[1:],
                "error": f"{type(exc).__name__}: {exc}", "source_hashes": hashes()})
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"VERIFICATION_FAILURE {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
