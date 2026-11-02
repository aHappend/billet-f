"""Contract-derived deterministic stimulus and observed-comparison metrics."""

import math
import random
from pathlib import Path

from oracle import (Beamformer, NLMS, VAD, erle, rne, sat, log_token, spectrum,
                    mel, frontend, FIR, SOS, resample)


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
    # Re-enable without reset so illegal disabled-state weight updates affect public audio.
    for i in range(64):
        far = rng.randint(-4000, 4000)
        append(rne(far, 1), far, "disabled-resume", enabled=True)
    metrics["disabled-resume"] = {"samples": 64}
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


def log_vectors(path, seed=17):
    rng = random.Random(seed)
    energies = [0, 1, 2, 3, (1 << 48)-1]
    for exponent in range(48):
        energies += [(1 << exponent)-1, 1 << exponent, (1 << exponent)+1]
    for interval in range(16):
        for residual in [0, 1, 2047, 2048, 2049, 4094, 4095]:
            energies += [(1 << 30) + ((interval*4096+residual) << 14)]
    energies += [rng.randrange(1 << 48) for _ in range(256)]
    rows = [f"{energy} {log_token(energy)} {i%4} {i%64} "
            f"{int(i%64==63)} {rng.randrange(8)}\n"
            for i, energy in enumerate(energies) if energy < 1 << 48]
    path.write_text(str(len(rows)) + "\n" + "".join(rows))
    return len(rows)


def vad_vectors(path):
    energies = [0, 32767, 32768, 32769, 65535, 65536, 65537,
                32768, 32767, 0, 0, 0, 0, 65536, 32769, 32768,
                32767, 32767, 32767, 32767, 0, 125*((1 << 32)-1), 0]
    model = VAD()
    rows = []
    for i, energy in enumerate(energies):
        rows.append(f"{energy} {model.step(energy)} {i%7} 0\n")
    model = VAD()
    rows += [f"32768 {model.step(32768)} 6 1\n",
             f"65536 {model.step(65536)} 5 0\n",
             f"0 {VAD().step(0)} 3 1\n"]
    path.write_text(str(len(rows)) + "\n" + "".join(rows))
    return len(rows)


def spectral_frames(seed=17):
    rng = random.Random(seed)
    return [
        [0]*512, [1000]*512, [32767]+[0]*511,
        [round(10000*math.sin(2*math.pi*32*n/512)) for n in range(512)],
        [round(5000*math.sin(2*math.pi*9*n/512)+
               3000*math.cos(2*math.pi*71*n/512)) for n in range(512)],
        [rng.randint(-32768, 32767) for _ in range(512)],
    ]


def spectrum_vectors(path, seed=17):
    rows = []
    for channel, samples in enumerate(spectral_frames(seed)):
        powers = spectrum(samples)
        rows.append(f"{channel%4}\n")
        rows.append(" ".join(map(str, samples)) + "\n")
        rows.append(" ".join(map(str, powers)) + "\n")
    path.write_text("6\n" + "".join(rows))
    return 6*257


def mel_vectors(path, seed=17):
    rng = random.Random(seed)
    frames = [spectrum(samples) for samples in spectral_frames(seed)]
    frames += [[(1 << 32)-1]*257, [rng.randrange(1 << 32) for _ in range(257)]]
    rows = []
    for i, powers in enumerate(frames):
        rows.append(f"{i%4}\n")
        rows.append(" ".join(map(str, powers)) + "\n")
        rows.append(" ".join(map(str, mel(powers))) + "\n")
    path.write_text(str(len(frames)) + "\n" + "".join(rows))
    return len(frames)*64


def acoustic_top_vectors(path, seed=17, taps=16, depth=8, compact=False):
    rng = random.Random(seed)
    rows = []
    metrics = {"epochs": []}
    comparisons = 0
    settings = [(1, 1, [0]*8, 1, 12),
                (0, 0, [rng.randrange(depth*256) for _ in range(8)], 1, 2),
                (1, 0, [0]*8, 0, 4)]
    if compact:
        settings = [(0, 0, [0]*8, 1, 1)]
    for epoch, (enabled, gated, delays, ready_mode, frame_count) in enumerate(settings):
        beam = Beamformer(depth, delays)
        aec = NLMS(taps, enabled)
        vad = VAD()
        samples = []
        audio = []
        events = []
        tokens = []
        echo_history = []
        for n in range(frame_count*512):
            frame = n//512
            far = rng.randint(-4000, 4000) if frame in [1, 2, 3] else 0
            echo_history.insert(0, far)
            echo = rne(4*far + 2*(echo_history[3] if len(echo_history)>3 else 0) -
                       (echo_history[7] if len(echo_history)>7 else 0), 3)
            near = (round(12000*math.sin(2*math.pi*1000*n/16000))
                    if compact or frame in [2, 3] or (epoch==1 and frame==0) else 0)
            microphones = [sat(echo+near+rng.randint(-20, 20))
                           if compact or frame in [1, 2, 3] else 0 for _ in range(8)]
            desired = beam.step(microphones)
            output, frozen = aec.step(desired, far)
            samples.append(" ".join(map(str, microphones)) +
                           f" {far} {output} {frozen}\n")
            audio.append(output)
            if (n+1)%512==0:
                energy, frame_tokens = frontend(audio[-512:])
                decision = vad.step(energy)
                events.append(f"{energy} {decision}\n")
                if not gated or decision:
                    tokens += [f"{token} {band}\n" for band, token in
                               enumerate(frame_tokens)]
        count = len(samples)
        rows += [f"{count} {frame_count} {len(tokens)} {enabled} {gated} {ready_mode}\n",
                 " ".join(map(str, delays))+"\n", *samples, *events, *tokens]
        active_frames = sum(int(line.split()[1]) for line in events)
        metrics["epochs"].append({"samples": count, "frames": frame_count,
                                   "active_frames": active_frames,
                                   "suppressed_frames": frame_count-active_frames if gated else 0,
                                   "tokens": len(tokens), "aec_enabled": enabled,
                                   "gate_enabled": gated, "ready_mode": ready_mode})
        comparisons += count+frame_count+len(tokens)+64
    path.write_text(f"{len(settings)} {seed}\n"+"".join(rows))
    return comparisons, metrics


def spatial_cases(seed):
    rng = random.Random(seed)
    size = 4608
    desired = []
    noise = []
    for n in range(size):
        speech = round(4000*math.sin(2*math.pi*437*n/16000)+
                       3000*math.sin(2*math.pi*1133*n/16000))
        desired.append([speech]*8)
        noise.append([rng.randint(-6124, 6124) for _ in range(8)])
    yield "broadside-desired", [0]*8, desired
    yield "broadside-noise", [0]*8, noise
    for angle in [30, -30]:
        delays = [i*16000*.04*math.sin(math.radians(angle))/343 for i in range(8)]
        steering = [round(256*(max(delays)-d)) for d in delays]
        for label, frequency, arrival in [
                ("desired", 1000, delays), ("interferer", 2000, [-d for d in delays])]:
            waves = [[round(6000*math.sin(2*math.pi*frequency*(n-d)/16000))
                      for d in arrival] for n in range(size)]
            yield f"angle{angle}-{label}", steering, waves


def spatial_vectors(path, delays, microphones):
    beam = Beamformer(8, delays)
    outputs = [beam.step(row) for row in microphones]
    rows = []
    for n, (row, output) in enumerate(zip(microphones, outputs)):
        prefix = "1 "+" ".join(map(str, delays))+" " if n==0 else "0 "
        rows.append(prefix+" ".join(map(str, row))+f" 0 {output} 0\n")
    path.write_text(str(len(rows))+"\n"+"".join(rows))
    # Only post-warmup samples contribute to the acoustic measurements.
    output_energy = sum(x*x for x in outputs[512:])
    input_energy = [sum(row[i]*row[i] for row in microphones[512:]) for i in range(8)]
    return len(rows), {"output_energy": output_energy, "input_energy": input_energy,
                       "measured_samples": len(outputs)-512, "delays_q8": delays}


def assert_spatial(metrics):
    desired = metrics["broadside-desired"]
    noise = metrics["broadside-noise"]
    input_snr_db = sum(10*math.log10(s/n) for s, n in
                       zip(desired["input_energy"], noise["input_energy"]))/8
    output_snr_db = 10*math.log10(desired["output_energy"]/noise["output_energy"])
    gain = output_snr_db-input_snr_db
    if gain < 7:
        raise AssertionError(f"broadside SNR improvement {gain:.6f} < 7 dB")
    result = {"broadside_snr_improvement_db": gain,
              "mean_input_snr_db": input_snr_db, "output_snr_db": output_snr_db}
    for angle in [30, -30]:
        desired = metrics[f"angle{angle}-desired"]
        interferer = metrics[f"angle{angle}-interferer"]
        input_ratio = sum(desired["input_energy"])/sum(interferer["input_energy"])
        output_ratio = desired["output_energy"]/interferer["output_energy"]
        sir_gain = 10*math.log10(output_ratio/input_ratio)
        desired_gain = 10*math.log10(desired["output_energy"]/
                                    (sum(desired["input_energy"])/8))
        if abs(desired_gain)>1 or sir_gain<6:
            raise AssertionError(f"angle {angle}: desired gain={desired_gain:.6f} dB, "
                                 f"SIR improvement={sir_gain:.6f} dB")
        result[f"angle{angle}"] = {"desired_gain_db": desired_gain,
                                  "sir_improvement_db": sir_gain}
    return result


def src_vectors(path, taps=1, seed=17, all_ratios=True):
    rng=random.Random(seed)
    pairs=[(l,m) for l in range(1,9) for m in range(1,9)] if all_ratios else [
        (1,1),(8,8),(7,3)]
    rows=[]
    compared=0
    for case,(l,m) in enumerate(pairs):
        coefficients=[]
        for phase in range(l):
            coefficients.append([65536+phase*4096 if j==0 else
                                 rng.randint(-1024,1024) for j in range(taps)])
        samples=[rng.randint(-32768,32767) for _ in range(32)]
        expected=resample(samples,coefficients,l,m)
        rows += [f"{l} {m} {case%4} {len(samples)} {len(expected)}\n",
                 " ".join(str(c) for row in coefficients for c in row)+"\n",
                 " ".join(map(str,samples))+"\n",
                 " ".join(map(str,expected))+"\n"]
        compared+=len(expected)
    path.write_text(str(len(pairs))+"\n"+"".join(rows))
    return compared,{"ratios":pairs,"taps":taps,"samples_per_channel_case":32}


def legacy_vectors(path,taps=1,sos_count=1,src_taps=1,seed=17,full=True):
    rng=random.Random(seed)
    actions=[]
    compared=0
    fir_coeffs=[[rng.randint(-4096,4096) for _ in range(taps)] for _ in range(4)]
    for channel in range(4):
        fir_coeffs[channel][0]=16384+channel*1024
    sos_coeffs=[[(65536,8192,-4096,-8192,2048)] +
                [(65536,0,0,0,0)]*(sos_count-1) for _ in range(4)]
    fir=[FIR(c) for c in fir_coeffs]
    sos=[SOS(c) for c in sos_coeffs]
    for channel in range(4):
        actions.append(f"1 {channel} "+" ".join(map(str,fir_coeffs[channel]))+"\n")
        actions.append(f"2 {channel} "+
                       " ".join(str(c) for row in sos_coeffs[channel] for c in row)+"\n")
    for mode in range(5):
        sections=0 if mode<2 else sos_count
        actions.append(f"4 {mode} {sections} 1\n"); compared+=1
        sos=[SOS(c) for c in sos_coeffs]
        for n in range(64 if full else 8):
            channel=n%4
            sample=rng.randint(-32768,32767)
            expected=sample
            if mode in [1,3]:
                expected=fir[channel].step(expected)
            if mode in [2,3,4]:
                expected=sos[channel].step(expected)
            if mode==4:
                expected=fir[channel].step(expected)
            actions.append(f"6 {channel} {sample} {expected} {rng.randrange(8)}\n")
            compared+=1
        if mode==1:
            # Bank swap retains channel FIR history, unlike the SOS bank swap.
            new=[-c for c in fir_coeffs[0]]
            actions.append("1 0 "+" ".join(map(str,new))+"\n")
            fir_coeffs[0]=new; fir[0].coefficients=new
    for mode,sections in [(5,0),(0,1),(2,0),(2,sos_count+1)]:
        actions.append(f"4 {mode} {sections} 0\n"); compared+=1
    actions.append("4 0 0 1\n"); compared+=1
    actions.append("5 1\n")
    for n in range(16):
        value=rng.randint(-32768,32767)
        actions.append(f"6 {n%4} {value} {value} {rng.randrange(8)}\n")
        compared+=1
    if full:
        actions.append("9\n")
        fir_c=[16384]+([8192] if taps>1 else [])+([0]*(taps-2) if taps>1 else [])
        sos_c=[(65536,8192,0,-8192,0)]+[(65536,0,0,0,0)]*(sos_count-1)
        src_c=[65536]+[0]*(src_taps-1)
        actions += ["1 0 "+" ".join(map(str,fir_c))+"\n",
                    "2 0 "+" ".join(str(c) for row in sos_c for c in row)+"\n",
                    "3 0 1 "+" ".join(map(str,src_c))+"\n",
                    f"4 3 {sos_count} 1\n","5 2\n"]
        compared+=1
        input_frame=[round(9000*math.sin(2*math.pi*1000*n/16000))+
                     rng.randint(-1000,1000) for n in range(512)]
        f_model,s_model=FIR(fir_c),SOS(sos_c)
        after=[s_model.step(f_model.step(value)) for value in input_frame]
        transformed=resample(after,[src_c],1,1)
        _,tokens=frontend(transformed)
        actions += ["8 0 "+" ".join(map(str,input_frame))+" "+
                    " ".join(map(str,tokens))+"\n","5 3\n","10\n"]
        compared+=64+1
        powers=[rng.randrange(1<<32) for _ in range(257)]
        tokens=[log_token(energy) for energy in mel(powers)]
        actions.append("7 3 "+" ".join(map(str,powers))+" "+
                       " ".join(map(str,tokens))+"\n")
        compared+=64
    path.write_text(str(len(actions))+"\n"+"".join(actions))
    return compared,{"chain_modes":[0,1,2,3,4],"channels":[0,1,2,3],
                     "tap_count":taps,"sos_count":sos_count,"src_taps":src_taps,
                     "full_chain_frame":full,"malformed_recovery":full}


def pipeline_vectors(path,depth=17,seed=17):
    rng=random.Random(seed)
    delays=[rng.randrange(depth*256) for _ in range(8)]
    delays[0]=0; delays[7]=(depth-1)*256+255
    model=Beamformer(depth,delays)
    rows=[]
    for n in range(512):
        microphones=[rng.randint(-32768,32767) for _ in range(8)]
        far=rng.randint(-32768,32767)
        rows.append(" ".join(map(str,microphones))+f" {far} {model.step(microphones)}\n")
    path.write_text("512\n"+" ".join(map(str,delays))+"\n"+"".join(rows))
    return len(rows)
