"""Portable, fail-closed subprocess execution and immutable source-bound receipts."""

import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
SELECTORS = ("rtl/*.sv", "design/SPEC.md", "design/ARCHITECTURE.md",
             "tests/*.py", "billet_f1_reference/rtl/*.sv")
TIMEOUT = 3600


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def inventory():
    files = sorted({p for selector in SELECTORS for p in ROOT.glob(selector)})
    for selector in SELECTORS:
        if not list(ROOT.glob(selector)):
            raise RuntimeError(f"missing declared input: {selector}")
    return {relative(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class Evidence:
    def __init__(self, output, argv, required):
        requested = ROOT / output
        requested.resolve().relative_to(ROOT)
        requested.mkdir(parents=True, exist_ok=True)
        self.path = requested
        if any(requested.iterdir()):
            self.path = requested / ("attempt-" + uuid.uuid4().hex[:12])
            self.path.mkdir()
        self.sources = inventory()
        self.receipt = {
            "argv": argv, "cwd": ".", "started_at": stamp(),
            "timeout_s": None, "timeout_owner": "shared runtime",
            "child_timeout_s": TIMEOUT, "source_hashes": self.sources,
            "tool_versions": {}, "commands": [],
            "stdout": relative(self.path / "stdout.log"),
            "stderr": relative(self.path / "stderr.log"),
        }
        self.start = time.monotonic()
        (self.path / "stdout.log").write_text("")
        (self.path / "stderr.log").write_text("")
        for filename in self.sources:
            target = self.path / "sources" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / filename, target)
        self.save()
        for name in required:
            if shutil.which(name) is None:
                self.finish(1, f"missing prerequisite: {name}")
                raise RuntimeError(f"missing prerequisite: {name}; no installation attempted")
        versions = {"python3": ["python3", "--version"],
                    "iverilog": ["iverilog", "-V"], "vvp": ["vvp", "-V"],
                    "yosys": ["yosys", "-V"]}
        for name in required:
            record = self.run(versions[name], self.path / ("version-" + name))
            self.receipt["tool_versions"][name] = record
        self.save()

    def save(self):
        write_json(self.path / "receipt.json", self.receipt)

    def run(self, argv, directory, expected=0, cwd=ROOT):
        directory.mkdir(parents=True, exist_ok=False)
        out, err = directory / "stdout.log", directory / "stderr.log"
        record = {"argv": argv, "cwd": relative(cwd), "timeout_s": TIMEOUT,
                  "started_at": stamp(), "source_hashes": self.sources,
                  "stdout": relative(out), "stderr": relative(err),
                  "timed_out": False}
        start = time.monotonic()
        try:
            with out.open("w") as stdout, err.open("w") as stderr:
                result = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                        timeout=TIMEOUT, check=False)
            record["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            record["exit_code"] = None
            record["timed_out"] = True
        finally:
            record["ended_at"] = stamp()
            record["duration_s"] = time.monotonic() - start
            write_json(directory / "receipt.json", record)
            self.receipt["commands"].append(relative(directory / "receipt.json"))
            self.save()
        if record["timed_out"]:
            raise RuntimeError(f"tool timeout: {relative(directory / 'receipt.json')}")
        if expected is not None and record["exit_code"] != expected:
            raise RuntimeError(f"tool exit {record['exit_code']}: "
                               f"{relative(directory / 'receipt.json')}")
        return record

    def run_in_tree(self, argv, directory, tree):
        self.run(argv, directory, cwd=tree)
        return relative(directory / "receipt.json")

    def finish(self, code, detail):
        self.receipt.update(exit_code=code, ended_at=stamp(),
                            duration_s=time.monotonic() - self.start, detail=detail)
        (self.path / ("stdout.log" if code == 0 else "stderr.log")).write_text(detail + "\n")
        self.save()


class Mismatch(AssertionError):
    """Only a successful simulation followed by a behavioral rejection is a kill."""

    def __init__(self, cycle, detail, expected=None, observed=None):
        super().__init__(f"cycle {cycle}: {detail}; expected={expected}, observed={observed}")
        self.record = {"cycle": cycle, "detail": detail,
                       "expected": expected, "observed": observed}


def require(condition, message):
    if not condition:
        raise AssertionError(message)
