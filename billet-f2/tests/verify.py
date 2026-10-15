#!/usr/bin/env python3
"""BILLET-F2 admitted suites.

Run from the delivered project root:
  python3 tests/verify.py --suite behavior --output evidence/behavior
  python3 tests/verify.py --suite compatibility --output evidence/compatibility
  python3 tests/verify.py --suite defects --output evidence/defects
  python3 tests/verify.py --suite synthesis --output evidence/synthesis
  python3 tests/verify.py --suite portability --output evidence/portability

--cell T,L,SEED selects one complete behavior/compatibility/synthesis cell for
bounded diagnostics and clean-tree reproduction; it never lowers thresholds.
Results and exact per-tool commands are retained under the requested output.
Existing evidence is retained by creating a new attempt directory.

Requirement mapping:
  behavior: 45 cells; all public pre/post pins, conservation and reset epochs;
    >=256 retired/channel, >=4 distinct commits/channel, >=16 stall cycles/channel;
    signed ties/near-ties, saturation, impulses and interleaved history;
    ownership exclusion, priority, completeness and exact G/G+1 observations.
  compatibility: 15 cells; unchanged F1 and F2 channel zero run in lockstep,
    with the same independent integer oracle and reachable directed thresholds.
  defects: baseline plus three disposable semantic mutations; only a Python
    behavioral/protocol mismatch after successful tool execution counts as a kill.
  synthesis: all 15 cells, substantive connected storage/hierarchy and useful
    parallel multipliers before flattening; generic synth -noabc, no latches,
    plus six successful-elaboration/nonzero-runtime parameter diagnostics.
  portability: fresh declared-source-only tree, complete behavior 8/4/17,
    compatibility 1/1/17 and synthesis 8/4.
Negative multi-product saturation and lane cancellation are unreachable at one
tap. Inter-lane cancellation applies only to multi-tap/multi-lane cells.
Seeds are 17, 89, 233. Missing tools, X/Z, partial output, timeouts, nonzero
unexpected exits, numerical/protocol mismatches or coverage shortfalls fail.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation import behavior_cell, defects
from support import Evidence, ROOT, SELECTORS, relative, require, write_json
from synthesis import illegal_parameters, synthesis_cell


TAPS = (1, 8, 16, 32, 64)
LANES = (1, 2, 4)
SEEDS = (17, 89, 233)


def portability(evidence):
    tree = evidence.path / "fresh-project"
    tree.mkdir()
    copied = {}
    for selector in SELECTORS:
        for source in sorted(ROOT.glob(selector)):
            target = tree / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied[source.relative_to(ROOT).as_posix()] = evidence.sources[relative(source)]
    write_json(evidence.path / "portable_inputs.json", copied)
    # Run in the fresh project, not with a wrapper importing the original tests.
    # The subprocess helper's cwd is project-relative and retained explicitly.
    commands = [
        ("behavior", "8,4,17"), ("compatibility", "1,1,17"), ("synthesis", "8,4,17"),
    ]
    records = []
    for suite, cell in commands:
        argv = ["python3", "tests/verify.py", "--suite", suite, "--cell", cell,
                "--output", "evidence/" + suite]
        record = evidence.run_in_tree(argv, evidence.path / ("reproduce-" + suite), tree)
        result_path = tree / "evidence" / suite / "results.json"
        require(result_path.is_file(), "missing fresh-tree result")
        result = json.loads(result_path.read_text())
        require(result["status"] == "passed" and len(result["runs"]) == 1,
                "partial portability execution")
        require(result["source_hashes"] == copied, "fresh-tree source identity mismatch")
        records.append({"suite": suite, "cell": cell, "result": relative(result_path),
                        "receipt": record})
    return {"status": "passed", "fresh_inputs": copied, "executions": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True,
                        choices=("behavior", "compatibility", "defects", "synthesis", "portability"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--cell")
    parser.add_argument("--repair-of", help="project-relative prior failed preparation receipt")
    args = parser.parse_args()
    required = ["python3", "iverilog", "vvp"]
    if args.suite in ("synthesis", "portability"):
        required += ["yosys"]
    evidence = None
    result = None
    active_entry = None
    try:
        evidence = Evidence(args.output, ["python3", "tests/verify.py"] + sys.argv[1:], required)
        if args.repair_of:
            prior = ROOT / args.repair_of
            prior.resolve().relative_to(ROOT)
            require(prior.is_file(), "missing prior repair receipt")
            evidence.receipt["repair_of"] = args.repair_of
            evidence.save()
        selected = None
        if args.cell:
            selected = tuple(map(int, args.cell.split(",")))
            require(len(selected) == 3, "--cell requires T,L,SEED")
            require(selected[0] in TAPS and selected[1] in LANES and selected[2] in SEEDS,
                    "cell outside admitted legal matrix")
            require(args.suite in ("behavior", "compatibility", "synthesis"), "unsupported --cell")
        result = {"suite": args.suite, "source_hashes": evidence.sources, "runs": [],
                  "ledger": [], "mandatory_full_matrix": selected is None}
        write_json(evidence.path / "results.json", dict(result, status="running"))
        if args.suite in ("behavior", "compatibility"):
            compatibility = args.suite == "compatibility"
            if selected and compatibility:
                require(selected[1] == 1, "F1 compatibility requires one lane")
            cells = [selected] if selected else [
                (t, l, s) for t in TAPS for l in ((1,) if compatibility else LANES) for s in SEEDS]
            result["required_run_count"] = len(cells)
            result["ledger"] = [
                {"taps": t, "lanes": l, "seed": s, "status": "not_run",
                 "required_retirements_per_channel": 256, "units": "retired samples",
                 "required_channels": [0] if compatibility else [0, 1, 2, 3],
                 "result": relative(evidence.path / f"t{t}-l{l}-s{s}/result.json")}
                for t, l, s in cells]
            write_json(evidence.path / "results.json", dict(result, status="running"))
            for (t, l, s), active_entry in zip(cells, result["ledger"]):
                active_entry["status"] = "running"
                write_json(evidence.path / "results.json", dict(result, status="running"))
                run = behavior_cell(evidence, evidence.path / f"t{t}-l{l}-s{s}", t, l, s,
                                    compatibility)
                active_entry.update(status="passed", actual_retirements=run["retired"],
                                    canceled=run["canceled"], receipt=run["simulation_receipt"])
                result["runs"].append(run)
                write_json(evidence.path / "results.json", dict(result, status="running"))
            require(len(result["runs"]) == (len(cells)), "missing behavior cells")
            result["total_retired"] = sum(sum(r["retired"]) for r in result["runs"])
            result["total_canceled"] = sum(sum(r["canceled"]) for r in result["runs"])
            result["maximum_output_code_error"] = 0
        elif args.suite == "defects":
            active_entry = {"check": "baseline_and_three_semantic_mutants", "status": "running",
                            "required_kills": 3, "required_baseline": "passed"}
            result["ledger"] = [active_entry]
            write_json(evidence.path / "results.json", dict(result, status="running"))
            result["defects"] = defects(evidence, evidence.path / "mutations")
            active_entry.update(status="passed", actual_kills=result["defects"]["kills"])
        elif args.suite == "synthesis":
            cells = [(selected[0], selected[1])] if selected else [(t, l) for t in TAPS for l in LANES]
            result["required_run_count"] = len(cells)
            result["ledger"] = [
                {"taps": t, "lanes": l, "status": "not_run", "required_latches": 0,
                 "result": relative(evidence.path / f"t{t}-l{l}/result.json")} for t, l in cells]
            if not selected:
                result["ledger"].append({
                    "check": "illegal_parameter_diagnostics", "status": "not_run",
                    "required_cases": [[0, 1], [3, 1], [65, 1], [8, 0], [8, 3], [8, 8]],
                })
            write_json(evidence.path / "results.json", dict(result, status="running"))
            for (t, l), active_entry in zip(cells, result["ledger"]):
                active_entry["status"] = "running"
                write_json(evidence.path / "results.json", dict(result, status="running"))
                run = synthesis_cell(evidence, evidence.path / f"t{t}-l{l}", t, l)
                result["runs"].append(run)
                active_entry.update(status="passed", actual_latches=0, receipt=run["receipt"])
                write_json(evidence.path / "results.json", dict(result, status="running"))
            if not selected:
                active_entry = result["ledger"][-1]
                active_entry["status"] = "running"
                write_json(evidence.path / "results.json", dict(result, status="running"))
                result["illegal_parameters"] = illegal_parameters(evidence, evidence.path / "illegal")
                active_entry.update(status="passed", actual_cases=result["illegal_parameters"])
        else:
            active_entry = {"check": "fresh_tree_reproduction", "status": "running",
                            "required_cells": ["behavior:8,4,17", "compatibility:1,1,17",
                                               "synthesis:8,4,17"]}
            result["ledger"] = [active_entry]
            write_json(evidence.path / "results.json", dict(result, status="running"))
            result["portability"] = portability(evidence)
            active_entry["status"] = "passed"
        result["status"] = "passed"
        write_json(evidence.path / "results.json", result)
        message = f"{args.suite}: passed; {relative(evidence.path / 'results.json')}"
        evidence.finish(0, message)
        print(message)
        return 0
    except Exception as error:
        if evidence:
            if result is not None:
                if active_entry is not None and active_entry["status"] == "running":
                    active_entry.update(status="failed", error=str(error))
                result["status"] = "failed"
                write_json(evidence.path / "results.json", result)
            write_json(evidence.path / "failure.json", {"type": type(error).__name__,
                                                       "message": str(error)})
            evidence.finish(1, f"{type(error).__name__}: {error}")
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
