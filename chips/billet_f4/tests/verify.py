#!/usr/bin/env python3
"""BILLET-F4 independent L1 entry point.

The executable requirement matrix lives in campaign.py. No suite substitutes
for native timing/hardware gates or uses the future product PDF as an input.
"""

import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulator import Evidence


class Tee:
    def __init__(self, stream, file):
        self.stream, self.file = stream, file

    def write(self, text):
        self.file.write(text)
        self.stream.write(text)
        return len(text)

    def flush(self):
        self.file.flush()
        self.stream.flush()


def main():
    if not __debug__:
        raise RuntimeError("Numerical verification cannot run with Python assertions disabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("behavior", "sensitivity", "synthesis",
                                           "reproduction", "portable-smoke"))
    parser.add_argument("--probe", choices=("startup", "leaves", "integration", "synthesis-minimum",
                                          "controls", "datapath", "logarithm"),
                        help="bounded advisory diagnostic")
    parser.add_argument("--repair-of", help="project-relative failed preparation receipt")
    args = parser.parse_args()
    from campaign import (behavior, sensitivity, synthesis, reproduction, portable_smoke,
                          startup, leaves, integration_probe, synthesis_probe, control_matrix,
                          datapath_probe, log_matrix)
    functions = {"behavior": behavior, "sensitivity": sensitivity, "synthesis": synthesis,
                 "reproduction": reproduction, "portable-smoke": portable_smoke,
                 "startup": startup, "leaves": leaves, "integration": integration_probe,
                 "synthesis-minimum": synthesis_probe, "controls": control_matrix,
                 "datapath": datapath_probe, "logarithm": log_matrix}
    suites = [args.probe] if args.probe else [args.suite] if args.suite else [
        "behavior", "sensitivity", "synthesis", "reproduction"]
    for suite in suites:
        evidence = Evidence(suite, bool(args.probe), args.repair_of)
        if args.probe:
            evidence.receipt["argv"] = ["python3", "tests/verify.py", "--probe", args.probe]
            if args.repair_of:
                evidence.receipt["argv"] += ["--repair-of", args.repair_of]
            evidence.write()
        with (evidence.path / "stdout.txt").open("w") as stdout, (
                evidence.path / "stderr.txt").open("w") as stderr:
            with redirect_stdout(Tee(sys.stdout, stdout)), redirect_stderr(Tee(sys.stderr, stderr)):
                try:
                    evidence.tool("python3")
                    functions[suite](evidence)
                except Exception as error:
                    traceback.print_exc()
                    evidence.finish(error)
                    print(json.dumps({"suite": suite, "status": "failed",
                                      "receipt": evidence.relative(evidence.path / "receipt.json")}), flush=True)
                    return 1
                evidence.finish()
                print(json.dumps({"suite": suite, "status": "passed",
                                  "receipt": evidence.relative(evidence.path / "receipt.json")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
