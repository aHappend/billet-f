"""Exclusive source-bound execution receipts for portable verification."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parent.parent
SELECTORS = ("rtl/*.sv", "design/*.md", "tests/*",
             "billet_f4_reference/rtl/*.sv")


def hashes(root=ROOT):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for selector in SELECTORS for p in sorted(root.glob(selector)) if p.is_file()
    }


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Evidence:
    def __init__(self, suite, preparation=False, root=ROOT, repair_of=None):
        self.root = root
        identifier = (datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        base = root / "evidence"
        if preparation:
            base /= "preparation"
            stem = suite
        else:
            stem = "l1-" + suite
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / (stem + "-" + identifier)
        self.path.mkdir(exist_ok=False)
        self.record = {
            "suite": suite, "argv": ["python3", "tests/verify.py", *os.sys.argv[1:]],
            "cwd": ".", "source_sha256": hashes(root), "started_at": stamp(),
            "commands": [], "comparisons": [], "metrics": {}, "coverage": {},
            "repair_of": repair_of,
        }
        self.finish_called = False
        self.save()

    def relative(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def save(self):
        # This file belongs to this invocation; prior runs are never reopened.
        (self.path / "receipt.json").write_text(
            json.dumps(self.record, indent=2, sort_keys=True) + "\n")

    def run(self, argv, name, timeout=3600, cwd=None, expected_exit=0):
        directory = cwd or self.root
        stdout = self.path / (name + ".stdout.txt")
        stderr = self.path / (name + ".stderr.txt")
        entry = {
            "argv": [str(a) for a in argv], "cwd": self.relative(directory),
            "started_at": stamp(), "timeout_s": timeout,
            "stdout": self.relative(stdout), "stderr": self.relative(stderr),
        }
        self.record["commands"].append(entry)
        self.save()
        start = time.monotonic()
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(argv, cwd=directory, stdout=out, stderr=err)
            timed_out = False
            try:
                while True:
                    try:
                        code = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        print(f"progress {name}: {int(time.monotonic()-start)}s",
                              flush=True)
                        if time.monotonic() - start >= timeout:
                            timed_out = True
                            process.kill()
                            code = process.wait()
                            break
            except BaseException:
                process.kill()
                process.wait()
                raise
        entry.update(ended_at=stamp(), duration_s=time.monotonic() - start,
                     exit_code=code, timed_out=timed_out)
        self.save()
        if timed_out or (expected_exit is not None and code != expected_exit):
            tail = stderr.read_text(errors="replace")[-3000:]
            tail += stdout.read_text(errors="replace")[-3000:]
            raise AssertionError(f"{name}: exit={code}, timeout={timed_out}\n{tail}")
        return code, stdout, stderr

    def versions(self, tools):
        self.record["tool_versions"] = {}
        for tool, option in tools:
            _, out, err = self.run([tool, option], "version-" + tool)
            text = out.read_text(errors="replace") + err.read_text(errors="replace")
            self.record["tool_versions"][tool] = text[:2000]
        self.save()

    def compared(self, name, count, **details):
        if count <= 0:
            raise AssertionError(f"{name}: no comparisons executed")
        self.record["comparisons"].append(
            {"name": name, "count": count, **details})
        self.save()

    def artifact(self, path):
        path = Path(path)
        self.record.setdefault("artifacts", {})[self.relative(path)] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        self.save()

    def finish(self, error=None):
        self.record.update(ended_at=stamp(), exit_code=0 if error is None else 1,
                           status="passed" if error is None else "failed")
        if error is not None:
            self.record["failure"] = str(error)
        current = hashes(self.root)
        self.record["sources_unchanged"] = current == self.record["source_sha256"]
        if not self.record["sources_unchanged"]:
            self.record.update(status="failed", exit_code=1,
                               failure="verification inputs changed during execution")
        self.save()
        self.finish_called = True
        return self.relative(self.path / "receipt.json")
