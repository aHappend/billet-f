"""Independent convolution and public transaction/timing contract; no RTL internals."""

import cmath
import hashlib
import json
import math
from fractions import Fraction
from pathlib import Path

LEGAL = (1, 8, 16, 32, 64)
FREQUENCIES = (1, 3, 7, 15)
FLOAT_TOL = 1e-12


def quantize(value):
    return max(-32768, min(32767, round(value * 32768)))


def rounded(acc):
    q, r = divmod(acc, 32768)
    return q + (r > 16384 or (r == 16384 and q % 2 == 1))


def convolve(history, coefficients):
    terms = [x * c for x, c in zip(history, coefficients)]
    acc = sum(terms)
    code = max(-32768, min(32767, rounded(acc)))
    floating = math.fsum((x / 32768) * (c / 32768)
                         for x, c in zip(history, coefficients))
    clipped = min(32767 / 32768, max(-1.0, floating))
    assert abs(code / 32768 - clipped) <= .5 / 32768 + FLOAT_TOL
    if -1 <= floating <= 32767 / 32768:
        assert abs(code / 32768 - floating) <= .5 / 32768 + FLOAT_TOL
    return acc, code, floating, clipped


def bank_document():
    rules = json.loads((Path(__file__).resolve().parent / "data/generation.json").read_text())
    assert rules["schema"] == 1 and rules["legal_taps"] == list(LEGAL)
    result = {"format": "signed Q1.15", "quantization": "nearest ties-even, clamp int16",
              "hash_encoding": "SHA256 of compact JSON coefficient array", "taps": {}}
    for t in LEGAL:
        tri = [Fraction(k + 1, t * (t + 1) // 2) for k in range(t)]
        ideals = {
            "impulse": [Fraction(1)] + [Fraction(0)] * (t - 1),
            "moving_average": [Fraction(1, t)] * t,
            "triangular": tri,
            "alternating_triangular": [v * (-1) ** k for k, v in enumerate(tri)],
        }
        banks = {}
        for name, ideal in ideals.items():
            codes = [quantize(x) for x in ideal]
            banks[name] = {
                "coefficients": codes, "ideal_rational": [str(x) for x in ideal],
                "sha256": hashlib.sha256(json.dumps(codes, separators=(",", ":")).encode()).hexdigest(),
                "l1_quantization_bound": float(sum(abs(x - Fraction(c, 32768))
                                                   for x, c in zip(ideal, codes))),
            }
        stress = [-32768] if t == 1 else [32767, 32767] + [0] * (t - 2)
        banks["stress"] = {
            "coefficients": stress, "ideal_rational": [str(Fraction(c, 32768)) for c in stress],
            "sha256": hashlib.sha256(json.dumps(stress, separators=(",", ":")).encode()).hexdigest(),
            "l1_quantization_bound": 0.0,
        }
        result["taps"][str(t)] = banks
    return result


def response_data(t, banks):
    records = []
    for name, bank in banks.items():
        ideal = [float(Fraction(v)) for v in bank["ideal_rational"]]
        codes = bank["coefficients"]
        for f in FREQUENCIES:
            z = cmath.exp(-2j * math.pi * f / 64)
            h = sum(c / 32768 * z ** k for k, c in enumerate(codes))
            hi = sum(c * z ** k for k, c in enumerate(ideal))
            assert abs(h - hi) <= bank["l1_quantization_bound"] + 1e-12
            # Measure the response with independent real/imaginary time-domain convolutions.
            measured = sum(complex(
                math.fsum(c / 32768 * math.cos(2 * math.pi * f * (n - k) / 64)
                          for k, c in enumerate(codes)),
                math.fsum(c / 32768 * math.sin(2 * math.pi * f * (n - k) / 64)
                          for k, c in enumerate(codes)))
                * cmath.exp(-2j * math.pi * f * n / 64) for n in range(512)) / 512
            assert abs(measured - h) <= 1e-10
            records.append({"taps": t, "bank": name, "frequency": f / 64,
                            "analytical": [h.real, h.imag],
                            "floating_measured": [measured.real, measured.imag],
                            "ideal": [hi.real, hi.imag],
                            "quantization_response_error": abs(h - hi),
                            "coefficient_l1_error_bound": bank["l1_quantization_bound"],
                            "measurement_error": abs(measured - h)})
    return records


class Contract:
    """Whole accepted-sample convolution, plus the specified external edge table."""

    def __init__(self, taps, vectors, expected, outputs):
        self.t = taps
        self.vectors, self.expected, self.outputs = vectors, expected, outputs
        self.cycle = 0
        self.history = [0] * taps
        self.active = [0] * taps
        self.staging = [0] * taps
        self.written = set()
        self.due = None
        self.inflight = None
        self.pending = None
        self.epoch = 0
        self.accepted = self.completed = self.transferred = self.aborted = 0
        self.commits = self.bubbles = self.stalls = self.max_stall = self.stall_run = 0
        self.committed_banks = []
        self.coverage = {}
        self.ties = set()
        self.adjacent = set()
        self.saturations = set()
        self.random_samples = 0
        self.tone_counts = {}
        self.window_accepts = []
        self.window_outputs = []
        self.window_transfers = []
        self.errors = []
        self.unclipped_errors = []
        self.unclipped_only_errors = []
        self.clipping_errors = []
        self.tone_errors = {}
        self.tone_clipping_counts = {}
        self.clipping_count = 0
        self.last_s = None
        self.last_cfg = None

    def hit(self, name):
        self.coverage[name] = self.coverage.get(name, 0) + 1

    def pins(self, rst, mr, cm, ca):
        if not rst:
            return (0, 0, 0, 0, 0)
        valid = self.pending is not None
        return (int(self.due is None and (not valid or mr) and not cm),
                int(valid), int(not cm and ca < self.t),
                int(len(self.written) == self.t and self.due is None and not valid),
                self.pending["code"] if valid else 0)

    def tick(self, *, rst=1, sv=0, sd=0, mr=1, cv=0, ca=0, cd=0, cm=0,
             label="", random_sample=False, tone="", window=False, async_pulse=False):
        k = self.cycle
        if async_pulse:
            assert rst and not sv and not cv and not cm
        pre = self.pins(rst, mr, cm, ca)
        sr, mv, cr, c_ready, _ = pre
        if rst and not async_pulse:
            if self.last_s is not None:
                assert sv and sd == self.last_s, "stimulus violated sample hold"
            if self.last_cfg is not None:
                assert cv and (ca, cd) == self.last_cfg, "stimulus violated cfg hold"
        self.last_s = sd if rst and not async_pulse and sv and not sr else None
        # The intentionally invalid-address diagnostic is withdrawn, since that
        # address can never transfer. Legal stalled requests are never withdrawn.
        self.last_cfg = (ca, cd) if rst and not async_pulse and cv and not cr and ca < self.t else None
        if not rst or async_pulse:
            if self.due is not None:
                self.hit("reset_computation")
                if 1 < k - self.inflight["accept_cycle"] <= self.t:
                    self.hit("reset_interior_mac")
                self.aborted += 1
            if self.pending:
                self.hit("reset_output")
                self.aborted += 1
            self.hit("reset_full_staging" if len(self.written) == self.t
                     else "reset_incomplete_staging")
            if 0 < len(self.written) < self.t:
                self.hit("reset_partial_programming")
            if not self.due and not self.pending:
                self.hit("reset_idle")
            self.history = [0] * self.t
            self.active = [0] * self.t
            self.staging = [0] * self.t
            self.written.clear()
            self.due = self.inflight = self.pending = None
            self.epoch += 1
            if async_pulse:
                self.hit("reset_pulse_without_clock_edge")
                pre = self.pins(1, mr, cm, ca)
        else:
            if not sv and sr:
                self.bubbles += 1
            if mv and not mr:
                self.stalls += 1
                self.stall_run += 1
                self.max_stall = max(self.max_stall, self.stall_run)
            else:
                self.stall_run = 0
            if cm and not c_ready:
                self.hit("held_commit")
                if self.due is not None:
                    self.hit("commit_busy")
                if mv and not mr:
                    self.hit("commit_output_stall")
                if len(self.written) != self.t:
                    self.hit("incomplete_commit")
            if mv and mr:
                self.transferred += 1
                assert k > self.pending["available_cycle"], "Output transferred on availability edge"
                if self.pending["window"]:
                    self.window_transfers.append(k)
                self.pending = None
                if sv and sr:
                    self.hit("retire_accept_coincidence")
                if cm:
                    assert not c_ready
                    self.hit("commit_blocked_on_retirement")
            if self.due == k:
                self.pending = self.inflight
                self.due = self.inflight = None
                self.completed += 1
                rec = self.pending
                assert k - rec["accept_cycle"] == self.t
                rec["available_cycle"] = k
                self.outputs.write(json.dumps(rec, separators=(",", ":")) + "\n")
                if rec["window"]:
                    self.window_outputs.append(k)
                err = rec["code"] / 32768 - rec["floating_clipped"]
                self.errors.append(err)
                self.unclipped_errors.append(rec["code"] / 32768 - rec["floating"])
                self.clipping_errors.append(rec["floating_clipped"] - rec["floating"])
                self.clipping_count += rec["floating"] != rec["floating_clipped"]
                if rec["floating"] == rec["floating_clipped"]:
                    self.unclipped_only_errors.append(rec["code"] / 32768 - rec["floating"])
                q, r = divmod(rec["acc"], 32768)
                if r == 16384:
                    self.ties.add(("negative" if rec["acc"] < 0 else "positive", q % 2))
                if r in (16383, 16385):
                    self.adjacent.add(r)
                if rounded(rec["acc"]) > 32767:
                    self.saturations.add("positive")
                if rounded(rec["acc"]) < -32768:
                    self.saturations.add("negative")
                if rec["tone"]:
                    self.tone_counts[rec["tone"]] = self.tone_counts.get(rec["tone"], 0) + 1
                    self.tone_errors.setdefault(rec["tone"], []).append(err)
                    self.tone_clipping_counts[rec["tone"]] = (
                        self.tone_clipping_counts.get(rec["tone"], 0)
                        + (rec["floating"] != rec["floating_clipped"]))
                if rec["label"]:
                    self.hit(rec["label"])
            if cv and cr:
                if ca in self.written:
                    self.hit("staging_overwrite")
                if self.due is not None or self.pending is not None:
                    self.hit("write_during_computation_or_output")
                # pre-edge busy, including the final computation edge
                if self.inflight is not None or (self.pending and self.pending.get("available_cycle") == k):
                    self.hit("write_during_computation")
                self.staging[ca] = cd
                self.written.add(ca)
            if cm and c_ready:
                self.active, self.staging = self.staging[:], self.active[:]
                self.written.clear()
                self.commits += 1
                self.committed_banks.append({
                    "commit": self.commits, "epoch": self.epoch, "cycle": k,
                    "coefficients": self.active[:],
                    "sha256": hashlib.sha256(json.dumps(self.active, separators=(",", ":")).encode()).hexdigest(),
                })
                if any(self.history):
                    self.hit("history_retained_commit")
            if sv and sr:
                self.history = [sd] + self.history[:-1]
                acc, code, floating, clipped = convolve(self.history, self.active)
                self.inflight = {
                    "accept_cycle": k, "epoch": self.epoch, "sample": sd, "acc": acc,
                    "commit_generation": self.commits,
                    "code": code, "floating": floating, "floating_clipped": clipped,
                    "label": label, "tone": tone, "window": window,
                }
                self.due = k + self.t
                self.accepted += 1
                self.random_samples += random_sample
                if window:
                    self.window_accepts.append(k)
        post = self.pins(rst, mr, cm, ca)
        vector_reset = 2 if async_pulse else rst
        self.vectors.write(f"{vector_reset} {sv} {sd} {mr} {cv} {ca} {cd} {cm}\n")
        for phase, values in ((0, pre), (1, pre), (2, post), (3, post)):
            self.expected.write(f"{k} {phase} " + " ".join(map(str, values)) + "\n")
        self.cycle += 1
        assert self.cycle <= 2000000, "bounded stimulus exceeded cycle budget"
        return pre

    def drain(self):
        for _ in range(self.t + 3):
            if self.due is None and self.pending is None:
                return
            self.tick()
        raise AssertionError("drain failed")

    def program(self, bank):
        for i, c in enumerate(bank):
            assert self.tick(cv=1, ca=i, cd=c)[2]

    def commit(self):
        for _ in range(self.t + 5):
            if self.tick(cm=1)[3]:
                return
        raise AssertionError("commit nonprogress")

    def send(self, x, **kw):
        for _ in range(self.t + 256):
            if self.tick(sv=1, sd=x, **kw)[0]:
                return
        raise AssertionError("sample nonprogress")

    def summary(self, full):
        assert self.due is None and self.pending is None
        assert self.accepted == self.transferred + self.aborted
        assert len(self.window_accepts) >= 64
        assert len(self.window_accepts) == len(self.window_outputs)
        assert len(self.window_accepts) == len(self.window_transfers)
        assert all(b - a == self.t + 1 for a, b in zip(self.window_accepts, self.window_accepts[1:]))
        assert all(o - a == self.t for a, o in zip(self.window_accepts, self.window_outputs))
        assert all(o - a == self.t + 1 for a, o in zip(self.window_accepts, self.window_transfers))
        assert self.commits >= 8 and self.bubbles >= 64 and self.stalls >= 64 and self.max_stall >= 16
        needed = ["reset_idle", "reset_computation", "reset_output", "reset_incomplete_staging",
                      "reset_pulse_without_clock_edge",
                      "staging_overwrite", "write_during_computation", "incomplete_commit",
                      "commit_busy", "commit_output_stall", "commit_blocked_on_retirement",
                      "history_retained_commit", "retire_accept_coincidence", "positive_impulse",
                      "negative_impulse", "positive_dc", "negative_dc", "alternating_extremes",
                      "zero", "positive_fullscale", "negative_fullscale", "long_bubble", "random_bubble",
                      "single_cycle_stall"]
        if self.t > 1:
            needed.extend(("reset_interior_mac", "reset_partial_programming"))
        else:
            needed.append("invalid_address")
        assert all(self.coverage.get(x, 0) > 0 for x in needed), (needed, self.coverage)
        assert self.ties == {("positive", 0), ("positive", 1), ("negative", 0), ("negative", 1)}
        assert self.adjacent == {16383, 16385}
        # A single signed product cannot reach negative output saturation.
        assert "positive" in self.saturations
        assert self.t == 1 or "negative" in self.saturations
        if full:
            assert self.completed >= 4096
            assert self.random_samples >= 1024
            assert len(self.tone_counts) == 8 and min(self.tone_counts.values()) >= 512
        def metrics(values):
            return {"max_absolute": max(map(abs, values), default=0),
                    "mae": math.fsum(map(abs, values)) / max(1, len(values)),
                    "rmse": math.sqrt(math.fsum(v * v for v in values) / max(1, len(values)))}
        return {
            "cycles": self.cycle, "accepted": self.accepted, "completed": self.completed,
            "transferred": self.transferred, "reset_aborted": self.aborted,
            "random_samples": self.random_samples, "commits": self.commits,
            "committed_banks": self.committed_banks,
            "input_bubble_cycles": self.bubbles, "output_stall_cycles": self.stalls,
            "max_contiguous_stall": self.max_stall, "coverage": self.coverage,
            "half_tie_sign_floor_parity": sorted(self.ties), "adjacent_remainders": sorted(self.adjacent),
            "saturation_directions": sorted(self.saturations),
            "unreachable": ["negative saturation with a single product",
                            "nonempty incomplete staging bitmap with only one address"] if self.t == 1 else [],
            "tone_scored_outputs": self.tone_counts, "clipped_outputs": self.clipping_count,
            "vs_saturated_float": metrics(self.errors), "vs_unclipped_float": metrics(self.unclipped_errors),
            "unclipped_outputs_only": metrics(self.unclipped_only_errors),
            "clipping_only_error": metrics(self.clipping_errors),
            "tone_error_metrics": {name: {**metrics(values),
                                           "clipped_outputs": self.tone_clipping_counts[name]}
                                   for name, values in self.tone_errors.items()},
            "float_tolerance": FLOAT_TOL,
            "measured_window": {"outputs": len(self.window_outputs),
                                "availability_latency_cycles": self.t,
                                "earliest_transfer_latency_cycles": self.t + 1,
                                "initiation_interval_cycles": self.t + 1,
                                "accept_cycles": self.window_accepts, "availability_cycles": self.window_outputs,
                                "transfer_cycles": self.window_transfers},
        }
