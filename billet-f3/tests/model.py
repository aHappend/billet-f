"""Specification-derived transaction and arithmetic oracles; no RTL state access."""
from collections import Counter
import cmath
import math

FIXTURES = {
    "LP50": (4096, 8192, 4096, -65536, 16384),
    "HP50": (36864, -73728, 36864, -65536, 16384),
    "LP75": (1024, 2048, 1024, -98304, 36864),
}
INPUTS = (
    ("rst_n", 1), ("s_data", 16), ("s_channel", 2), ("s_valid", 1),
    ("m_ready", 1), ("cfg_valid", 1), ("cfg_channel", 2), ("cfg_addr", 7),
    ("cfg_data", 16), ("commit_valid", 1), ("commit_channel", 2),
    ("sos_cfg_valid", 1), ("sos_cfg_channel", 2), ("sos_cfg_section", 2),
    ("sos_cfg_addr", 3), ("sos_cfg_data", 18), ("sos_commit_valid", 1),
    ("sos_commit_channel", 2), ("chain_valid", 1), ("chain_mode", 3),
    ("chain_sections", 3),
)
OUTPUTS = (
    ("s_ready", 1), ("cfg_ready", 1), ("commit_ready", 1),
    ("sos_cfg_ready", 1), ("sos_commit_ready", 1), ("chain_ready", 1),
    ("m_valid", 1), ("m_channel", 2), ("m_data", 16),
)


def pack(fields, values):
    word = 0
    for name, width in fields:
        word = (word << width) | (values.get(name, 0) & ((1 << width) - 1))
    return word


def unpack(fields, word):
    result = {}
    for name, width in reversed(fields):
        result[name] = word & ((1 << width) - 1)
        word >>= width
    if "m_data" in result and result["m_data"] >= 32768:
        result["m_data"] -= 65536
    return result


def quantize(value, shift, bits, coverage=None, label=""):
    q, r = divmod(value, 1 << shift)
    if coverage is not None:
        if r == 1 << (shift - 1):
            coverage[f"{label}_tie_{'negative' if value < 0 else 'positive'}_"
                     f"{'odd' if q & 1 else 'even'}"] += 1
        if value == 0:
            coverage[f"{label}_zero"] += 1
    q += r > (1 << (shift - 1)) or (r == (1 << (shift - 1)) and q & 1)
    low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if coverage is not None:
        if q < low:
            coverage[label + "_negative_saturation"] += 1
        if q > high:
            coverage[label + "_positive_saturation"] += 1
        if q in (low, high):
            coverage[label + "_exact_extreme"] += 1
    return min(high, max(low, q))


class Arithmetic:
    def __init__(self, taps, sections, coverage):
        self.taps, self.sections, self.coverage = taps, sections, coverage
        self.fir = [[0] * taps for _ in range(4)]
        self.sos = [[[0] * 5 for _ in range(sections)] for _ in range(4)]
        self.history = [[0] * taps for _ in range(4)]
        self.state = [[[0] * 4 for _ in range(sections)] for _ in range(4)]

    def clear_sos(self, channel=None):
        for c in range(4) if channel is None else (channel,):
            self.state[c] = [[0] * 4 for _ in range(self.sections)]

    def process(self, sample, channel, mode, count):
        def fir(x):
            h = [x] + self.history[channel][:-1]
            self.history[channel] = h
            terms = [a * b for a, b in zip(h, self.fir[channel])]
            if sum(terms) == 0 and any(terms):
                self.coverage["fir_accumulator_cancellation"] += 1
            return quantize(sum(terms), 15, 16, self.coverage, "fir")

        def sos(x):
            x *= 256
            for section in range(count):
                x1, x2, y1, y2 = self.state[channel][section]
                b0, b1, b2, a1, a2 = self.sos[channel][section]
                terms = (b0*x, b1*x1, b2*x2, -a1*y1, -a2*y2)
                a = sum(terms)
                assert -(1 << 52) <= a < (1 << 52)
                if a == 0 and any(terms):
                    self.coverage["state_accumulator_cancellation"] += 1
                y = quantize(a, 16, 32, self.coverage, "state")
                self.state[channel][section] = [x, x1, y, y1]
                x = y
            return quantize(x, 8, 16, self.coverage, "output")

        if mode == 0:
            return sample
        if mode == 1:
            return fir(sample)
        if mode == 2:
            return sos(sample)
        if mode == 3:
            return sos(fir(sample))
        return fir(sos(sample))


class Protocol:
    """One public transaction reservation, with specified edge-distance visibility."""
    def __init__(self, taps, lanes, sections):
        self.taps, self.lanes, self.sections = taps, lanes, sections
        self.cycle = 0
        self.coverage = Counter()
        self.accepted = self.retired = self.aborted = 0
        self.mode_counts = Counter()
        self.latencies = {}
        self.intervals = {}
        self.previous_accept = None
        self.reset()

    def reset(self):
        self.math = Arithmetic(self.taps, self.sections, self.coverage)
        self.mode, self.count = 1, 0
        self.pending = None
        self.fstage = [[0] * self.taps for _ in range(4)]
        self.sstage = [[[0] * 5 for _ in range(self.sections)] for _ in range(4)]
        self.fmask = [set() for _ in range(4)]
        self.smask = [set() for _ in range(4)]
        self.fbanks, self.sbanks = [0] * 4, [0] * 4
        self.previous_accept = None

    def visible(self):
        return self.pending is not None and self.cycle >= self.pending[0]

    def observe(self, v):
        if not v["rst_n"]:
            return {name: 0 for name, _ in OUTPUTS}
        p = self.pending
        valid = self.visible()
        empty = p is None
        updates = v["chain_valid"] or v["sos_commit_valid"] or v["commit_valid"]
        legal = ((v["chain_mode"] in (0, 1) and v["chain_sections"] == 0) or
                 (v["chain_mode"] in (2, 3, 4) and
                  1 <= v["chain_sections"] <= self.sections))
        return dict(
            s_ready=int(not updates and (empty or (valid and v["m_ready"]))),
            cfg_ready=int(not updates and v["cfg_addr"] < self.taps),
            commit_ready=int(empty and not v["chain_valid"] and
                             not v["sos_commit_valid"] and
                             len(self.fmask[v["commit_channel"]]) == self.taps),
            sos_cfg_ready=int(not updates and v["sos_cfg_section"] < self.sections
                              and v["sos_cfg_addr"] < 5),
            sos_commit_ready=int(empty and not v["chain_valid"] and
                                 len(self.smask[v["sos_commit_channel"]]) ==
                                 5*self.sections),
            chain_ready=int(empty and legal),
            m_valid=int(valid), m_data=p[1] if valid else 0,
            m_channel=p[2] if valid else 0,
        )

    def step(self, v):
        was_visible = self.visible()
        was_pending = self.pending is not None
        pre = self.observe(v)
        self.cycle += 1
        if not v["rst_n"]:
            if self.pending:
                self.aborted += 1
                self.coverage["reset_held" if was_visible else "reset_busy"] += 1
            self.reset()
            return pre, self.observe(v)
        retirement = pre["m_valid"] and v["m_ready"]
        if retirement:
            self.pending = None
            self.retired += 1
        # Staging and updates are derived from accepted public events only.
        for name in ("cfg", "sos_cfg", "commit", "sos_commit", "chain"):
            if v[name + "_valid"] and pre[name + "_ready"]:
                self.coverage[name + "_handshakes"] += 1
            elif v[name + "_valid"]:
                self.coverage[name + "_blocked"] += 1
        for high, low in (("chain", "sos_commit"), ("chain", "commit"),
                          ("sos_commit", "commit")):
            if v[high+"_valid"] and v[low+"_valid"]:
                assert not pre[low+"_ready"]
                self.coverage[f"priority_{high}_over_{low}"] += 1
        if was_pending:
            for name in ("cfg", "sos_cfg"):
                if v[name+"_valid"] and pre[name+"_ready"]:
                    self.coverage[name+("_during_held" if was_visible else "_during_busy")] += 1
        if (v["cfg_valid"] and pre["cfg_ready"] and v["sos_cfg_valid"]
                and pre["sos_cfg_ready"]):
            self.coverage["simultaneous_staging_writes"] += 1
        if any(v[k] for k in ("chain_valid", "sos_commit_valid", "commit_valid")):
            assert not any(pre[k] for k in ("s_ready", "cfg_ready", "sos_cfg_ready"))
            self.coverage["update_suppresses_sample_and_writes"] += 1
        if v["cfg_valid"] and pre["cfg_ready"]:
            c, a = v["cfg_channel"], v["cfg_addr"]
            self.coverage["fir_duplicate"] += a in self.fmask[c]
            self.fstage[c][a] = v["cfg_data"]
            self.fmask[c].add(a)
        if v["sos_cfg_valid"] and pre["sos_cfg_ready"]:
            c, s, a = v["sos_cfg_channel"], v["sos_cfg_section"], v["sos_cfg_addr"]
            self.coverage["sos_duplicate"] += (s, a) in self.smask[c]
            self.sstage[c][s][a] = v["sos_cfg_data"]
            self.smask[c].add((s, a))
        if v["chain_valid"] and pre["chain_ready"]:
            if any(v for ch in self.math.state for sec in ch for v in sec):
                self.coverage["chain_clears_nonzero_sos"] += 1
            if any(v for ch in self.math.history for v in ch):
                self.coverage["chain_retains_nonzero_fir_history"] += 1
            self.mode, self.count = v["chain_mode"], v["chain_sections"]
            self.math.clear_sos()
            self.previous_accept = None
        elif v["sos_commit_valid"] and pre["sos_commit_ready"]:
            c = v["sos_commit_channel"]
            if any(v for sec in self.math.state[c] for v in sec):
                self.coverage["commit_clears_nonzero_sos"] += 1
            old = self.math.sos[c]
            self.math.sos[c], self.sstage[c] = self.sstage[c], old
            self.smask[c].clear()
            self.math.clear_sos(c)
            self.sbanks[c] ^= 1
            self.coverage[f"sos_bank_{self.sbanks[c]}"] += 1
        elif v["commit_valid"] and pre["commit_ready"]:
            c = v["commit_channel"]
            if any(self.math.history[c]):
                self.coverage["commit_retains_nonzero_fir_history"] += 1
            self.math.fir[c], self.fstage[c] = self.fstage[c], self.math.fir[c]
            self.fmask[c].clear()
            self.fbanks[c] ^= 1
            self.coverage[f"fir_bank_{self.fbanks[c]}"] += 1
        if v["s_valid"] and pre["s_ready"]:
            assert self.pending is None
            x, c = v["s_data"], v["s_channel"]
            result = self.math.process(x, c, self.mode, self.count)
            g = (self.taps + self.lanes - 1)//self.lanes
            latency = (0, g, self.count, g+self.count+1, g+self.count+1)[self.mode]
            self.pending = (self.cycle + latency, result, c)
            self.accepted += 1
            key = f"{self.mode}:{self.count}:{c}"
            self.mode_counts[key] += 1
            self.latencies[f"{self.mode}:{self.count}"] = latency
            if self.previous_accept is not None:
                interval = self.cycle-self.previous_accept
                label = f"{self.mode}:{self.count}"
                self.intervals[label] = min(interval, self.intervals.get(label, interval))
            self.previous_accept = self.cycle
            if retirement:
                self.coverage[f"retire_accept_mode_{self.mode}"] += 1
        if pre["m_valid"] and not v["m_ready"]:
            self.coverage[f"stall_mode_{self.mode}"] += 1
        assert self.accepted == self.retired + self.aborted + (self.pending is not None)
        return pre, self.observe(v)


class Floating:
    """Transposed direct form II, independent of the integer DF-I recurrence."""
    def __init__(self, fir, sos, mode):
        self.fir = [x/32768 for x in fir]
        self.sos = [[x/65536 for x in row] for row in sos]
        self.mode = mode
        self.h = [[0.0]*len(fir) for _ in range(4)]
        self.z = [[[0.0, 0.0] for _ in sos] for _ in range(4)]

    def sample(self, x, c):
        def fir(v):
            self.h[c] = [v] + self.h[c][:-1]
            return sum(a*b for a, b in zip(self.h[c], self.fir))

        def sos(v):
            for i, (b0, b1, b2, a1, a2) in enumerate(self.sos):
                z1, z2 = self.z[c][i]
                y = b0*v + z1
                self.z[c][i] = [b1*v-a1*y+z2, b2*v-a2*y]
                v = y
            return v

        x /= 32768
        return (lambda: x, lambda: fir(x), lambda: sos(x),
                lambda: sos(fir(x)), lambda: fir(sos(x)))[self.mode]()


def response(fir, sections, mode, omega):
    z = cmath.exp(-1j*omega)
    h = 1+0j
    if mode in (1, 3, 4):
        h *= sum(v/32768*z**i for i, v in enumerate(fir))
    if mode in (2, 3, 4):
        for b0, b1, b2, a1, a2 in sections:
            h *= (b0+b1*z+b2*z*z)/(65536+a1*z+a2*z*z)
    return h


def fixture_analysis():
    records = {}
    for name, row in FIXTURES.items():
        a1, a2 = row[3]/65536, row[4]/65536
        root = cmath.sqrt(a1*a1-4*a2)
        poles = [(-a1+root)/2, (-a1-root)/2]
        expected = .75 if name == "LP75" else .5
        assert all(abs(p-expected) < 1e-12 for p in poles)
        dc = response([], [row], 2, 0)
        ny = response([], [row], 2, math.pi)
        assert abs(dc-(0 if name == "HP50" else 1)) < 1e-12
        if name == "HP50":
            assert abs(ny-1) < 1e-12
        records[name] = dict(poles=[[p.real, p.imag] for p in poles],
                             dc_gain=abs(dc), nyquist_gain=abs(ny),
                             coefficients=row)
    return records
