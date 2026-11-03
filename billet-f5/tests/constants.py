"""Compare mathematically reconstructed coefficients with every shipped ROM entry."""

import re

from oracle import HANN, COSINE, SINE, MEL_WEIGHTS, LOG_ENDPOINTS, signed, rne


def assert_constants(root):
    rtl = root / "rtl"
    text = (rtl / "billet_f4_hann_rom.sv").read_text()
    actual = {int(k): int(v) for k, v in
              re.findall(r"9'd(\d+): coefficient <= 17'd(\d+);", text)}
    if actual != dict(enumerate(HANN)):
        raise AssertionError("Hann ROM differs from mathematical oracle")
    text = (rtl / "billet_f4_twiddle_rom.sv").read_text()
    actual = {int(k): int(v, 16) for k, v in
              re.findall(r"9'd(\d+): value_q <= 36'h([0-9a-f]+);", text)}
    if set(actual) != set(range(512)):
        raise AssertionError("twiddle ROM entry coverage incomplete")
    for k, v in actual.items():
        if (signed(v >> 18, 18), signed(v, 18)) != (COSINE[k], SINE[k]):
            raise AssertionError(f"twiddle coefficient mismatch q={k}")
    text = (rtl / "billet_f4_mel_rom.sv").read_text()
    actual = {int(k): int(v, 16) for k, v in
              re.findall(r"9'd(\d+): value_q <= 48'h([0-9a-f]+);", text)}
    if set(actual) != set(range(257)):
        raise AssertionError("Mel ROM bin coverage incomplete")
    for k, v in actual.items():
        encoded = [(v >> 41, (v >> 24) & 131071),
                   ((v >> 17) & 127, v & 131071)]
        decoded = [0] * 64
        for band, weight in encoded:
            if band == 127:
                if weight != 0:
                    raise AssertionError("nonzero absent Mel band")
            elif band < 64:
                decoded[band] += weight
            else:
                raise AssertionError("illegal encoded Mel band")
        if decoded != [row[k] for row in MEL_WEIGHTS]:
            raise AssertionError(f"Mel coefficient mismatch bin={k}")
    text = (rtl / "billet_f4_log_rom.sv").read_text()
    actual = {int(k): (int(a), int(b)) for k, a, b in re.findall(
        r"4'd(\d+): begin base=17'd(\d+); following=17'd(\d+); end", text)}
    if actual != {i: (LOG_ENDPOINTS[i], LOG_ENDPOINTS[i+1]) for i in range(16)}:
        raise AssertionError("log ROM differs from mathematical oracle")
    return {"hann": 512, "twiddles": 1024, "mel_weights": 257*64,
            "log_endpoints": 17}


def assert_oracle_primitives():
    cases = [(-5, 1, -2), (-3, 1, -2), (-1, 1, 0), (1, 1, 0),
             (3, 1, 2), (5, 1, 2), (32768, 16, 0), (-32768, 16, 0),
             (98304, 16, 2), (-98304, 16, -2)]
    for value, shift, expected in cases:
        if rne(value, shift) != expected:
            raise AssertionError("independent RNE analytical fixture failed")
    return len(cases)
