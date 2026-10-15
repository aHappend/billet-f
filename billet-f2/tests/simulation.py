"""Compile real RTL, retain public traces, compare every edge independently."""

import difflib
import hashlib

from bridge import testbench
from model import FIELDS, Oracle
from stimulus import Stimulus
from support import ROOT, Mismatch, relative, require, write_json


def behavior_cell(evidence, directory, taps, lanes, seed, compatibility=False,
                  rtl=None, thresholds=True):
    directory.mkdir(parents=True, exist_ok=False)
    rows, phases = Stimulus(taps, lanes, seed, compatibility).run()
    vectors = directory / "vectors.txt"
    vectors.write_text("".join(" ".join(str(row[f]) for f in FIELDS) + "\n" for row in rows))
    write_json(directory / "phases.json", phases)
    write_json(directory / "case.json", {
        "taps": taps, "lanes": lanes, "seed": seed, "compatibility": compatibility,
        "vectors": len(rows), "retirement_threshold": 256,
        "channels": [0] if compatibility else list(range(4)),
        "oracle": "Python arbitrary-precision signed convolution and divmod rounding",
    })
    tb = directory / "bridge.sv"
    tb.write_text(testbench(taps, lanes, compatibility))
    sources = sorted(ROOT.glob("rtl/*.sv")) if rtl is None else sorted(rtl.glob("*.sv"))
    if compatibility:
        sources += sorted(ROOT.glob("billet_f1_reference/rtl/*.sv"))
    write_json(directory / "compiled_source_hashes.json", {
        relative(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources + [tb]
    })
    binary = directory / "simulation.vvp"
    evidence.run(["iverilog", "-g2012", "-Wall", "-s", "tb", "-o", relative(binary),
                  relative(tb)] + [relative(p) for p in sources], directory / "compile")
    trace = directory / "trace.txt"
    evidence.run(["vvp", relative(binary), "+vectors=" + relative(vectors),
                  "+trace=" + relative(trace)], directory / "simulate")
    stdout = (directory / "simulate/stdout.log").read_text()
    require(stdout.splitlines() == [f"DONE {len(rows)}"], "missing/partial simulator completion")
    lines = trace.read_text().splitlines()
    require(len(lines) == len(rows), "missing/partial public trace")
    model = Oracle(taps, lanes)
    try:
        for cycle, (line, row, phase) in enumerate(zip(lines, rows, phases)):
            parts = line.split()
            require(len(parts) == 14 and parts[0] == "R", "malformed public trace")
            # int() fails explicitly on any X/Z; the bridge also rejects them.
            values = list(map(int, parts[1:]))
            require(values[0] == cycle, "missing/reordered public trace")
            model.tick(row, phase, values[1:7], values[7:13])
        result = model.results([0] if compatibility else range(4), thresholds=thresholds)
    except Mismatch as failure:
        failure.record.update(inputs=rows[failure.record["cycle"]],
                              trace=relative(trace), seed=seed, taps=taps, lanes=lanes)
        write_json(directory / "mismatch.json", failure.record)
        raise
    result.update(seed=seed, compatibility=compatibility,
                  raw_trace=relative(trace),
                  compile_receipt=relative(directory / "compile/receipt.json"),
                  simulation_receipt=relative(directory / "simulate/receipt.json"))
    # Trace windows include actual input drives and both pre/post public pins.
    for boundary in model.boundaries:
        cycle = boundary["cycle"]
        boundary["public_trace_window"] = [
            {"cycle": i, "input": rows[i], "observation": lines[i]}
            for i in range(max(0, cycle - model.groups - 3),
                           min(len(rows), cycle + model.groups + 4))
        ]
    write_json(directory / "bank_boundaries.json", model.boundaries)
    write_json(directory / "transactions.json", model.transactions)
    write_json(directory / "canceled.json", model.cancel_records)
    write_json(directory / "coverage_witnesses.json", model.coverage_witnesses)
    write_json(directory / "result.json", result)
    return result


def defects(evidence, directory):
    directory.mkdir()
    baseline = behavior_cell(evidence, directory / "baseline", 8, 4, 17)
    changes = {
        "history_crossing": (
            "billet_f2_history.sv",
            "history[read_channel*TAP_COUNT + tap_addr]",
            "history[((read_channel+1)%4)*TAP_COUNT + tap_addr]"),
        "bank_ownership": (
            "billet_f2_top.sv",
            "assign commit_ready = rst_n && staging_complete[commit_channel] &&\n"
            "                                  !busy && !output_valid;",
            "assign commit_ready = rst_n && staging_complete[commit_channel] &&\n"
            "                                  ((!busy && !output_valid) || commit_valid);"),
        "reduction_lane": (
            "billet_f2_mac.sv",
            "assign reduction[node] = reduction[2*node+1] + reduction[2*node+2];",
            "assign reduction[node] = (node == 0) ? reduction[2*node+1] :\n"
            "                                     reduction[2*node+1] + reduction[2*node+2];"),
    }
    results = []
    for name, (filename, old, new) in changes.items():
        mutant = directory / name
        rtl = mutant / "rtl"
        rtl.mkdir(parents=True)
        patch = []
        hashes = {}
        for original in sorted(ROOT.glob("rtl/*.sv")):
            before = original.read_text()
            after = before
            if original.name == filename:
                require(before.count(old) == 1, f"mutation anchor is not unique: {name}")
                after = before.replace(old, new)
                patch += list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                  fromfile="rtl/" + filename,
                                                  tofile=name + "/rtl/" + filename))
            target = rtl / original.name
            target.write_text(after)
            hashes[relative(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
        (mutant / "defect.patch").write_text("".join(patch))
        write_json(mutant / "source_hashes.json", hashes)
        try:
            behavior_cell(evidence, mutant / "experiment", 8, 4, 17, rtl=rtl)
        except Mismatch as mismatch:
            record = {"name": name, "status": "killed_by_behavioral_mismatch",
                      "trigger": mismatch.record, "baseline": baseline["simulation_receipt"],
                      "mutant_simulation": relative(mutant / "experiment/simulate/receipt.json"),
                      "patch": relative(mutant / "defect.patch")}
            write_json(mutant / "result.json", record)
            results.append(record)
        else:
            raise AssertionError(f"semantic mutant survived: {name}")
    require(len(results) == 3, "all three distinct semantic mutants must be killed")
    return {"status": "passed", "baseline": baseline, "mutants": results, "kills": 3}
