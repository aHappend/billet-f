"""Deterministic directed cases followed by seeded, bounded traffic."""

import math
import random

from reference import FREQUENCIES


def reset(c):
    c.tick(rst=0)
    c.tick()


def isolated(c, value, label=""):
    for _ in range(c.t):
        c.send(0)
    c.send(value, label=label)
    for _ in range(c.t):
        c.send(0)
    c.drain()


def run_scenario(c, banks, seed, full=True):
    rng = random.Random(seed)
    reset(c)
    c.send(32767, label="initial_zero_bank")
    c.drain()
    if c.t == 1:
        assert c.tick(cv=1, ca=1, cd=123)[2] == 0
        c.hit("invalid_address")

    # Incomplete commits prevent writes and sample acceptance; an incomplete
    # request must be withdrawn before configuration can continue.
    if c.t > 1:
        c.tick(cv=1, ca=0, cd=31)
    for _ in range(7):
        pre = c.tick(cm=1, cv=1, ca=0, cd=99)
        assert pre[0] == pre[2] == pre[3] == 0
    c.tick(cv=1, ca=0, cd=99)
    c.tick(cv=1, ca=0, cd=27)
    c.tick(cv=1, ca=0, cd=29)
    reset(c)  # partial programming, including the singleton's sole address

    # Reset after an accepted sample, before its first MAC edge (also T=1).
    c.program(banks["triangular"]["coefficients"])
    c.commit()
    c.send(12345)
    c.tick(async_pulse=True)
    c.tick()
    if c.t > 1:
        c.send(-23456)
        c.tick()
        c.tick(rst=0)
        c.tick()
    c.program(banks["impulse"]["coefficients"])
    c.commit()
    c.send(32767, mr=0)
    for _ in range(c.t + 1):
        c.tick(mr=0)
    assert c.pending is not None
    c.tick(async_pulse=True)
    c.tick()

    # At least 64 consecutive transfers, with simultaneous retire/accept edges.
    c.program(banks["triangular"]["coefficients"])
    c.commit()
    for i in range(72):
        c.send((i * 997) % 65536 - 32768, window=True)
    c.drain()

    for name in ("impulse", "moving_average", "triangular", "alternating_triangular", "stress"):
        c.program(banks[name]["coefficients"])
        c.commit()
        isolated(c, 32767, "positive_impulse")
        isolated(c, -32768, "negative_impulse")
        for value, label in ((8192, "positive_dc"), (-8192, "negative_dc"),
                             (0, "zero"), (32767, "positive_fullscale"),
                             (-32768, "negative_fullscale")):
            for _ in range(c.t + 8):
                c.send(value, label=label)
        for i in range(c.t + 16):
            c.send(32767 if i % 2 else -32768, label="alternating_extremes")
        c.drain()

    # Exact-half cases of both signs/floor-quotient parities, plus both sides.
    for coeff in (16384, 16383, 16385):
        c.program([coeff] + [0] * (c.t - 1))
        c.commit()
        for value in (1, 3, -1, -3):
            c.send(value, label="rounding_boundary")
        c.drain()
    c.program([-32768] * c.t)
    c.commit()
    for _ in range(c.t + 2):
        c.send(-32768, label="full_accumulator_positive")
    if c.t > 1:
        for _ in range(c.t + 2):
            c.send(32767, label="full_accumulator_negative")
    c.drain()

    # Stage during MAC activity, overwrite an address, hold commit through a
    # long output stall, and prove it cannot share the retirement edge.
    c.program(banks["triangular"]["coefficients"])
    c.commit()
    c.send(22001, mr=0)
    c.tick(cv=1, ca=0, cd=-12345, mr=0)
    assert c.coverage.get("write_during_computation", 0)
    c.program(banks["alternating_triangular"]["coefficients"])
    # program() may drain the earlier output: establish a fresh blocked result.
    c.send(-17777, mr=0)
    assert len(c.written) == c.t
    for _ in range(c.t + 70):
        c.tick(cm=1, mr=0)
    assert c.pending is not None
    before = c.commits
    pre = c.tick(cm=1, mr=1)
    assert pre[1] and not pre[3] and c.commits == before
    assert c.tick(cm=1)[3]
    assert c.commits == before + 1 and any(c.history)
    c.send(19191, label="post_commit_retained_history")
    c.drain()

    # One-cycle stall is separate from the long stall.
    c.send(777, mr=0)
    for _ in range(c.t):
        c.tick(mr=0)
    assert c.pending is not None
    pre = c.tick(mr=0)
    assert pre[1]
    c.hit("single_cycle_stall")
    c.tick()
    for _ in range(80):
        assert c.tick()[0]
    c.hit("long_bubble")

    c.program([rng.randint(-32768, 32767) for _ in range(c.t)])
    c.commit()
    for _ in range(1200 if full else 96):
        if rng.randrange(3) == 0:
            for _ in range(rng.randrange(1, 6)):
                c.tick(mr=rng.randrange(2))
            c.hit("random_bubble")
        value = rng.randint(-32768, 32767)
        # Ready may vary on every waiting edge, but the producer holds payload.
        for _ in range(c.t + 256):
            if c.tick(sv=1, sd=value, mr=int(rng.randrange(5) != 0), random_sample=True)[0]:
                break
        else:
            raise AssertionError("random traffic nonprogress")
    c.drain()

    for name in ("moving_average", "triangular"):
        c.program(banks[name]["coefficients"])
        c.commit()
        for freq in FREQUENCIES:
            for n in range(c.t + (512 if full else 16)):
                x = round(24000 * math.cos(2 * math.pi * freq * n / 64))
                c.send(x, tone=f"{name}:{freq}/64" if n >= c.t else "")
            c.drain()
    c.drain()
    return c.summary(full)


def witness(c, banks, mutation):
    reset(c)
    if mutation == "tap_order":
        c.program(banks["triangular"]["coefficients"])
        c.commit()
        c.send(30001)
        c.send(-17003)
    elif mutation == "rounding":
        c.program([16384] + [0] * (c.t - 1))
        c.commit()
        c.send(3)
        c.send(-3)
    elif mutation == "coefficient_update":
        c.program([4096] * c.t)
        c.commit()
        for _ in range(c.t):
            c.send(20000)
        c.drain()
        c.send(25000)
        c.tick()  # tap zero has executed; write the coefficient of a later tap
        c.tick(cv=1, ca=c.t - 1, cd=-32768)
    else:
        raise ValueError(mutation)
    c.drain()
