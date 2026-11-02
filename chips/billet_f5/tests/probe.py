"""Bounded advisory diagnostics, never a replacement for the admitted L1 suites."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import Evidence
from scenarios import beamformer_vectors, nlms_vectors
from simulation import compile_tb, execute_tb, clean_compiler_intermediates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--leaf", choices=["beamformer", "nlms"], required=True)
    parser.add_argument("--parameter", type=int, required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--simulator", choices=["iverilog", "verilator"],
                        default="iverilog")
    parser.add_argument("--repair-of")
    args = parser.parse_args()
    evidence = Evidence(f"{args.leaf}-{args.parameter}-seed{args.seed}",
                        preparation=True, repair_of=args.repair_of)
    evidence.record["argv"] = ["python3", "tests/probe.py", *sys.argv[1:]]
    try:
        evidence.versions([("iverilog", "-V"), ("vvp", "-V")] if
                          args.simulator == "iverilog" else [("verilator", "--version")])
        vectors = evidence.path / "vectors.txt"
        if args.leaf == "beamformer":
            count, metrics = beamformer_vectors(vectors, args.parameter, args.seed)
            parameters = {"DEPTH": args.parameter}
        else:
            count, metrics = nlms_vectors(vectors, args.parameter, args.seed, full=False)
            parameters = {"TAPS": args.parameter}
        evidence.record["metrics"] = metrics
        binary = compile_tb(evidence, args.leaf + "_tb", parameters, args.simulator)
        execute_tb(evidence, binary, vectors, args.leaf, count, args.simulator)
        clean_compiler_intermediates(binary)
    except BaseException as error:
        print(evidence.finish(error), flush=True)
        raise
    print(evidence.finish(), flush=True)


if __name__ == "__main__":
    main()
