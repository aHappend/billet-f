"""Bounded advisory diagnostics, never a replacement for the admitted L1 suites."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import Evidence
from constants import assert_constants, assert_oracle_primitives
from scenarios import (beamformer_vectors, nlms_vectors, log_vectors, vad_vectors,
                       spectrum_vectors, mel_vectors, acoustic_top_vectors,
                       assert_convergence, legacy_vectors, src_vectors)
from simulation import compile_tb, execute_tb, clean_compiler_intermediates
from synthesis import synthesize
from mutations import run_mutations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--leaf", choices=["beamformer", "nlms", "log", "vad",
                                         "spectrum", "mel", "acoustic_top",
                                         "synthesis", "mutation","legacy","src"], required=True)
    parser.add_argument("--parameter", type=int, default=1)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--simulator", choices=["iverilog", "verilator"],
                        default="iverilog")
    parser.add_argument("--repair-of")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--mutant", default="weight-leakage-removed")
    args = parser.parse_args()
    if args.simulator=="iverilog" and (
            args.full or args.leaf in {"spectrum","mel","mutation"} or
            (args.leaf=="nlms" and args.parameter>=16) or
            (args.leaf=="acoustic_top" and args.parameter>1)):
        args.simulator="verilator"
    evidence = Evidence(f"{args.leaf}-{args.parameter}-seed{args.seed}",
                        preparation=True, repair_of=args.repair_of)
    evidence.record["argv"] = ["python3", "tests/probe.py", *sys.argv[1:]]
    if args.leaf in {"synthesis","mutation"}:
        try:
            if args.leaf=="synthesis":
                evidence.versions([("yosys","-V")])
                synthesize(evidence,"singleton",{"DELAY_DEPTH":1,"AEC_TAPS":1,
                                                "TAP_COUNT":1,"SRC_TAPS":1,"SOS_COUNT":1})
            else:
                run_mutations(evidence,args.mutant)
        except BaseException as error:
            print(evidence.finish(error),flush=True)
            raise
        print(evidence.finish(),flush=True)
        return
    try:
        evidence.versions([("iverilog", "-V"), ("vvp", "-V")] if
                          args.simulator == "iverilog" else [("verilator", "--version")])
        vectors = evidence.path / "vectors.txt"
        evidence.record["coverage"]["rom_constants"] = assert_constants(evidence.root)
        evidence.compared("analytical-rne", assert_oracle_primitives())
        if args.leaf == "beamformer":
            count, metrics = beamformer_vectors(vectors, args.parameter, args.seed)
            parameters = {"DEPTH": args.parameter}
        elif args.leaf == "nlms":
            count, metrics = nlms_vectors(vectors, args.parameter, args.seed, full=args.full)
            parameters = {"TAPS": args.parameter}
        elif args.leaf == "acoustic_top":
            count, metrics = acoustic_top_vectors(
                vectors, args.seed, taps=args.parameter, compact=not args.full)
            parameters = {"AEC": args.parameter}
        elif args.leaf == "legacy":
            count,metrics=legacy_vectors(vectors,taps=args.parameter,full=args.full)
            parameters={"TAPS":args.parameter}
        elif args.leaf == "src":
            count,metrics=src_vectors(vectors,taps=args.parameter,all_ratios=False)
            parameters={"TAPS":args.parameter}
        else:
            generator = {"log": log_vectors, "vad": vad_vectors,
                         "spectrum": spectrum_vectors, "mel": mel_vectors}[args.leaf]
            count = generator(vectors)
            parameters, metrics = {}, {}
        evidence.record["metrics"] = metrics
        binary = compile_tb(evidence, args.leaf + "_tb", parameters, args.simulator)
        execute_tb(evidence, binary, vectors, args.leaf, count, args.simulator)
        if args.leaf == "nlms" and args.full:
            assert_convergence(metrics)
        clean_compiler_intermediates(binary)
    except BaseException as error:
        print(evidence.finish(error), flush=True)
        raise
    print(evidence.finish(), flush=True)


if __name__ == "__main__":
    main()
