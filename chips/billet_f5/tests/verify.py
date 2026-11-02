"""Portable, self-contained BILLET-F5 L1 checks; Python stdlib and installed tools.

compatibility: all five inherited modes/four channels, a legal covering matrix,
64 SRC ratios, spectral frames, every bin/band and full-chain tokens.
acoustics: each admitted seed, four tap counts/delay depths, fixed convergence,
double-talk/recovery and spatial thresholds, multi-frame VAD/token accounting.
robustness: legal/illegal elaboration, signed ties/saturation, pre/post-edge
latency, reset during division/partial frames, commits and independent stalls.
defect-sensitivity: unchanged numerical scoreboards reject seven compiled mutants.
synthesis: complete default/singleton/maximum generic memory-preserving netlists.
portable: copied declared inputs, fresh interpreter, compact dual simulation and
real Yosys checks, with no original binary/runtime/report dependencies.

Run from the delivered root: python3 tests/verify.py --suite <suite>; acoustics
also requires --seed 17, 29 or 43. Every invocation allocates exclusive evidence.
Interpreted processes are bounded to 600 seconds and fewer than 500000 cycles;
whole-suite wall time is bounded to 2700 seconds. Large sweeps use Verilator.
Native physical timing and hardware gates are owned by the shared runtime.
"""

import argparse
from pathlib import Path
import signal
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acoustics import run_acoustics
from compatibility import run_compatibility
from constants import assert_constants, assert_oracle_primitives
from evidence import Evidence, ROOT
from mutations import run_mutations
from portable import run_portable
from robustness import run_robustness
from synthesis import run_synthesis


def suite_expired(signum, frame):
    raise TimeoutError("suite exceeded its 2700-second execution budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=(
        "compatibility", "acoustics", "robustness", "defect-sensitivity",
        "synthesis", "portable"))
    parser.add_argument("--seed", type=int, choices=(17, 29, 43))
    args = parser.parse_args()
    if (args.suite == "acoustics") != (args.seed is not None):
        parser.error("--seed is required only for the acoustics suite")
    if Path.cwd().resolve() != ROOT:
        parser.error("run from the delivered project root")
    stem = args.suite + (f"-seed{args.seed}" if args.seed is not None else "")
    evidence = Evidence(stem)
    signal.signal(signal.SIGALRM, suite_expired)
    signal.alarm(2700)
    try:
        evidence.versions([("python3", "--version")])
        evidence.record["coverage"]["rom_constants"] = assert_constants(ROOT)
        evidence.compared("analytical-rne", assert_oracle_primitives())
        if args.suite == "acoustics":
            run_acoustics(evidence, args.seed)
        else:
            {"compatibility": run_compatibility, "robustness": run_robustness,
             "defect-sensitivity": run_mutations, "synthesis": run_synthesis,
             "portable": run_portable}[args.suite](evidence)
        if len(evidence.record["comparisons"]) < 2:
            raise AssertionError("suite produced no DUT comparison or structural result")
    except BaseException as error:
        print(evidence.finish(error), flush=True)
        raise
    finally:
        signal.alarm(0)
    receipt = evidence.finish()
    print(receipt, flush=True)
    if evidence.record["exit_code"] != 0:
        raise AssertionError(evidence.record["failure"])


if __name__ == "__main__":
    main()
