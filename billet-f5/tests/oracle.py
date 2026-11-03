"""Independent integer DSP oracle derived from the admitted BILLET-F5 contract."""

import math
from collections import deque


def rne(value, shift):
    divisor = 1 << shift
    floor, residue = divmod(value, divisor)
    return floor + int(residue * 2 > divisor or
                       (residue * 2 == divisor and floor & 1))


def sat(value, width=16):
    return max(-(1 << (width - 1)), min((1 << (width - 1)) - 1, value))


def signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if value & (1 << (width - 1)) else value


def trunc_div(numerator, denominator):
    if denominator <= 0:
        raise ValueError("normalization denominator must be positive")
    magnitude = abs(numerator) // denominator
    return -magnitude if numerator < 0 else magnitude


class Beamformer:
    def __init__(self, depth=8, delays=None):
        self.depth = depth
        self.delays = list(delays if delays is not None else [0] * 8)
        if len(self.delays) != 8 or any(not 0 <= q < depth * 256
                                        for q in self.delays):
            raise ValueError("eight legal Q8 delays required")
        self.samples = deque(maxlen=depth)

    def step(self, microphones):
        if len(microphones) != 8:
            raise ValueError("eight synchronized microphones required")
        ages = [tuple(microphones), *self.samples]
        values = []
        for i, delay in enumerate(self.delays):
            integer, fraction = divmod(delay, 256)
            u = ages[integer][i] if integer < len(ages) else 0
            v = ages[integer + 1][i] if integer + 1 < len(ages) else 0
            values.append(sat(rne((256 - fraction) * u + fraction * v, 8)))
        self.samples.appendleft(tuple(microphones))
        return sat(rne(sum(values), 3))


class NLMS:
    def __init__(self, taps=16, enabled=True):
        self.taps = taps
        self.enabled = enabled
        self.weights = [0] * taps
        self.history = deque([0] * taps, maxlen=taps)
        self.desired_power = 0
        self.far_power = 0

    def step(self, desired, far):
        self.history.appendleft(far)
        vector = list(self.history)
        denominator = 65536 + sum(x * x for x in vector)
        prediction = sat(rne(sum(w * x for w, x in
                                 zip(self.weights, vector)), 22))
        error = sat(desired - prediction)
        self.desired_power += -(self.desired_power >> 5) + (desired * desired >> 5)
        self.far_power += -(self.far_power >> 5) + (far * far >> 5)
        frozen = (not self.enabled or self.far_power < 65536 or
                  self.desired_power > 2 * self.far_power + 65536)
        if not frozen:
            self.weights = [
                sat(w - rne(w, 16) + trunc_div(error * x * (1 << 21),
                                               denominator), 24)
                for w, x in zip(self.weights, vector)
            ]
        return (error if self.enabled else desired), int(frozen)


class VAD:
    def __init__(self):
        self.active = False
        self.hang = 0

    def step(self, energy):
        if energy >= 65536:
            self.active = True
            self.hang = 3
        elif self.active and energy >= 32768:
            pass
        elif self.active and self.hang > 0:
            self.hang -= 1
        else:
            self.active = False
            self.hang = 0
        return int(self.active)


HANN = tuple(round(65536 * (1 - math.cos(2 * math.pi * n / 511)) / 2)
             for n in range(512))
COSINE = tuple(round(65536 * math.cos(2 * math.pi * q / 512))
               for q in range(512))
SINE = tuple(round(-65536 * math.sin(2 * math.pi * q / 512))
             for q in range(512))
BREAKPOINTS = tuple(700 * ((1 + 8000 / 700) ** (j / 65) - 1)
                    for j in range(66))
MEL_WEIGHTS = tuple(
    tuple(round(65536 * max(0.0, min(
        (16000 * k / 512 - BREAKPOINTS[b]) /
        (BREAKPOINTS[b + 1] - BREAKPOINTS[b]),
        (BREAKPOINTS[b + 2] - 16000 * k / 512) /
        (BREAKPOINTS[b + 2] - BREAKPOINTS[b + 1]))))
          for k in range(257))
    for b in range(64)
)
LOG_ENDPOINTS = tuple(round(65536 * math.log2(1 + i / 16)) for i in range(17))


def spectrum(samples):
    if len(samples) != 512:
        raise ValueError("a complete spectrum frame has exactly 512 samples")
    windowed = [sat(rne(x * h, 16)) for x, h in zip(samples, HANN)]
    powers = []
    for bin_index in range(257):
        real = sat(rne(sum(x * COSINE[(n * bin_index) % 512]
                           for n, x in enumerate(windowed)), 25), 18)
        imag = sat(rne(sum(x * SINE[(n * bin_index) % 512]
                           for n, x in enumerate(windowed)), 25), 18)
        powers.append(min((1 << 32) - 1, real * real + imag * imag))
    return powers


def mel(powers):
    if len(powers) != 257:
        raise ValueError("257 spectrum bins required")
    return [rne(sum(p * w for p, w in zip(powers, weights)), 16)
            for weights in MEL_WEIGHTS]


def log_token(energy):
    if not 0 <= energy < 1 << 48:
        raise ValueError("energy must be unsigned 48-bit")
    if energy == 0:
        return 0
    exponent = energy.bit_length() - 1
    fraction = ((energy << (47 - exponent)) >> 31) & 65535
    interval, residual = divmod(fraction, 4096)
    logarithm = (exponent * 65536 + LOG_ENDPOINTS[interval] +
                 rne((LOG_ENDPOINTS[interval + 1] -
                      LOG_ENDPOINTS[interval]) * residual, 12))
    db = max(-96 * 65536, min(0, rne((logarithm - 30 * 65536) * 197283, 16)))
    return min(65535, rne((db + 96 * 65536) * 21845, 21))


def frontend(samples):
    powers = spectrum(samples)
    return sum(powers[4:129]), [log_token(e) for e in mel(powers)]


class FIR:
    def __init__(self, coefficients, shift=15):
        self.coefficients = list(coefficients)
        self.history = deque([0] * len(coefficients), maxlen=len(coefficients))
        self.shift = shift

    def step(self, sample):
        self.history.appendleft(sample)
        return sat(rne(sum(x * c for x, c in
                           zip(self.history, self.coefficients)), self.shift))


class SOS:
    def __init__(self, sections):
        self.sections = [tuple(c) for c in sections]
        self.history = [[0, 0, 0, 0] for _ in sections]

    def step(self, sample):
        value = sample << 8
        for coefficients, history in zip(self.sections, self.history):
            x1, x2, y1, y2 = history
            b0, b1, b2, a1, a2 = coefficients
            output = sat(rne(b0 * value + b1 * x1 + b2 * x2 -
                             a1 * y1 - a2 * y2, 16), 32)
            history[:] = [value, x1, output, y1]
            value = output
        return sat(rne(value, 8))


def resample(samples, coefficients, ratio_l, ratio_m):
    """Mathematical polyphase schedule: output r uses input floor(r*M/L)."""
    if len(coefficients) != ratio_l or not 1 <= ratio_m <= 8:
        raise ValueError("legal polyphase coefficient set required")
    outputs = []
    r = 0
    while r * ratio_m // ratio_l < len(samples):
        index, phase = divmod(r * ratio_m, ratio_l)
        total = sum(c * (samples[index - j] if index >= j else 0)
                    for j, c in enumerate(coefficients[phase]))
        outputs.append(sat(rne(total, 16)))
        r += 1
    return outputs


def erle(desired, error):
    signal = sum(x * x for x in desired)
    residual = sum(x * x for x in error)
    if signal == 0:
        raise ValueError("ERLE requires nonzero desired energy")
    return None if residual == 0 else 10 * math.log10(signal / residual)
