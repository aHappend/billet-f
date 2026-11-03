"""Independent integer mathematics for the admitted BILLET-F4 contract.

No RTL, ROM, simulator output, or parent implementation supplies expectations.
Constants are evaluated at 80 decimal digits; final integer rounding is audited
against a second, 100-digit evaluation before use.
"""

from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from functools import lru_cache
import math


def rne(value, shift):
    floor, remainder = divmod(value, 1 << shift)
    half = 1 << (shift - 1)
    return floor + (remainder > half or (remainder == half and floor & 1))


def sat(value, bits=16, signed=True):
    low = -(1 << (bits - 1)) if signed else 0
    high = (1 << (bits - int(signed))) - 1
    return min(high, max(low, value))


def _atan_reciprocal(n):
    x = Decimal(1) / n
    term, total, i = x, x, 1
    while True:
        term *= -x * x
        increment = term / (2 * i + 1)
        updated = total + increment
        if updated == total:
            return total
        total, i = updated, i + 1


def _cos(x, pi):
    x %= 2 * pi
    if x > pi:
        x -= 2 * pi
    term, result, n = Decimal(1), Decimal(1), 1
    while True:
        term *= -x * x / ((2 * n - 1) * (2 * n))
        updated = result + term
        if updated == result:
            return result
        result, n = updated, n + 1


def _tables(precision):
    with localcontext() as ctx:
        ctx.prec = precision
        pi = 16 * _atan_reciprocal(5) - 4 * _atan_reciprocal(239)

        def quantize(x):
            return int(x.to_integral_value(rounding=ROUND_HALF_EVEN))

        hann = [quantize(32768 * (1 - _cos(2 * pi * n / 511, pi)))
                for n in range(512)]
        cosine = [quantize(65536 * _cos(2 * pi * n / 512, pi))
                  for n in range(512)]
        sine = [quantize(-65536 * _cos(pi / 2 - 2 * pi * n / 512, pi))
                for n in range(512)]
        knots = [700 * ((Decimal(1) + Decimal(8000) / 700).ln()
                       * Decimal(i) / 65).exp() - 700 for i in range(66)]
        weights = []
        for b in range(64):
            row = []
            for k in range(257):
                f = Decimal(125) * k / 4
                triangle = max(Decimal(0), min(
                    (f - knots[b]) / (knots[b + 1] - knots[b]),
                    (knots[b + 2] - f) / (knots[b + 2] - knots[b + 1])))
                row.append(quantize(65536 * triangle))
            weights.append(row)
        endpoints = [quantize(65536 * (1 + Decimal(j) / 16).ln()
                              / Decimal(2).ln()) for j in range(17)]
        return hann, cosine, sine, weights, endpoints


@lru_cache(maxsize=1)
def tables():
    first, audit = _tables(80), _tables(100)
    assert first == audit, "constant rounding differs at 80 and 100 digits"
    assert all(any(row) for row in first[3]), "empty Mel band"
    return first


def spectrum(samples):
    assert len(samples) == 512
    hann, cosine, sine, _, _ = tables()
    windowed = [sat(rne(x * w, 16)) for x, w in zip(samples, hann)]
    result = []
    for k in range(257):
        real = sum(x * cosine[(k * n) % 512] for n, x in enumerate(windowed))
        imag = sum(x * sine[(k * n) % 512] for n, x in enumerate(windowed))
        ar, ai = rne(real, 25), rne(imag, 25)
        assert -(1 << 17) <= ar < (1 << 17)
        assert -(1 << 17) <= ai < (1 << 17)
        result.append(sat(ar * ar + ai * ai, 32, False))
    return result


def mel(powers):
    assert len(powers) == 257
    return [rne(sum(p * w for p, w in zip(powers, row)), 16)
            for row in tables()[3]]


def token(energy):
    assert 0 <= energy < (1 << 48)
    if energy == 0:
        return 0
    exponent = energy.bit_length() - 1
    fraction = ((energy - (1 << exponent)) << 16) // (1 << exponent)
    segment, residual = divmod(fraction, 4096)
    endpoints = tables()[4]
    log_q16 = ((exponent << 16) + endpoints[segment]
               + rne((endpoints[segment + 1] - endpoints[segment]) * residual, 12))
    db_q16 = rne((log_q16 - (30 << 16)) * 197283, 16)
    clipped = min(0, max(-(96 << 16), db_q16))
    return sat(rne((clipped + (96 << 16)) * 21845, 21), 16, False)


def tokens(samples):
    return [token(e) for e in mel(spectrum(samples))]


def pwl_error():
    """Analytical extrema of each line, plus exhaustive fixed-point fractions.

    A chord-minus-log has its sole interior stationary point at 1/(slope*ln2).
    Endpoint quantization and interpolation rounding each add <= 0.5/65536.
    Input normalization truncation contributes <= 1/(65536*ln2).
    """
    endpoints = tables()[4]
    continuous = 0.0
    fixed = 0.0
    for j in range(16):
        left, right = 1 + j / 16, 1 + (j + 1) / 16
        slope = (endpoints[j + 1] - endpoints[j]) / 4096
        stationary = 1 / (slope * math.log(2))
        for x in (left, right, min(right, max(left, stationary))):
            line = endpoints[j] / 65536 + slope * (x - left)
            continuous = max(continuous, abs(line - math.log2(x)))
        for t in range(4096):
            line = (endpoints[j] + rne(
                (endpoints[j + 1] - endpoints[j]) * t, 12)) / 65536
            fixed = max(fixed, abs(line - math.log2(1 + (4096 * j + t) / 65536)))
    bound = max(fixed, continuous + 0.5 / 65536) + 1 / (65536 * math.log(2))
    assert bound <= 0.001, f"PWL error bound {bound} exceeds 0.001"
    return {"fixed_grid_max_log2": fixed, "all_energy_bound_log2": bound}


class Legacy:
    def __init__(self, fir, sos, mode=1, sections=None):
        self.fir = fir
        self.sos = sos
        self.mode = mode
        self.sections = len(sos[0]) if sections is None else sections
        self.history = [[] for _ in range(4)]
        self.clear_sos()

    def clear_sos(self, channel=None):
        if channel is None:
            self.state = [[[0, 0, 0, 0] for _ in row] for row in self.sos]
        else:
            self.state[channel] = [[0, 0, 0, 0] for _ in self.sos[channel]]

    def fir_sample(self, x, ch):
        history = self.history[ch]
        history.insert(0, x)
        del history[len(self.fir[ch]):]
        return sat(rne(sum(c * v for c, v in zip(self.fir[ch], history)), 15))

    def sos_sample(self, x, ch):
        value = x << 8
        for section in range(self.sections):
            b0, b1, b2, a1, a2 = self.sos[ch][section]
            x1, x2, y1, y2 = self.state[ch][section]
            y = sat(rne(b0 * value + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2, 16), 32)
            self.state[ch][section] = [value, x1, y, y1]
            value = y
        return sat(rne(value, 8))

    def sample(self, value, ch):
        if self.mode == 0:
            return value
        if self.mode == 1:
            return self.fir_sample(value, ch)
        if self.mode == 2:
            return self.sos_sample(value, ch)
        if self.mode == 3:
            return self.sos_sample(self.fir_sample(value, ch), ch)
        return self.fir_sample(self.sos_sample(value, ch), ch)


class Resampler:
    """Output indices define the model; it does not mirror DUT gap/FSM state."""

    def __init__(self, banks, l=1, m=1):
        self.banks, self.l, self.m = banks, l, m
        self.inputs = [[] for _ in range(4)]
        self.output_indices = [0] * 4

    def sample(self, value, channel):
        history = self.inputs[channel]
        history.append(value)
        outputs = []
        j = self.output_indices[channel]
        while (j * self.m) // self.l < len(history):
            anchor, phase = divmod(j * self.m, self.l)
            total = sum(history[anchor - k] * c
                        for k, c in enumerate(self.banks[channel][phase])
                        if anchor >= k)
            outputs.append(sat(rne(total, 16)))
            j += 1
        self.output_indices[channel] = j
        return outputs


def lowpass(taps=32, cutoff=0.12):
    values = []
    for n in range(taps):
        x = n - (taps - 1) / 2
        sinc = math.sin(2 * math.pi * cutoff * x) / (math.pi * x) if x else 2 * cutoff
        window = 0.54 - 0.46 * math.cos(2 * math.pi * n / (taps - 1))
        values.append(sinc * window)
    coefficients = [round(x * 65536 / sum(values)) for x in values]
    coefficients[taps // 2] += 65536 - sum(coefficients)
    assert sum(coefficients) == 65536
    return coefficients
