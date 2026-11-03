"""Fresh declared-source reproduction without original binaries or runtime metadata."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from constants import assert_constants, assert_oracle_primitives
from evidence import Evidence, ROOT, SELECTORS, hashes
from scenarios import legacy_vectors, acoustic_top_vectors
from simulation import compile_tb, execute_tb, clean_compiler_intermediates
from synthesis import synthesize


def run_worker():
    evidence = Evidence("portable-worker", preparation=True)
    evidence.record["argv"] = ["python3", "tests/portable.py", "--worker"]
    try:
        evidence.versions([("python3", "--version"), ("iverilog", "-V"),
                           ("vvp", "-V"), ("verilator", "--version"), ("yosys", "-V")])
        evidence.record["coverage"]["rom_constants"] = assert_constants(ROOT)
        evidence.compared("analytical-rne", assert_oracle_primitives())
        vectors = evidence.path / "legacy-smoke.txt"
        count, metrics = legacy_vectors(vectors, full=False)
        binary = compile_tb(evidence, "legacy_tb", simulator="iverilog", name="legacy")
        execute_tb(evidence, binary, vectors, "legacy", count, "iverilog")
        evidence.record["coverage"]["legacy"] = metrics
        vectors = evidence.path / "acoustic-frame.txt"
        count, metrics = acoustic_top_vectors(vectors, taps=1, compact=True)
        for simulator in ("iverilog", "verilator"):
            name = "acoustic-"+simulator
            binary = compile_tb(evidence, "acoustic_top_tb", {"AEC": 1},
                                simulator=simulator, name=name)
            execute_tb(evidence, binary, vectors, name, count, simulator)
            clean_compiler_intermediates(binary)
        evidence.record["coverage"]["complete_frame"] = metrics
        synthesize(evidence, "portable-synthesis",
                   {"DELAY_DEPTH": 1, "AEC_TAPS": 1, "TAP_COUNT": 1,
                    "SRC_TAPS": 1, "SOS_COUNT": 1})
    except BaseException as error:
        print(evidence.finish(error), flush=True)
        raise
    print(evidence.finish(), flush=True)
    if evidence.record["exit_code"] != 0:
        raise AssertionError(evidence.record["failure"])


def run_portable(evidence):
    bundle = evidence.path / ("bundle-"+uuid.uuid4().hex[:12])
    bundle.mkdir(exist_ok=False)
    originals = hashes(evidence.root)
    for selector in SELECTORS:
        files = [p for p in evidence.root.glob(selector) if p.is_file()]
        if not files:
            raise AssertionError("portable source selector empty: "+selector)
        for source in files:
            target = bundle / source.relative_to(evidence.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if hashes(bundle) != originals:
        raise AssertionError("fresh portable source binding mismatch")
    # A fresh interpreter imports the copied oracle and helpers, not this process's modules.
    _, stdout, _ = evidence.run(["python3", "tests/portable.py", "--worker"],
                               "portable-worker", cwd=bundle)
    lines = [line for line in stdout.read_text().splitlines()
             if line.startswith("evidence/") and line.endswith("/receipt.json")]
    if len(lines) != 1:
        raise AssertionError("fresh reproduction did not retain exactly one worker receipt")
    receipt_path = bundle / lines[0]
    result = json.loads(receipt_path.read_text())
    if (result["status"] != "passed" or result["exit_code"] != 0 or
            not result["sources_unchanged"] or result["source_sha256"] != originals):
        raise AssertionError("fresh reproduction failed or changed its declared inputs")
    required = {"legacy", "acoustic-iverilog", "acoustic-verilator", "portable-synthesis"}
    comparisons = {row["name"]: row["count"] for row in result["comparisons"]}
    if not required <= comparisons.keys() or any(comparisons[k] <= 0 for k in required):
        raise AssertionError("fresh reproduction comparison coverage incomplete")
    if hashes(bundle) != originals:
        raise AssertionError("portable inputs changed during reproduction")
    evidence.artifact(receipt_path)
    evidence.record["metrics"]["portable"] = {
        "bundle": evidence.relative(bundle), "worker_receipt": evidence.relative(receipt_path),
        "comparisons": comparisons, "source_sha256": originals}
    for name in sorted(required):
        evidence.compared("portable-"+name, comparisons[name],
                          receipt=evidence.relative(receipt_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.parse_args()
    if Path.cwd().resolve() != ROOT:
        parser.error("run from the copied project root")
    run_worker()
