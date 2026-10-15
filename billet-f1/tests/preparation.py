#!/usr/bin/env python3
"""Optional bounded harness diagnostic, never an admitted acceptance substitute."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify
from reference import bank_document


def worker(out):
    original = verify.initialize(out, ["python3", "iverilog", "vvp", "yosys"])
    for taps in (1, 8):
        cell = out / f"smoke-T{taps}"
        verify.make_vectors(cell, taps, 17, bank_document()["taps"][str(taps)], full=False)
        compared = verify.simulate(cell, taps)
        assert compared["first_mismatch"] is None, f"Smoke mismatch: {verify.relative(cell / 'comparison.json')}"
    # A single bounded mutation diagnostic exercises disposable-source plumbing;
    # the admitted sensitivity command independently runs all three witnesses.
    cell = out / "update-witness"
    cell.mkdir()
    copied = cell / "rtl"
    copied.mkdir()
    for source in verify.RTL:
        shutil.copyfile(verify.ROOT / source, copied / Path(source).name)
    (cell / "mutation.patch").write_text(verify.mutate(copied, "coefficient_update"))
    vectors = cell / "witness"
    verify.make_vectors(vectors, 8, 17, bank_document()["taps"]["8"], mutation="coefficient_update")
    baseline = verify.simulate(vectors, 8)
    assert baseline["first_mismatch"] is None
    # Retain separate trace/log namespaces and exactly the same input/oracle.
    mutant = cell / "mutant"
    mutant.mkdir()
    for name in ("vectors.txt", "expected.txt", "reference_outputs.jsonl", "coverage_expected.json"):
        shutil.copyfile(vectors / name, mutant / name)
    comparison = verify.simulate(mutant, 8, [verify.relative(copied / Path(p).name) for p in verify.RTL])
    assert comparison["first_mismatch"]["kind"] == "output_mismatch"
    assert comparison["first_numerical_mismatch"] is not None
    verify.synthesis(out / "synthesis-smoke", [1])
    assert verify.hashes() == original
    print("Bounded harness diagnostic completed; not full acceptance.")


def main():
    os.chdir(verify.ROOT)
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(verify.ROOT / sys.argv[2])
        return
    name = datetime.now(timezone.utc).strftime("verifier-smoke-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    out = verify.ROOT / "evidence/preparation" / name
    out.mkdir(parents=True)
    original = verify.hashes()
    for source in original:
        dest = out / "sources" / source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(verify.ROOT / source, dest)
    versions = {}
    for tool in ("python3", "iverilog", "vvp", "yosys"):
        argv = [tool, "--version" if tool == "python3" else "-V"]
        result = subprocess.run(argv, capture_output=True, timeout=3600, check=False)
        (out / f"{tool}-version.stdout").write_bytes(result.stdout)
        (out / f"{tool}-version.stderr").write_bytes(result.stderr)
        versions[tool] = {"argv": argv, "exit_code": result.returncode,
                          "stdout": verify.relative(out / f"{tool}-version.stdout"),
                          "stderr": verify.relative(out / f"{tool}-version.stderr")}
    argv = ["python3", "tests/preparation.py", "--worker", verify.relative(out / "diagnostic")]
    receipt = {"argv": argv, "cwd": ".", "source_hashes": original,
               "source_snapshot": verify.relative(out / "sources"), "tool_versions": versions,
               "started_at": verify.stamp(), "timeout_seconds": 14400,
               "stdout": verify.relative(out / "stdout.log"), "stderr": verify.relative(out / "stderr.log"),
               "acceptance_evidence": False}
    if len(sys.argv) == 3 and sys.argv[1] == "--repair-of":
        repair = Path(sys.argv[2])
        assert not repair.is_absolute() and ".." not in repair.parts
        assert (verify.ROOT / repair).is_file()
        receipt["repair_of"] = repair.as_posix()
    start = time.monotonic()
    with (out / "stdout.log").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
        try:
            result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=14400, check=False)
            receipt.update(exit_code=result.returncode, timed_out=False)
        except subprocess.TimeoutExpired:
            receipt.update(exit_code=None, timed_out=True)
    receipt.update(ended_at=verify.stamp(), elapsed_seconds=time.monotonic() - start)
    verify.write_json(out / "receipt.json", receipt)
    print(json.dumps({"receipt": verify.relative(out / "receipt.json"),
                      "exit_code": receipt["exit_code"], "timed_out": receipt["timed_out"]}))
    if receipt["exit_code"] != 0:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"PREPARATION_FAILURE {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
