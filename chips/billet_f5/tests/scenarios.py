"""Contract-derived deterministic stimulus and observed-comparison metrics."""

import math
import random
from pathlib import Path

from oracle import Beamformer, NLMS, erle, rne, sat


def nlms_vectors(path, taps, seed, full=True):
    rng = random.Random(seed)
    model = NLMS(taps)
    rows = []
    metrics = {}
    phase_desired = []
    phase_errors = []
    far_history = []
    total = 0

    def append(desired, far, phase, enabled=True, reset=False):
        nonlocal model, total
        if reset:
            model = NLMS(taps, enabled)
        model.enabled = enabled
        error, frozen = model.step(desired, far)
        rows.append(f"{desired} {far} {error} {frozen} {int(enabled)} "
                    f"{rng.randrange(4)} {int(reset)}\n")
        total += 1
        return error, frozen

    # Startup, low-reference, full-scale and signed rounding/headroom.
    for desired, far in [(0, 0), (-32768, 0), (32767, 0),
                         (-1, 32767), (1, -32768), (-32768, -32768),
                         (32767, 32767), (0, 0)]:
        append(desired, far, "boundary")
    append(0, 0, "training-reset", reset=True)
    lengths = ([16384, 4096, 1024, 2048, 4096] if full else
               [128, 32, 32, 32, 32])
    freeze_tail = []
    phases = ["training", "evaluation", "double-talk", "recovery", "re-evaluation"]
    for phase, length in zip(phases, lengths):
        desired_values, error_values = [], []
        for i in range(length):
            far = rng.randint(-4000, 4000)
            far_history.insert(0, far)
            if taps == 1:
                echo = rne(far, 1)
            else:
                echo = rne(4 * far +
                           2 * (far_history[3] if len(far_history) > 3 else 0) -
                           (far_history[7] if len(far_history) > 7 else 0), 3)
            near = rng.randint(-12000, 12000) if phase == "double-talk" else 0
            desired = sat(echo + near)
            error, frozen = append(desired, far, phase)
            desired_values.append(echo)
            error_values.append(error)
            if phase == "double-talk" and i >= length - min(512, length):
                freeze_tail.append(frozen)
        metrics[phase] = {"samples": length}
        if phase in {"evaluation", "re-evaluation"}:
            measured = erle(desired_values, error_values)
            metrics[phase].update(
                desired_energy=sum(x*x for x in desired_values),
                residual_energy=sum(x*x for x in error_values),
                erle_db=measured, exact_zero_residual=measured is None)
    metrics["double-talk"]["frozen_tail"] = sum(freeze_tail)
    metrics["double-talk"]["tail_samples"] = len(freeze_tail)
    metrics["double-talk"]["freeze_fraction"] = sum(freeze_tail) / len(freeze_tail)
    # A nonzero trained state is allowed to leak only on nonfrozen samples.
    leakage_before = tuple(model.weights)
    leakage_compared = 0
    for i in range(64):
        far = rng.randint(-4000, 4000)
        _, frozen = append(0, far, "leakage")
        if not frozen:
            leakage_compared += 1
    metrics["leakage"] = {"nonzero_start_weights": sum(w != 0 for w in leakage_before),
                          "adapting_samples": leakage_compared}
    silence_frozen = 0
    for i in range(512):
        _, frozen = append(0, 0, "reference-silence")
        if i >= 384:
            silence_frozen += frozen
    metrics["reference-silence"] = {"final128_frozen": silence_frozen}
    for i in range(64):
        append(rng.randint(-32768, 32767), rng.randint(-32768, 32767),
               "disabled", enabled=False)
    metrics["disabled"] = {"samples": 64}
    path.write_text(str(len(rows)) + "\n" + "".join(rows))
    return len(rows), metrics


def assert_convergence(metrics):
    for phase, threshold in [("evaluation", 20), ("re-evaluation", 18)]:
        value = metrics[phase]["erle_db"]
        if value is not None and value < threshold:
            raise AssertionError(f"{phase}: ERLE {value:.6f} < {threshold} dB")
    if metrics["double-talk"]["freeze_fraction"] < .95:
        raise AssertionError("double-talk: freeze fraction below 0.95")
    if metrics["reference-silence"]["final128_frozen"] != 128:
        raise AssertionError("reference-silence freeze not observed")
    if metrics["leakage"]["nonzero_start_weights"] == 0 or \
            metrics["leakage"]["adapting_samples"] == 0:
        raise AssertionError("nonzero-weight leakage comparisons missing")


def beamformer_vectors(path, depth, seed, samples=256):
    rng = random.Random(seed)
    rows = []
    model = Beamformer(depth)
    epochs = 0
    for n in range(samples):
        mode = int(n % 64 == 0)
        prefix = str(mode) + " "
        if mode:
            if n == 0:
                delays = [0, 1, 127, 128, 129, 254, 255, (depth-1)*256+255]
            elif n == 64:
                delays = [(depth-1)*256 + 255] * 8
            elif n == 128:
                delays = [i * 256 % (depth*256) for i in range(8)]
            else:
                delays = [rng.randrange(depth*256) for _ in range(8)]
            model = Beamformer(depth, delays)
            prefix += " ".join(map(str, delays)) + " "
            epochs += 1
        microphones = ([(-32768 if i & 1 else 32767) for i in range(8)]
                       if n % 8 == 0 else
                       [rng.randint(-32768, 32767) for _ in range(8)])
        far = rng.randint(-32768, 32767)
        expected = model.step(microphones)
        rows.append(prefix + " ".join(map(str, microphones)) +
                    f" {far} {expected} {rng.randrange(6)}\n")
    path.write_text(str(len(rows)) + "\n" + "".join(rows))
    return len(rows), {"epochs": epochs, "depth": depth}
