#!/usr/bin/env python3
"""BILLET-F3 independent acceptance entry point. Python standard library only."""
import argparse
import collections
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys
import time
import traceback
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import (FIXTURES, INPUTS, OUTPUTS, Floating, fixture_analysis, pack, quantize,
                   response, unpack)
from workloads import (Scenario, arithmetic, compatibility, defect_workload,
                       directed, fir_fixture, matrix, stream)

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ("behavior", "numerical", "defects", "synthesis", "portable")
CONFIGS = [(t, l, c) for t in (1, 8, 16, 32, 64)
           for l in (1, 2, 4) for c in (1, 2, 3, 4)]
EXTENDED = ((1, 1, 1), (1, 4, 4), (8, 2, 2), (16, 4, 1), (32, 1, 2), (64, 4, 4))
SELECTORS = ("rtl/*.sv", "design/*.md", "tests/*", "billet_f2_reference/rtl/*.sv")


def relative(path):
    return Path(path).relative_to(ROOT).as_posix()


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n")


def identity():
    files = []
    for selector in SELECTORS:
        found = sorted(p for p in ROOT.glob(selector) if p.is_file())
        if not found:
            raise AssertionError(f"empty required selector: {selector}")
        files.extend(found)
    return {relative(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class ToolFailure(RuntimeError):
    pass


class ScoreboardMismatch(RuntimeError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__(json.dumps(detail, sort_keys=True))


class Campaign:
    def __init__(self, check, folder=None, repair_of=None):
        self.check = check
        if folder is None:
            parent = ROOT/"evidence"/check
            parent.mkdir(parents=True, exist_ok=True)
            folder = parent/(datetime.datetime.now(datetime.timezone.utc).strftime(
                "run-%Y%m%dT%H%M%S-")+uuid.uuid4().hex[:10])
        folder.mkdir(parents=True, exist_ok=False)
        self.folder = folder
        self.started = time.time()
        self.hashes = identity()
        self.records = []
        self.commands = []
        self.cache = {}
        self.repair_of = repair_of
        self.versions = {}
        snapshot = folder/"sources"
        for name in self.hashes:
            dest = snapshot/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, dest)
        self.result = dict(check=check, source_hashes=self.hashes,
                           source_snapshot=relative(snapshot), records=self.records,
                           commands=self.commands, thresholds=dict(
                               time_max=0.0004, time_rms=0.0001,
                               response_complex_max=0.01, decay_peak_lsb=1))

    def prerequisite(self, synth=False):
        names = ("python3", "iverilog", "vvp") + (("yosys",) if synth else ())
        for name in names:
            if not shutil.which(name):
                raise ToolFailure("missing required prerequisite: "+name)
        for name, args in (("python3", ["--version"]), ("iverilog", ["-V"]),
                           ("vvp", ["-V"]), ("yosys", ["-V"])):
            if name in names:
                rec = self.run([name]+args, self.folder/("version-"+name))
                self.versions[name] = (
                    (ROOT/rec["stdout"]).read_text()+
                    (ROOT/rec["stderr"]).read_text())
        self.result["tool_versions"] = self.versions

    def run(self, argv, dest, allow_failure=False):
        dest.mkdir(parents=True, exist_ok=False)
        start = time.time()
        rec = dict(argv=[str(a) for a in argv], cwd=".", start_time=start,
                   timeout_seconds=86400, source_hashes=self.hashes,
                   stdout=relative(dest/"stdout.txt"), stderr=relative(dest/"stderr.txt"))
        self.commands.append(relative(dest/"receipt.json"))
        code, timeout = None, False
        try:
            with (dest/"stdout.txt").open("wb") as out, (dest/"stderr.txt").open("wb") as err:
                try:
                    child = subprocess.run(rec["argv"], cwd=ROOT, stdout=out, stderr=err,
                                           timeout=86400, check=False)
                    code = child.returncode
                except subprocess.TimeoutExpired:
                    timeout = True
        finally:
            rec.update(end_time=time.time(), exit_code=code, timeout=timeout)
            dump(dest/"receipt.json", rec)
        if timeout or code != 0:
            if not allow_failure:
                raise ToolFailure(f"tool execution failed: {relative(dest/'receipt.json')}")
        return rec

    def compile(self, config, rtl=None):
        rtl = Path(rtl) if rtl is not None else ROOT/"rtl"
        key = (config, str(rtl))
        if key in self.cache:
            return self.cache[key]
        tag = "-".join(map(str, config))+"-"+str(len(self.cache))
        dest = self.folder/("compile-"+tag)
        dest.mkdir()
        # Parent dependencies are byte-identical and are checked independently below.
        parent = dest/"parent.sv"
        parent.write_text((ROOT/"billet_f2_reference/rtl/billet_f2_top.sv").read_text().
                          replace("module billet_f2_top", "module parent_billet_f2_top", 1))
        executable = dest/"sim.vvp"
        argv = ["iverilog", "-g2012", "-Wall", "-s", "bridge",
                "-Pbridge.TAP_COUNT="+str(config[0]),
                "-Pbridge.MAC_LANES="+str(config[1]),
                "-Pbridge.SOS_COUNT="+str(config[2]), "-o", relative(executable)]
        argv += [relative(p) for p in sorted(rtl.glob("*.sv"))]
        argv += ["tests/bridge.sv", relative(parent)]
        self.run(argv, dest/"compile")
        self.cache[key] = executable
        return executable

    def simulate(self, name, config, workload, seed=11, compat=False,
                 rtl=None, reject=False):
        dest = self.folder/name
        dest.mkdir()
        scenario = Scenario(dest, config, seed)
        try:
            workload(scenario)
        finally:
            ledger = scenario.close()
            dump(dest/"stimulus.json", ledger)
        executable = self.compile(config, rtl)
        self.run(["vvp", relative(executable),
                  "+vectors="+relative(dest/"vectors.hex"),
                  "+trace="+relative(dest/"trace.hex"), "+compat="+str(int(compat))],
                 dest/"simulation")
        rec = self.run(["python3", "tests/verify.py", "--compare",
                        relative(dest/"expected.bin"), relative(dest/"trace.hex"),
                        relative(dest/"comparison.json"), relative(dest/"measured.bin"),
                        relative(dest/"vectors.hex")],
                       dest/"scoreboard", allow_failure=True)
        comparison_path = dest/"comparison.json"
        if not comparison_path.exists():
            raise ToolFailure("scoreboard did not produce a structured result")
        comparison = json.loads(comparison_path.read_text())
        if reject:
            if rec["timeout"] or rec["exit_code"] != 1 or comparison.get(
                    "failure_kind") != "pin_mismatch":
                raise AssertionError("mutant not rejected by ordinary pin scoreboard: "+
                                     relative(comparison_path))
            ledger["stimulus_expected_counts"] = {
                k: ledger.pop(k) for k in ("accepted", "retired", "aborted", "cycles")}
        elif rec["exit_code"] != 0:
            if comparison.get("failure_kind") == "pin_mismatch":
                raise ScoreboardMismatch(dict(evidence=relative(comparison_path),
                                               **comparison))
            raise ToolFailure("scoreboard failure: "+relative(comparison_path))
        else:
            assert comparison["cycles"] == ledger["cycles"]
            assert comparison["retired"] == ledger["retired"]
            assert comparison["accepted"] == ledger["accepted"]
            assert comparison["aborted"] == ledger["aborted"]
            assert comparison["mode_channel_counts"] == ledger["mode_channel_counts"]
            for label, observed in comparison["visibility_latencies"].items():
                assert observed["minimum"] == observed["maximum"] == (
                    ledger["expected_edge_latencies"][label]), (name, label, observed)
            assert comparison["minimum_accept_intervals"] == ledger["minimum_accept_intervals"]
        ledger.update(name=name, comparison=relative(comparison_path),
                      execution=relative(dest/"simulation/receipt.json"),
                      outcome="detected" if reject else "pass",
                      first_mismatch=comparison.get("first_mismatch"))
        ledger["observed_compared_cycles"] = comparison["cycles"]
        ledger["observed_compared_retirements"] = comparison["retired"]
        ledger["observed_visibility_latencies"] = comparison["visibility_latencies"]
        ledger["observed_minimum_accept_intervals"] = comparison["minimum_accept_intervals"]
        self.records.append(ledger)
        return scenario, dest

    def finish(self, failure=None, publish=True):
        self.result.update(start_time=self.started, end_time=time.time(),
                           outcome="fail" if failure else "pass",
                           failure=failure, repair_of=self.repair_of)
        self.result["totals"] = {
            key: sum(r.get(key, 0) for r in self.records)
            for key in ("accepted", "retired", "aborted", "cycles")}
        inventory = []
        for name in self.hashes:
            p = ROOT/name
            inventory.append(dict(path=name, bytes=p.stat().st_size,
                                  lines=len(p.read_bytes().splitlines()),
                                  modules=len(re.findall(rb"(?m)^\s*module\s+", p.read_bytes()))
                                  if p.suffix == ".sv" else 0))
        self.result["source_inventory"] = inventory
        self.result["source_totals"] = {
            group: dict(files=sum(row["path"].startswith(prefix) for row in inventory),
                        **{key: sum(row[key] for row in inventory
                                    if row["path"].startswith(prefix))
                           for key in ("bytes", "lines", "modules")})
            for group, prefix in (("rtl", "rtl/"), ("parent", "billet_f2_reference/"),
                                  ("tests", "tests/"), ("design", "design/"))}
        self.result["source_unchanged"] = identity() == self.hashes
        if not self.result["source_unchanged"] and failure is None:
            self.result.update(outcome="fail", failure="source changed during execution")
        dump(self.folder/"results.json", self.result)
        dump(self.folder/"receipt.json", dict(
            argv=["python3", "tests/verify.py"]+sys.argv[1:], cwd=".",
            source_hashes=self.hashes, tool_versions=self.versions,
            start_time=self.started, end_time=time.time(), timeout_seconds=86400,
            timeout=False, exit_code=1 if self.result["outcome"] == "fail" else 0,
            raw_child_receipts=self.commands, repair_of=self.repair_of,
            results=relative(self.folder/"results.json")))
        if publish:
            dump(ROOT/"evidence"/self.check/"results.json", self.result | {
                "receipt": relative(self.folder/"receipt.json")})
        print(json.dumps(dict(check=self.check, outcome=self.result["outcome"],
                              receipt=relative(self.folder/"receipt.json"))))


def compare(expected, trace, report, measured, vectors):
    cycles = retired = accepted = aborted = 0
    mode, count = 1, 0
    pending = previous_accept = None
    mode_counts = collections.Counter()
    latencies, intervals = {}, {}
    failure = None
    try:
        with (expected.open("rb") as exp, trace.open() as actual,
              measured.open("wb") as obs, vectors.open() as stimulus):
            while True:
                item, line, vector = exp.read(9), actual.readline(), stimulus.readline()
                if not item and not line and not vector:
                    break
                if len(item) != 9 or not line or not vector:
                    failure = dict(failure_kind="trace_length", cycle=cycles)
                    break
                pre, post, retire = struct.unpack("<IIB", item)
                fields = line.split()
                if len(fields) != 2 or any(
                        not re.fullmatch("[0-9a-fA-F]+", x) for x in fields):
                    failure = dict(failure_kind="active_xz_or_malformed",
                                   cycle=cycles, raw=line.strip())
                    break
                observed = list(map(lambda x: int(x, 16), fields))
                for stage, want, got in zip(("pre", "post"), (pre, post), observed):
                    if want != got:
                        failure = dict(failure_kind="pin_mismatch", first_mismatch=dict(
                            cycle=cycles, stage=stage,
                            expected=unpack(OUTPUTS, want), observed=unpack(OUTPUTS, got)))
                        break
                if failure:
                    break
                request = unpack(INPUTS, int(vector, 16))
                before, after = (unpack(OUTPUTS, pins) for pins in observed)
                if not request["rst_n"]:
                    aborted += pending is not None
                    pending = previous_accept = None
                    mode, count = 1, 0
                else:
                    if before["m_valid"] and request["m_ready"]:
                        assert pending is not None
                        pending = None
                    if request["chain_valid"] and before["chain_ready"]:
                        mode, count = request["chain_mode"], request["chain_sections"]
                        previous_accept = None
                    if request["s_valid"] and before["s_ready"]:
                        assert pending is None
                        accepted += 1
                        label = f"{mode}:{count}"
                        mode_counts[f"{label}:{request['s_channel']}"] += 1
                        if previous_accept is not None:
                            distance = cycles-previous_accept
                            intervals[label] = min(distance, intervals.get(label, distance))
                        previous_accept = cycles
                        pending = [cycles, label, False]
                    if after["m_valid"]:
                        assert pending is not None
                        if not pending[2]:
                            distance = cycles-pending[0]
                            row = latencies.setdefault(pending[1], dict(
                                minimum=distance, maximum=distance, compared=0))
                            row.update(minimum=min(row["minimum"], distance),
                                       maximum=max(row["maximum"], distance),
                                       compared=row["compared"]+1)
                            pending[2] = True
                if retire:
                    pins = unpack(OUTPUTS, observed[0])
                    obs.write(struct.pack("<Bh", pins["m_channel"], pins["m_data"]))
                    retired += 1
                cycles += 1
    except (OSError, ValueError) as exc:
        failure = dict(failure_kind="scoreboard_io", error=str(exc))
    result = dict(outcome="fail" if failure else "pass", cycles=cycles,
                  accepted=accepted, retired=retired, aborted=aborted,
                  mode_channel_counts=dict(mode_counts), visibility_latencies=latencies,
                  minimum_accept_intervals=intervals, **(failure or {}))
    dump(report, result)
    print(json.dumps(result))
    return int(failure is not None)


def parent_integrity():
    for p in sorted((ROOT/"billet_f2_reference/rtl").glob("*.sv")):
        assert p.read_bytes() == (ROOT/"rtl"/p.name).read_bytes(), p.name


def rounding_units(c):
    records = []
    for width, shift, bits in ((53, 16, 32), (32, 8, 16)):
        dest = c.folder/f"round-{width}-{shift}-{bits}"
        dest.mkdir()
        values = set()
        half = 1 << (shift-1)
        for q in (-9, -4, -3, -2, -1, 0, 1, 2, 3, 4, 9,
                  -(1 << (bits-1))-1, -(1 << (bits-1)), (1 << (bits-1))-1,
                  1 << (bits-1)):
            for r in (0, half-1, half, half+1, (1 << shift)-1):
                v = q*(1 << shift)+r
                if -(1 << (width-1)) <= v < (1 << (width-1)):
                    values.add(v)
        values.update((-(1 << (width-1)), (1 << (width-1))-1))
        rng = random.Random(97)
        values.update(rng.randrange(-(1 << (width-1)), 1 << (width-1))
                      for _ in range(1000))
        values = sorted(values)
        (dest/"vectors.hex").write_text("".join(
            f"{v & ((1 << width)-1):x}\n" for v in values))
        exe = dest/"sim.vvp"
        c.run(["iverilog", "-g2012", "-Wall", "-s", "round_bridge",
               "-Pround_bridge.IN_W="+str(width), "-Pround_bridge.SHIFT="+str(shift),
               "-Pround_bridge.OUT_W="+str(bits), "-o", relative(exe),
               "rtl/billet_f3_round_sat.sv", "tests/round_bridge.sv"], dest/"compile")
        c.run(["vvp", relative(exe), "+vectors="+relative(dest/"vectors.hex"),
               "+trace="+relative(dest/"trace.hex")], dest/"simulation")
        lines = (dest/"trace.hex").read_text().splitlines()
        assert len(lines) == len(values)
        cov = collections.Counter()
        for v, line in zip(values, lines):
            assert re.fullmatch("[0-9a-fA-F]+", line), "rounding X/Z"
            got = int(line, 16)
            if got & (1 << (bits-1)):
                got -= 1 << bits
            want = quantize(v, shift, bits, cov, "unit")
            assert got == want, (v, want, got)
        for sign in ("positive", "negative"):
            for parity in ("even", "odd"):
                assert cov[f"unit_tie_{sign}_{parity}"] > 0
            assert cov[f"unit_{sign}_saturation"] > 0
        assert cov["unit_exact_extreme"] > 0
        record = dict(width=width, shift=shift, output_bits=bits,
                      compared=len(values), observed_coverage=dict(cov))
        dump(dest/"assertions.json", record)
        records.append(record)
    c.result["rounding_units"] = records


def illegal_parameters(c):
    rows = []
    for parameter, values in (("TAP_COUNT", (0, -1, 3, 65)),
                              ("MAC_LANES", (0, -1, 3)),
                              ("SOS_COUNT", (0, -1, 5))):
        for value in values:
            dest = c.folder/f"illegal-{parameter}-{value}"
            dest.mkdir()
            exe = dest/"sim.vvp"
            c.run(["iverilog", "-g2012", "-Wall", "-s", "billet_f3_top",
                   f"-Pbillet_f3_top.{parameter}={value}", "-o", relative(exe)] +
                  [relative(p) for p in sorted((ROOT/"rtl").glob("*.sv"))],
                  dest/"compile")
            result = c.run(["vvp", relative(exe)], dest/"simulation", allow_failure=True)
            text = (ROOT/result["stdout"]).read_text()+(ROOT/result["stderr"]).read_text()
            assert result["exit_code"] != 0 and not result["timeout"]
            assert "PARAMETER ERROR" in text
            rows.append(dict(parameter=parameter, value=value,
                             explicit_diagnostic=True, receipt=relative(dest/"simulation/receipt.json")))
    c.result["illegal_parameters"] = rows


def behavior(c, bounded=False):
    parent_integrity()
    configs = [(8, 2, 2)] if bounded else CONFIGS
    for cfg in configs:
        scenario, _ = c.simulate("matrix-"+"-".join(map(str, cfg)), cfg, matrix)
        matrix_coverage(scenario, 64, False)
    parents = [(8, 2)] if bounded else [(t, l) for t in (1, 8, 16, 32, 64)
                                      for l in (1, 2, 4)]
    for t, l in parents:
        c.simulate(f"parent-{t}-{l}", (t, l, 2), compatibility, compat=True)
    if not bounded:
        for cfg in EXTENDED:
            for seed in (11, 29, 97):
                scenario, _ = c.simulate(
                    "extended-"+"-".join(map(str, cfg))+f"-{seed}", cfg,
                    lambda s: matrix(s, 1024, True), seed)
                matrix_coverage(scenario, 1024, True)
        for cfg in ((1, 4, 4), (8, 2, 2), (32, 1, 2)):
            scenario, _ = c.simulate("directed-"+"-".join(map(str, cfg)), cfg, directed)
            directed_coverage(scenario)
        arith, _ = c.simulate("arithmetic", (8, 2, 4), arithmetic)
        cov = arith.p.coverage
        for boundary in ("fir", "state", "output"):
            for sign in ("positive", "negative"):
                for parity in ("odd", "even"):
                    assert cov[f"{boundary}_tie_{sign}_{parity}"] > 0
                assert cov[f"{boundary}_{sign}_saturation"] > 0
        assert cov["fir_accumulator_cancellation"] > 0
        assert cov["state_accumulator_cancellation"] > 0
        assert cov["fir_exact_extreme"] > 0 and cov["output_exact_extreme"] > 0
        rounding_units(c)
        illegal_parameters(c)


def matrix_coverage(s, samples, extended):
    for mode in range(5):
        counts = [0] if mode < 2 else (
            [s.config[2]] if extended else range(1, s.config[2]+1))
        for count in counts:
            for channel in range(4):
                assert s.p.mode_counts[f"{mode}:{count}:{channel}"] >= samples
            g = (s.config[0]+s.config[1]-1)//s.config[1]
            expected = (1, g+1, count+1, g+count+2, g+count+2)[mode]
            assert s.p.intervals[f"{mode}:{count}"] == expected
        assert s.stall_runs.get(mode, 0) >= 32
        assert s.p.coverage[f"retire_accept_mode_{mode}"] > 0


def directed_coverage(s):
    required = (
        "reset_busy", "reset_held", "fir_duplicate", "sos_duplicate",
        "cfg_blocked", "sos_cfg_blocked", "commit_blocked", "sos_commit_blocked",
        "chain_blocked", "fir_bank_0", "fir_bank_1", "sos_bank_0", "sos_bank_1",
        "priority_chain_over_sos_commit", "priority_chain_over_commit",
        "priority_sos_commit_over_commit", "simultaneous_staging_writes",
        "update_suppresses_sample_and_writes", "chain_clears_nonzero_sos",
        "chain_retains_nonzero_fir_history", "commit_clears_nonzero_sos",
        "commit_retains_nonzero_fir_history", "cfg_during_held", "sos_cfg_during_held",
    )
    for name in required:
        assert s.p.coverage[name] > 0, (s.config, "uncovered requirement", name)
    if s.config[2] > 1:
        for name in ("cfg_during_busy", "sos_cfg_during_busy"):
            assert s.p.coverage[name] > 0, (s.config, name)


def measured(dest):
    data = (dest/"measured.bin").read_bytes()
    assert len(data) % 3 == 0
    return list(struct.iter_unpack("<Bh", data))


def fixture_sequences():
    rows = [(f"{name}-{n}", [codes]*n) for name, codes in FIXTURES.items()
            for n in range(1, 5)]
    rows += [("LP50-HP50", [FIXTURES["LP50"], FIXTURES["HP50"]]),
             ("HP50-LP50", [FIXTURES["HP50"], FIXTURES["LP50"]])]
    return rows


def numerical_case(c, name, sections, mode, long_run=False):
    cfg = (32, 1, 4)
    fir = fir_fixture(32, "average")
    physical = sections+[FIXTURES["LP50"]]*(4-len(sections))
    inputs = []
    residual = {}
    categories = []

    def init(s):
        s.reset()
        s.program(fir=fir, sos=physical)
        s.chain(mode, len(sections))

    def impulse(s):
        init(s)
        values = [(ch, 16384 if i == 0 else 0) for i in range(1024)
                  for ch in range(4)]
        inputs.extend(values)
        s.samples(values)

    def timedomain(s):
        init(s)
        rng = random.Random(97)
        stimuli = (
            ("impulse", [8192]+[0]*511),
            ("positive_step_dc", [8192]*512),
            ("negative_step_dc", [-8192]*512),
            ("near_nyquist_tone", [
                round(8192*math.sin(2*math.pi*.46875*i)) for i in range(512)]),
            ("low_frequency_tone", [
                round(8192*math.sin(2*math.pi*.03125*i)) for i in range(512)]),
            ("midband_tone", [
                round(8192*math.sin(2*math.pi*.25*i)) for i in range(512)]),
            ("bounded_random", [rng.randint(-8192, 8192) for _ in range(1024)]),
        )
        for label, codes in stimuli:
            values = [(ch, x) for x in codes for ch in range(4)]
            categories.append(dict(stimulus=label, start=len(inputs), count=len(values)))
            inputs.extend(values)
            s.samples(values)

    def long(s):
        init(s)
        excitation = [(ch, 8192 if i % 2 == 0 else -4096)
                      for i in range(64) for ch in range(4)]
        zeros = [(ch, 0) for _ in range(32768) for ch in range(4)]
        rng = random.Random(29)
        randoms = [(ch, rng.randint(-8192, 8192))
                   for _ in range(32768) for ch in range(4)]
        s.samples(excitation)
        s.samples(zeros)
        residual["state_peak_code"] = max(
            abs(v) for states in s.p.math.state for sec in states for v in sec)
        residual["per_channel_state_codes"] = [
            [list(section) for section in channel] for channel in s.p.math.state]
        s.samples(randoms)
        inputs.extend(excitation+zeros+randoms)

    if long_run:
        _, dest = c.simulate("decay-random-"+name, cfg, long, 29)
        actual = measured(dest)
        assert len(actual) == len(inputs)
        start, end = (64+32768-4096)*4, (64+32768)*4
        peaks = [max(abs(x) for ch, x in actual[start:end] if ch == channel)
                 for channel in range(4)]
        assert max(peaks) <= 1, (name, peaks)
        residual.update(name=name, final_4096_output_peaks_lsb=peaks,
                        zero_inputs_per_channel=32768,
                        random_inputs_per_channel=32768,
                        compared_outputs=len(actual), execution=relative(dest))
        return residual
    _, dest = c.simulate(f"response-{name}-mode{mode}", cfg, impulse)
    actual = measured(dest)
    assert len(actual) == 4096
    channels = [[x/16384 for ch, x in actual if ch == channel] for channel in range(4)]
    assert all(row == channels[0] for row in channels)
    impulse_float = Floating(fir, sections, mode)
    impulse_errors = []
    for (ch, v), (got_ch, x) in zip(inputs, actual):
        assert ch == got_ch
        impulse_errors.append(x/32768-impulse_float.sample(v, ch))
    points = []
    for k in range(257):
        omega = math.pi*k/256
        import cmath
        z = cmath.exp(-1j*omega)
        power = 1+0j
        h = 0j
        for x in channels[0]:
            h += x*power
            power *= z
        target = response(fir, sections, mode, omega)
        points.append(dict(bin=k, omega=omega, measured_real=h.real,
                           measured_imag=h.imag, analytic_real=target.real,
                           analytic_imag=target.imag, gain=abs(h),
                           phase_radians=cmath.phase(h) if abs(h) > 1e-6 else None,
                           complex_error=abs(h-target)))
    response_max = max(p["complex_error"] for p in points)
    response_rms = math.sqrt(sum(p["complex_error"]**2 for p in points)/257)
    assert response_max <= .01, (name, mode, response_max)
    dump(dest/"frequency.json", dict(points=points, max_complex_error=response_max,
                                    rms_complex_error=response_rms,
                                    impulse_time_max_error=max(map(abs, impulse_errors))))
    inputs.clear()
    _, time_dest = c.simulate(f"error-{name}-mode{mode}", cfg, timedomain)
    actual = measured(time_dest)
    assert len(actual) == len(inputs)
    oracle = Floating(fir, sections, mode)
    errors = []
    for (ch, v), (got_ch, x) in zip(inputs, actual):
        assert ch == got_ch
        target = oracle.sample(v, ch)
        assert abs(target) < 1, "nonclipping metric stimulus unexpectedly clips"
        errors.append(x/32768-target)
    max_error = max(map(abs, errors))
    rms = math.sqrt(sum(e*e for e in errors)/len(errors))
    assert max_error <= .0004 and rms <= .0001, (name, mode, max_error, rms)
    for category in categories:
        start, count = category["start"], category["count"]
        subset = errors[start:start+count]
        maximum = max(map(abs, subset))
        category_rms = math.sqrt(sum(e*e for e in subset)/len(subset))
        assert maximum <= .0004 and category_rms <= .0001
        category.update(maximum_normalized_error=maximum,
                        rms_normalized_error=category_rms)
    record = dict(name=name, mode=mode, sections=len(sections),
                  compared_time_samples=len(errors), maximum_normalized_error=max_error,
                  rms_normalized_error=rms, max_complex_response_error=response_max,
                  rms_complex_response_error=response_rms,
                  impulse_samples_per_channel=1024, frequency_points=257,
                  fir_coefficients=fir, sos_coefficients=sections,
                  time_stimuli=categories,
                  frequency_evidence=relative(dest/"frequency.json"),
                  time_evidence=relative(time_dest),
                  representative_frequency_points=[points[k] for k in (0, 8, 32, 64, 128, 240, 256)])
    return record


def numerical(c, bounded=False):
    c.result["independent_analytic_fixtures"] = fixture_analysis()
    sequences = fixture_sequences()
    if bounded:
        sequences = sequences[:1]
    rows, decay = [], []
    c.result.update(numerical_rows=rows, decay_rows=decay)
    for name, sections in sequences:
        if not bounded:
            decay.append(numerical_case(c, name, sections, 2, True))
        for mode in ((2,) if bounded else (2, 3, 4)):
            rows.append(numerical_case(c, name, sections, mode))


MUTATIONS = {
    "feedback_sign": ("billet_f3_sos_engine.sv",
                      "extended_a1 - extended_a2", "extended_a1 + extended_a2"),
    "state_update": ("billet_f3_sos_engine.sv",
                     "y2[state_index] <= y1[state_index];",
                     "y2[state_index] <= quantized_y;"),
    "coefficient_bank": ("billet_f3_sos_engine.sv",
                         "bank <= active_banks[s_channel];",
                         "bank <= !active_banks[s_channel];"),
    "chain_order": ("billet_f3_top.sv",
                    "active_mode <= chain_mode;",
                    "active_mode <= (chain_mode == 3) ? 4 : "
                    "(chain_mode == 4) ? 3 : chain_mode;"),
}


def defects(c):
    rows = []
    c.result["mutations"] = rows
    for kind, (file, old, new) in MUTATIONS.items():
        cfg = (8, 2, 2)
        workload = lambda s, k=kind: defect_workload(s, k)
        _, baseline = c.simulate("pristine-"+kind, cfg, workload, 29)
        tree = c.folder/("mutant-"+kind)
        shutil.copytree(ROOT/"rtl", tree)
        source = tree/file
        text = source.read_text()
        assert text.count(old) == 1, kind
        source.write_text(text.replace(old, new, 1))
        hashes = {relative(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(tree.glob("*.sv"))}
        dump(tree/"mutation.json", dict(kind=kind, file=file, old=old, new=new,
                                        source_hashes=hashes))
        _, mutant = c.simulate("rejected-"+kind, cfg, workload, 29,
                               rtl=tree, reject=True)
        for name in ("vectors.hex", "expected.bin"):
            assert (baseline/name).read_bytes() == (mutant/name).read_bytes()
        comparison = json.loads((mutant/"comparison.json").read_text())
        rows.append(dict(kind=kind, pristine=relative(baseline),
                         mutant=relative(mutant), source_snapshot=relative(tree),
                         source_hashes=hashes, detected=True,
                         first_mismatch=comparison["first_mismatch"],
                         comparator_exit=1))
    assert len(rows) == 4 and all(row["detected"] for row in rows)


def hierarchy_assertions(path, config):
    data = json.loads(path.read_text())
    modules = data["modules"]
    visited = []
    storage = collections.Counter()
    connected = {}
    bad_cells = []

    def walk(name, prefix):
        mod = modules[name]
        assert str(mod.get("attributes", {}).get("blackbox", "0")).strip("0") == "", name
        for inst, cell in mod.get("cells", {}).items():
            typ = cell["type"]
            full = prefix+"/"+inst
            if "latch" in typ.lower():
                bad_cells.append(full)
            if typ in modules:
                child = modules[typ]
                base = str(child.get("attributes", {}).get("hdlname", typ)).split()[0].lstrip("\\")
                visited.append((full, base))
                # Every declared child port must be wired at the instance boundary.
                for port, desc in child.get("ports", {}).items():
                    assert port in cell["connections"], (full, port)
                    assert len(cell["connections"][port]) == len(desc["bits"]), (full, port)
                connected[full] = sorted(cell["connections"])
                for port in ("s_data", "s_valid", "s_ready", "m_data", "m_valid",
                             "m_ready", "coefficients", "coeff_lanes", "sample_lanes",
                             "active_banks", "staging_complete"):
                    if port in child.get("ports", {}):
                        assert any(isinstance(bit, int) for bit in
                                   cell["connections"][port]), (full, port, "constant-only")
                walk(typ, full)
            elif typ.startswith("$"):
                if "dff" in typ.lower():
                    storage[prefix] += len(cell.get("connections", {}).get("Q", []))
            else:
                raise AssertionError("unresolved module "+typ)
    walk("billet_f3_top", "billet_f3_top")
    assert not bad_cells, bad_cells
    for suffix, base in (("u_fir", "billet_f2_top"),
                         ("u_sos", "billet_f3_sos_engine")):
        matches = [(p, b) for p, b in visited if p.endswith(suffix) and b == base]
        assert len(matches) == 1, (suffix, matches)
    fir_bank = [p for p, b in visited if b == "billet_f2_coeff_bank"]
    sos_bank = [p for p, b in visited if b == "billet_f3_sos_coeff_bank"]
    history = [p for p, b in visited if b == "billet_f2_history"]
    assert len(fir_bank) == len(sos_bank) == len(history) == 1
    assert storage[fir_bank[0]] >= 128*config[0]
    assert storage[sos_bank[0]] >= 720*config[2]
    assert storage[history[0]] >= 64*config[0]
    sos_path = next(p for p, b in visited if b == "billet_f3_sos_engine")
    # The first SOS's x histories contain only 16 independent promoted input bits.
    assert storage[sos_path] >= 512*config[2]-128
    for p, b in visited:
        if b in ("billet_f2_top", "billet_f3_sos_engine"):
            required = ("s_data", "s_valid", "s_ready", "m_data", "m_valid", "m_ready")
            assert all(name in connected[p] for name in required)
            parent_path, instance = p.rsplit("/", 1)
            assert parent_path and instance
    return dict(zero_latches=True, zero_unresolved_modules=True,
                instance_hierarchy=visited, sequential_bits_by_instance=dict(storage),
                connected_ports=connected)


def synthesis(c, bounded=False):
    configs = [(32, 1, 2)] if bounded else [
        (32, 1, 2), (1, 1, 1), (1, 4, 4), (8, 2, 3), (64, 4, 4)]
    rows = []
    c.result["synthesis"] = rows
    for cfg in configs:
        dest = c.folder/("synth-"+"-".join(map(str, cfg)))
        dest.mkdir()
        pre, net = dest/"hierarchy.json", dest/"netlist.json"
        script = (
            "read_verilog -sv "+" ".join(relative(p) for p in sorted((ROOT/"rtl").glob("*.sv")))+"\n"
            f"hierarchy -check -top billet_f3_top -chparam TAP_COUNT {cfg[0]} "
            f"-chparam MAC_LANES {cfg[1]} -chparam SOS_COUNT {cfg[2]}\n"
            "proc\nopt\ncheck -assert\n"
            f"write_json {relative(pre)}\n"
            f"tee -o {relative(dest/'pre-stat.json')} stat -json\n"
            "synth -top billet_f3_top\ncheck -assert\n"
            f"write_json {relative(net)}\n"
            f"tee -o {relative(dest/'stat.json')} stat -json\n")
        (dest/"synth.ys").write_text(script)
        c.run(["yosys", "-Q", "-T", "-s", relative(dest/"synth.ys")], dest/"execution")
        assertions = hierarchy_assertions(pre, cfg)
        netlist = json.loads(net.read_text())
        counts = collections.Counter()
        for name, mod in netlist["modules"].items():
            assert not mod.get("attributes", {}).get("blackbox"), name
            for cell in mod.get("cells", {}).values():
                assert "latch" not in cell["type"].lower(), cell["type"]
                assert cell["type"] in netlist["modules"] or cell["type"].startswith("$_")
                counts[cell["type"]] += 1
        assertions.update(config=cfg, generic_cell_counts=dict(counts),
                          generic_module_definitions=len(netlist["modules"]),
                          physical_claims=False)
        dump(dest/"assertions.json", assertions)
        rows.append(dict(config=cfg, assertions=relative(dest/"assertions.json"),
                         execution=relative(dest/"execution/receipt.json"),
                         statistics=relative(dest/"stat.json"),
                         generic_cells=sum(counts.values()), outcome="pass"))


def audit():
    forbidden = (
        rb"/data/", rb"/home/", rb"/root/", rb"/Users/",
        rb"gh[pousr]_[A-Za-z0-9]{20,}", rb"github_pat_[A-Za-z0-9_]{20,}",
        rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
    rows = []
    for name in identity():
        text = (ROOT/name).read_bytes()
        # Regex literals in this auditor are not deployed secrets or private paths.
        if name != "tests/verify.py":
            for pattern in forbidden:
                assert re.search(pattern, text) is None, ("nonportable/sensitive source", name)
        assert ".vulcan/" not in name and ".tooling/" not in name
        rows.append(name)
    return rows


def portable(c):
    c.result["audited_sources"] = audit()
    parent_integrity()
    clean = c.folder/"clean"
    clean.mkdir()
    for name in c.hashes:
        dest = clean/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, dest)
    assert not (clean/"evidence").exists()
    # Run the identical owned entry point, in the fresh delivery root.
    argv = ["python3", "tests/verify.py", "--portable-child"]
    dest = c.folder/"reproduction"
    dest.mkdir()
    start = time.time()
    rec = dict(argv=argv, cwd=relative(clean), source_hashes=c.hashes,
               start_time=start, timeout_seconds=86400,
               stdout=relative(dest/"stdout.txt"), stderr=relative(dest/"stderr.txt"))
    with (dest/"stdout.txt").open("wb") as out, (dest/"stderr.txt").open("wb") as err:
        try:
            result = subprocess.run(argv, cwd=clean, stdout=out, stderr=err,
                                    timeout=86400, check=False)
            rec.update(exit_code=result.returncode, timeout=False)
        except subprocess.TimeoutExpired:
            rec.update(exit_code=None, timeout=True)
    rec["end_time"] = time.time()
    dump(dest/"receipt.json", rec)
    c.commands.append(relative(dest/"receipt.json"))
    assert rec["exit_code"] == 0 and not rec["timeout"], relative(dest/"receipt.json")
    result = json.loads((clean/"evidence/portable-child/results.json").read_text())
    assert result["outcome"] == "pass"
    assert result["source_hashes"] == c.hashes
    c.result["clean_reproduction"] = dict(root=relative(clean), results=result,
                                        receipt=relative(dest/"receipt.json"))


def execute(check, probe=False, repair_of=None, portable_child=False):
    folder = None
    if probe:
        base = ROOT/"evidence/preparation"
        base.mkdir(parents=True, exist_ok=True)
        folder = base/("verifier-smoke-"+uuid.uuid4().hex[:12])
    c = Campaign(check, folder, repair_of)
    failure = None
    try:
        c.prerequisite(synth=check in ("synthesis", "portable", "portable-child")
                       or probe == "synthesis")
        if probe == "synthesis":
            synthesis(c, True)
        elif probe == "numerical":
            c.result["independent_analytic_fixtures"] = fixture_analysis()
            c.result["numerical_rows"] = [
                numerical_case(c, "HP50-4", [FIXTURES["HP50"]]*4, 4),
                numerical_case(c, "LP75-4", [FIXTURES["LP75"]]*4, 3)]
            c.result["decay_rows"] = [
                numerical_case(c, "LP75-4", [FIXTURES["LP75"]]*4, 2, True)]
        elif probe:
            parent_integrity()
            scenario, _ = c.simulate("matrix", (8, 2, 2), matrix)
            matrix_coverage(scenario, 64, False)
            c.simulate("parent", (8, 2, 2), compatibility, compat=True)
            for cfg in ((1, 4, 4), (8, 2, 2), (32, 1, 2)):
                scenario, _ = c.simulate(
                    "directed-"+"-".join(map(str, cfg)), cfg, directed)
                directed_coverage(scenario)
            arith, _ = c.simulate("arithmetic", (8, 2, 4), arithmetic)
            c.result["arithmetic_coverage"] = dict(arith.p.coverage)
            rounding_units(c)
            illegal_parameters(c)
            numerical(c, True)
            defects(c)
        elif portable_child:
            behavior(c, True)
            c.simulate("portable-directed", (8, 2, 2), directed)
            numerical(c, True)
            defects(c)
            synthesis(c, True)
        else:
            globals()[check](c)
    except Exception as exc:
        failure = dict(type=type(exc).__name__, detail=str(exc),
                       traceback=traceback.format_exc())
    finally:
        c.finish(failure, publish=not probe)
    return 1 if c.result["outcome"] != "pass" else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=CHECKS)
    parser.add_argument("--probe", choices=("smoke", "synthesis", "numerical"))
    parser.add_argument("--repair-of")
    parser.add_argument("--portable-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--compare", nargs=5, metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.compare:
        return compare(*(ROOT/p for p in args.compare))
    if args.probe:
        return execute("preparation", args.probe, args.repair_of)
    if args.portable_child:
        return execute("portable-child", portable_child=True)
    if args.check:
        return execute(args.check)
    for check in CHECKS:
        if execute(check):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
