"""Public-transaction convolution oracle; no implementation hierarchy is observed."""

from collections import Counter
from dataclasses import asdict, dataclass

from support import Mismatch, require


FIELDS = ("rst_n", "s_data", "s_channel", "s_valid", "m_ready", "cfg_valid",
          "cfg_channel", "cfg_addr", "cfg_data", "commit_valid", "commit_channel")
OUTPUTS = ("s_ready", "cfg_ready", "commit_ready", "m_valid", "m_data", "m_channel")


def quantize(total):
    # Arbitrary precision, Euclidean quotient/remainder, independent of RTL widths.
    quotient, remainder = divmod(total, 32768)
    if remainder > 16384 or (remainder == 16384 and quotient % 2):
        quotient += 1
    return min(32767, max(-32768, quotient))


@dataclass
class Transaction:
    ident: int
    epoch: int
    channel: int
    generation: int
    accepted: int
    due: int
    total: int
    result: int
    products: list
    history: list
    coefficients: list
    other_histories: list
    phase: str


class Oracle:
    """Stores mathematical channel state and one public completion obligation.

    Completion is scheduled from the interface's G-cycle contract, not from
    a replica of tap indices, pipeline stages or accumulator transitions.
    """

    def __init__(self, taps, lanes):
        self.taps, self.lanes = taps, lanes
        self.groups = (taps + lanes - 1) // lanes
        self.cycle = 0
        self.epoch = 0
        self.accepted = [0] * 4
        self.retired = [0] * 4
        self.canceled = [0] * 4
        self.commits = [0] * 4
        self.generations_seen = [set() for _ in range(4)]
        self.stalls = [0] * 4
        self.coverage = Counter()
        self.coverage_witnesses = {}
        self.valid_at = {}
        self.latency_valid = []
        self.latency_retire = []
        self.intervals = []
        self.measure_intervals = []
        self.boundaries = []
        self.transactions = []
        self.cancel_records = []
        self.last_accept = None
        self.previous_pins = None
        self.previous_drive = None
        self.commit_must_hold = False
        self.previous_incomplete = False
        self.in_reset = False
        self.serial = 0
        self.pending = None
        self.clear()

    def clear(self):
        self.history = [[0] * self.taps for _ in range(4)]
        self.coefficients = [[0] * self.taps for _ in range(4)]
        self.staging = [[0] * self.taps for _ in range(4)]
        self.written = [set() for _ in range(4)]
        self.generation = [0] * 4
        self.pending = None
        self.last_accept = None

    def reset_if_needed(self, pins):
        if not pins["rst_n"]:
            if not self.in_reset:
                if self.pending:
                    p = self.pending
                    kind = "reset_compute" if self.cycle <= p.due else "reset_stalled"
                    # At the first pre-edge after E0+G, output is already valid.
                    self.coverage[kind] += 1
                    self.canceled[p.channel] += 1
                    self.cancel_records.append({"id": p.ident, "epoch": p.epoch,
                                                "cycle": self.cycle, "kind": kind})
                if self.previous_incomplete:
                    self.coverage["reset_blocked_commit"] += 1
                self.epoch += 1
                self.clear()
            self.in_reset = True
        else:
            self.in_reset = False

    def pins(self, drive, post=False):
        if not drive["rst_n"]:
            return [0] * 6
        p = self.pending
        # During pre-edge observation cycle E, E's computation has not run.
        visible = p is not None and (self.cycle >= p.due if post else self.cycle > p.due)
        computing = p is not None and not visible
        return [
            int(not computing and (not visible or drive["m_ready"]) and
                not drive["commit_valid"]),
            int(not drive["commit_valid"] and drive["cfg_addr"] < self.taps),
            int(len(self.written[drive["commit_channel"]]) == self.taps and p is None),
            int(visible),
            p.result if visible else 0,
            p.channel if visible else 0,
        ]

    def check(self, expected, observed, stage):
        if expected != observed:
            mismatch = Mismatch(self.cycle, stage + " public pins", expected, observed)
            mismatch.record["transaction"] = asdict(self.pending) if self.pending else None
            raise mismatch

    def numerical_coverage(self, p):
        c = self.coverage
        total = p.total
        q, r = divmod(total, 32768)
        if not any(p.coefficients):
            c["zero_coefficients"] += 1
        if not any(p.history):
            c["zero_history"] += 1
        if p.history[0] == 0:
            c["zero_sample"] += 1
        nonzero = [x for x in p.history if x]
        if len(nonzero) == 1:
            c["positive_impulse" if nonzero[0] > 0 else "negative_impulse"] += 1
        if r == 16384:
            c[f"tie_{'positive' if total >= 0 else 'negative'}_"
              f"{'odd' if q % 2 else 'even'}"] += 1
        if r == 16383:
            c["below_half"] += 1
            c["below_half_positive" if total >= 0 else "below_half_negative"] += 1
        if r == 16385:
            c["above_half"] += 1
            c["above_half_positive" if total >= 0 else "above_half_negative"] += 1
        if total > 32767 * 32768 + 16384:
            c["positive_saturation"] += 1
        if total < -32768 * 32768 - 16384:
            c["negative_saturation"] += 1
        if any(abs(x) >= 32767 and abs(h) >= 32767
               for x, h in zip(p.history, p.coefficients)):
            c["full_scale_product"] += 1
        if 1073741824 in p.products:
            c["negative_full_scale_product"] += 1
        if any(x > 0 for x in p.products) and any(x < 0 for x in p.products):
            c["mixed_sign_products"] += 1
        if (len(p.history) > 1 and all(p.history[i] * p.history[i + 1] < 0
                                     for i in range(min(4, self.taps) - 1))):
            c["alternating_sign_history"] += 1
        if (any(p.products[1:]) and any(other[1:] != p.history[1:]
                                       for other in p.other_histories)):
            c["independent_history_comparison"] += 1
        group = p.products[:self.lanes]
        if (self.lanes > 1 and any(x > 0 for x in group) and
                any(x < 0 for x in group) and abs(sum(group)) < max(map(abs, group))):
            c["lane_cancellation"] += 1

    def tick(self, drive, phase="", observed_pre=None, observed_post=None):
        coverage_before = self.coverage.copy()
        pending_before = self.pending
        if self.previous_drive and self.previous_drive["rst_n"] and drive["rst_n"]:
            prior = self.previous_drive
            for valid, ready_index, payload in [
                    ("s_valid", 0, ("s_data", "s_channel")),
                    ("cfg_valid", 1, ("cfg_channel", "cfg_addr", "cfg_data"))]:
                if prior[valid] and not self.previous_pins[ready_index]:
                    require(drive[valid] == 1 and all(drive[f] == prior[f] for f in payload),
                            f"stimulus violated held {valid}/payload")
            if self.commit_must_hold:
                require(drive["commit_valid"] == 1, "complete stalled commit was withdrawn")
            if prior["commit_valid"] and drive["commit_valid"] and not self.previous_pins[2]:
                require(prior["commit_channel"] == drive["commit_channel"],
                        "stalled commit channel changed")
        self.reset_if_needed(drive)
        pre = self.pins(drive)
        if observed_pre is not None:
            self.check(pre, observed_pre, "pre-edge")
            pre = observed_pre
        old = self.pending
        if drive["rst_n"]:
            c = self.coverage
            if (self.previous_drive and self.previous_drive["rst_n"] and
                    self.previous_pins[3] and not self.previous_drive["m_ready"]):
                require(pre[3:] == self.previous_pins[3:], "stalled output changed")
                c["output_stability"] += 1
            cv = drive["commit_valid"]
            cc = drive["commit_channel"]
            cfg = drive["cfg_valid"] and pre[1]
            accept = drive["s_valid"] and pre[0]
            commit = cv and pre[2]
            retire = pre[3] and drive["m_ready"]
            if pre[3] and not drive["m_ready"]:
                self.stalls[old.channel] += 1
                if cfg:
                    c["cfg_during_output_stall"] += 1
            if old and not pre[3] and cfg:
                c["cfg_during_compute"] += 1
            incomplete = cv and len(self.written[cc]) != self.taps
            self.commit_must_hold = bool(cv and not pre[2] and not incomplete)
            if old is None and any(len(mask) == self.taps for mask in self.written) and any(
                    len(mask) != self.taps for mask in self.written):
                c["independent_completeness"] += 1
            if incomplete:
                c[f"incomplete_commit_channel_{cc}"] += 1
            if cv and old:
                kind = "stalled" if pre[3] and not drive["m_ready"] else "busy"
                same = "same" if cc == old.channel else "other"
                if kind == "stalled" or not pre[3]:
                    c[f"commit_blocked_{kind}_{same}"] += 1
            if cv and drive["cfg_valid"]:
                require(not cfg, "commit priority must block cfg")
                c["write_blocked_by_commit"] += 1
            if cv and drive["s_valid"]:
                require(not accept, "commit priority must block sample")
                c["sample_blocked_by_commit"] += 1
            if drive["cfg_valid"] and drive["cfg_addr"] >= self.taps:
                require(not cfg, "out-of-range write was accepted")
                c["out_of_range_cfg"] += 1
            if retire:
                p = old
                self.retired[p.channel] += 1
                self.latency_retire.append(self.cycle - p.accepted)
                self.numerical_coverage(p)
                self.transactions.append({
                    "id": p.ident, "epoch": p.epoch, "channel": p.channel,
                    "generation": p.generation, "accepted": p.accepted,
                    "valid": self.valid_at[p.ident], "retired": self.cycle, "accumulator": p.total,
                    "expected": p.result, "observed": pre[4], "phase": p.phase,
                })
                self.pending = None
            if commit:
                old_gen = self.generation[cc]
                self.coefficients[cc] = self.staging[cc][:]
                self.staging[cc] = [0] * self.taps
                self.written[cc].clear()
                self.commits[cc] += 1
                self.generation[cc] = self.commits[cc]
                self.generations_seen[cc].add(tuple(self.coefficients[cc]))
                self.boundaries.append({
                    "cycle": self.cycle, "epoch": self.epoch, "channel": cc,
                    "old_generation": old_gen, "new_generation": self.generation[cc],
                    "coefficients": self.coefficients[cc][:],
                })
                c["atomic_commit"] += 1
            if cfg:
                ch, addr = drive["cfg_channel"], drive["cfg_addr"]
                if addr in self.written[ch]:
                    c["duplicate_write"] += 1
                    if self.staging[ch][addr] != drive["cfg_data"]:
                        c["duplicate_overwrite"] += 1
                if len(self.written[ch]) == self.taps - 1 and addr not in self.written[ch]:
                    c["final_missing_write"] += 1
                    if old is None and cc == ch:
                        require(pre[2] == 0, "final write enabled same-edge commit")
                        c["final_write_visibility"] += 1
                self.staging[ch][addr] = drive["cfg_data"]
                self.written[ch].add(addr)
            if accept:
                ch = drive["s_channel"]
                self.history[ch] = [drive["s_data"]] + self.history[ch][:-1]
                products = [x * h for x, h in zip(self.history[ch], self.coefficients[ch])]
                total = sum(products)
                self.serial += 1
                p = Transaction(self.serial, self.epoch, ch, self.generation[ch],
                                self.cycle, self.cycle + self.groups, total,
                                quantize(total), products, self.history[ch][:],
                                self.coefficients[ch][:],
                                [h[:] for i, h in enumerate(self.history) if i != ch], phase)
                self.pending = p
                self.accepted[ch] += 1
                if cfg:
                    c["simultaneous_cfg_sample"] += 1
                if retire:
                    c["retire_input_overlap"] += 1
                if self.last_accept:
                    gap = self.cycle - self.last_accept[0]
                    self.intervals.append(gap)
                    if phase == "measure" and self.last_accept[1] == "measure":
                        self.measure_intervals.append(gap)
                        require(gap == self.groups + 1, "G+1 measured initiation interval")
                self.last_accept = (self.cycle, phase)
            self.previous_incomplete = bool(incomplete)
        else:
            self.previous_incomplete = False
            self.commit_must_hold = False
        post = self.pins(drive, post=True)
        if observed_post is not None:
            self.check(post, observed_post, "post-edge")
            post = observed_post
        if post[3] and self.pending.ident not in self.valid_at:
            p = self.pending
            self.valid_at[p.ident] = self.cycle
            self.latency_valid.append(self.cycle - p.accepted)
            require(self.latency_valid[-1] == self.groups, "observed G latency contract")
        for name, count in self.coverage.items():
            if count > coverage_before[name] and name not in self.coverage_witnesses:
                self.coverage_witnesses[name] = {
                    "cycle": self.cycle, "inputs": drive.copy(),
                    "pre": observed_pre if observed_pre is not None else pre,
                    "post": observed_post if observed_post is not None else post,
                    "transaction": asdict(old or pending_before) if old or pending_before else None,
                }
        self.previous_pins = pre
        self.previous_drive = drive.copy()
        self.cycle += 1
        return pre, post

    def results(self, channels, thresholds=True):
        require(self.pending is None, "unfinished accepted sample at end")
        for ch in range(4):
            require(self.accepted[ch] == self.retired[ch] + self.canceled[ch],
                    f"transaction conservation channel {ch}")
        if thresholds:
            for ch in channels:
                require(self.retired[ch] >= 256, f"retirement threshold channel {ch}")
                require(self.commits[ch] >= 4, f"commit threshold channel {ch}")
                require(len(self.generations_seen[ch]) >= 4, f"distinct banks channel {ch}")
                require(self.stalls[ch] >= 16, f"stall threshold channel {ch}")
                require(self.coverage[f"incomplete_commit_channel_{ch}"] > 0,
                        f"incomplete commit coverage channel {ch}")
                require(any(b["epoch"] == self.epoch and b["channel"] == ch
                            for b in self.boundaries), f"post-reset refill channel {ch}")
            required = [
                "reset_compute", "reset_stalled", "reset_blocked_commit",
                "zero_coefficients", "zero_history", "zero_sample",
                "positive_impulse", "negative_impulse", "tie_positive_even",
                "tie_positive_odd", "tie_negative_even", "tie_negative_odd",
                "below_half_positive", "below_half_negative",
                "above_half_positive", "above_half_negative",
                "positive_saturation", "full_scale_product", "negative_full_scale_product",
                "duplicate_write", "duplicate_overwrite", "final_missing_write", "final_write_visibility",
                "cfg_during_compute", "cfg_during_output_stall", "simultaneous_cfg_sample",
                "write_blocked_by_commit", "sample_blocked_by_commit",
                "retire_input_overlap", "output_stability", "commit_blocked_busy_same",
                "commit_blocked_stalled_same",
            ]
            if self.taps > 1:
                required += ["negative_saturation", "mixed_sign_products", "alternating_sign_history"]
            if self.taps > 1 and self.lanes > 1:
                required += ["lane_cancellation"]
            if len(channels) > 1:
                required += ["commit_blocked_busy_other", "commit_blocked_stalled_other",
                             "independent_completeness"]
                if self.taps > 1:
                    required += ["independent_history_comparison"]
            if self.taps == 1:
                required += ["out_of_range_cfg"]
            for name in required:
                require(self.coverage[name] > 0, f"missing observed coverage: {name}")
            require(len(self.measure_intervals) >= 255, "missing no-backpressure measurements")
            require(any(i > self.groups + 1 for i in self.intervals),
                    "missing stalled initiation interval")
        for b in self.boundaries:
            eligible = [t for t in self.transactions
                        if t["epoch"] == b["epoch"] and t["channel"] == b["channel"]]
            before = [t for t in eligible if t["accepted"] < b["cycle"]]
            after = [t for t in eligible if t["accepted"] > b["cycle"]]
            b["last_old_sample"] = before[-1] if before else None
            b["first_new_sample"] = after[0] if after else None
            if before:
                require(before[-1]["generation"] == b["old_generation"],
                        "last old-generation sample does not match committed ownership")
            if after:
                require(after[0]["generation"] == b["new_generation"],
                        "no observed first sample for committed generation")
        def span(items):
            return {"count": len(items), "min": min(items) if items else None,
                    "max": max(items) if items else None}
        return {
            "taps": self.taps, "lanes": self.lanes, "cycles": self.cycle,
            "accepted": self.accepted, "retired": self.retired, "canceled": self.canceled,
            "commits": self.commits, "distinct_generations": [len(s) for s in self.generations_seen],
            "stalled_output_cycles": self.stalls, "coverage": dict(self.coverage),
            "latency_to_valid_cycles": span(self.latency_valid),
            "latency_to_retirement_cycles": span(self.latency_retire),
            "unstalled_initiation_interval_cycles": span(self.measure_intervals),
            "unstalled_throughput_cycles_per_sample": span(self.measure_intervals),
            "all_initiation_intervals_cycles": span(self.intervals),
            "extended_initiation_intervals_cycles":
                span([i for i in self.intervals if i > self.groups + 1]),
            "maximum_output_code_error": 0, "mismatches": 0,
            "required_retirements_per_channel": 256 if thresholds else None,
            "status": "passed",
        }
