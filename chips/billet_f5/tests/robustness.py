"""Executable parameter, cycle, reset, configuration and backpressure coverage."""

import math
import random

from compatibility import MATRIX
from oracle import Beamformer, NLMS, FIR, SOS, frontend, resample
from scenarios import (beamformer_vectors, pipeline_vectors, vad_vectors,
                       log_vectors, acoustic_top_vectors)
from simulation import compile_tb, execute_tb, clean_compiler_intermediates


ILLEGAL = {"TAPS": (0, 3), "LANES": (0, 3), "SOS": (0, 5),
           "SRC": (0, 3), "DEPTH": (0, 2), "AEC": (0, 3)}


def run_parameters(evidence):
    evidence.versions([("iverilog", "-V"), ("vvp", "-V")])
    vectors = evidence.path / "parameter-vectors.txt"
    vectors.write_text("1\n")
    for index, parameters in enumerate(MATRIX):
        name = f"legal-parameters-{index}"
        binary = compile_tb(evidence, "parameter_tb", parameters, "iverilog", name=name)
        execute_tb(evidence, binary, vectors, name, 1, "iverilog")
    rejections = []
    for parameter, values in ILLEGAL.items():
        for value in values:
            name = f"illegal-{parameter}-{value}"
            binary = compile_tb(evidence, "parameter_tb", {parameter: value},
                                "iverilog", name=name)
            code, out, err = evidence.run(
                ["vvp", evidence.relative(binary)], name+"-simulate", expected_exit=None)
            text = out.read_text(errors="replace") + err.read_text(errors="replace")
            if code == 0 or "PARAMETER ERROR" not in text:
                raise AssertionError(f"{name}: missing nonzero PARAMETER ERROR rejection")
            evidence.compared(name, 1, kind="diagnostic-rejection", exit_code=code)
            rejections.append({parameter: value})
    evidence.record["coverage"]["legal_elaboration_matrix"] = MATRIX
    evidence.record["coverage"]["illegal_runtime_rejections"] = rejections
    evidence.save()


def nlms_reset_vectors(path, taps, seed):
    rng = random.Random(seed)
    model = NLMS(taps)
    rows = []
    for n in range(80):
        mode = 2 if n in (8, 40) else 0
        if mode:
            model = NLMS(taps)
        far = rng.randint(-32768, 32767)
        desired = far // 2 if n % 8 else (-32768 if n % 16 else 32767)
        output, frozen = model.step(desired, far)
        rows.append(f"{desired} {far} {output} {frozen} 1 {n%5} {mode}\n")
    path.write_text(str(len(rows))+"\n"+"".join(rows))
    return len(rows), {"aborted_mid_division": 2, "taps": taps}


def boundary_vectors(path, depth, seed):
    beam = Beamformer(depth, [(depth-1)*256+255]*8)
    after_commit = beam.step([-500]*8)
    samples = [round(12000*math.sin(2*math.pi*1000*n/16000)) for n in range(512)]
    aec = NLMS(1)
    audio = [aec.step(sample, 0)[0] for sample in samples]
    if audio != samples:
        raise AssertionError("reference-silence boundary fixture is not frozen")
    energy, tokens = frontend(audio)
    if energy < 65536:
        raise AssertionError("reset-recovery speech fixture failed to activate VAD")
    path.write_text(f"{after_commit} {energy}\n"+
                    " ".join(map(str, samples))+"\n"+
                    " ".join(map(str, tokens))+"\n")
    # Four directed audio transactions plus a complete speech frame and its event/tokens.
    return 4+512+1+64, {"delay_depth": depth, "reset_mid_frame": True,
                       "withdrawn_commit": True, "partial_frame_commit": True}


def rounding_vectors(path):
    rows = []
    configurations = [
        ([128]*8, [[1]*8, [4]*8, [-1]*8, [-4]*8, [32767]*8, [-32768]*8]),
        ([0]*8, [[1]*4+[0]*4, [3]*4+[0]*4, [-1]*4+[0]*4, [-3]*4+[0]*4]),
        ([255]*8, [[32767]*8, [-32768]*8, [1]*8, [-1]*8]),
    ]
    for delays, microphones in configurations:
        model = Beamformer(1, delays)
        for n, samples in enumerate(microphones):
            prefix = "1 "+" ".join(map(str, delays))+" " if n == 0 else "0 "
            rows.append(prefix+" ".join(map(str, samples))+
                        f" 0 {model.step(samples)} 3\n")
    path.write_text(str(len(rows))+"\n"+"".join(rows))
    return len(rows)


def nlms_saturation_vectors(path):
    model = NLMS(1)
    samples = [(0, 32767)]*64 + [(32767, 512), (0, 123),
                                (32767, -512), (0, 123),
                                (-32768, 0), (32767, 0)]
    rows = []
    saturated = set()
    for n, (desired, far) in enumerate(samples):
        output, frozen = model.step(desired, far)
        if model.weights[0] in (-(1 << 23), (1 << 23)-1):
            saturated.add(model.weights[0])
        rows.append(f"{desired} {far} {output} {frozen} 1 {n%4} 0\n")
    if saturated != {-(1 << 23), (1 << 23)-1}:
        raise AssertionError("signed weight-saturation fixture did not reach both limits")
    path.write_text(str(len(rows))+"\n"+"".join(rows))
    return len(rows)


def legacy_saturation_vectors(path):
    rows = []
    compared = 0
    saturated = {32767: 0, -32768: 0}
    samples = [32767]*8+[-32768]*8+[32767, -32768, -1, 1]*4
    for mode in (1, 2, 0):
        rows.append("9\n")
        if mode == 1:
            coefficients = [32767]*8
            rows.append("1 0 "+" ".join(map(str, coefficients))+"\n")
            model = FIR(coefficients)
            expected = [model.step(sample) for sample in samples]
        elif mode == 2:
            coefficients = [(131071, 0, 0, 0, 0)]
            rows.append("2 0 131071 0 0 0 0\n")
            model = SOS(coefficients)
            expected = [model.step(sample) for sample in samples]
        else:
            coefficients = [131071]+[0]*7
            rows.append("3 0 1 "+" ".join(map(str, coefficients))+"\n")
            expected = resample(samples, [coefficients], 1, 1)
        rows.append(f"4 {mode} {int(mode==2)} 1\n")
        compared += 1
        if mode == 0:
            rows.append("5 1\n")
        for n, (sample, output) in enumerate(zip(samples, expected)):
            rows.append(f"6 0 {sample} {output} {n%7}\n")
            compared += 1
            if output in saturated:
                saturated[output] += 1
    if any(count == 0 for count in saturated.values()):
        raise AssertionError("inherited positive/negative saturation comparisons missing")
    path.write_text(str(len(rows))+"\n"+"".join(rows))
    return compared, {"signed_output_saturations": saturated, "engines": ["FIR", "SOS", "SRC"]}


def run_robustness(evidence):
    run_parameters(evidence)
    evidence.versions([("verilator", "--version")])
    vectors = evidence.path / "descriptor.txt"
    vectors.write_text("13\n")
    binary = compile_tb(evidence, "descriptor_tb", simulator="iverilog", name="descriptors")
    execute_tb(evidence, binary, vectors, "descriptors", 13, "iverilog")
    vectors = evidence.path / "legacy-saturation.txt"
    count, metrics = legacy_saturation_vectors(vectors)
    binary = compile_tb(evidence, "legacy_tb", {"TAPS": 8, "SRC": 8},
                        name="legacy-saturation")
    execute_tb(evidence, binary, vectors, "legacy-saturation", count)
    clean_compiler_intermediates(binary)
    evidence.record["coverage"]["legacy-saturation"] = metrics
    for name, top, parameters, generator in (
            ("signed-rounding-ties", "beamformer_tb", {"DEPTH": 1}, rounding_vectors),
            ("signed-weight-saturation", "nlms_tb", {"TAPS": 1}, nlms_saturation_vectors)):
        vectors = evidence.path / (name+".txt")
        count = generator(vectors)
        binary = compile_tb(evidence, top, parameters, name=name)
        execute_tb(evidence, binary, vectors, name, count)
        clean_compiler_intermediates(binary)
    for depth in (1, 8, 17, 32):
        for leaf, generator in (
                ("beamformer", beamformer_vectors),
                ("beamformer_pipeline", pipeline_vectors)):
            name = f"{leaf}-depth{depth}"
            vectors = evidence.path / (name+".txt")
            generated = generator(vectors, depth, 17)
            count = generated[0] if isinstance(generated, tuple) else generated
            binary = compile_tb(evidence, leaf+"_tb", {"DEPTH": depth}, name=name)
            execute_tb(evidence, binary, vectors, name, count)
            clean_compiler_intermediates(binary)
    for taps in (1, 8, 16, 32):
        name = f"division-reset-taps{taps}"
        vectors = evidence.path / (name+".txt")
        count, metrics = nlms_reset_vectors(vectors, taps, 17)
        binary = compile_tb(evidence, "nlms_tb", {"TAPS": taps}, name=name)
        execute_tb(evidence, binary, vectors, name, count)
        clean_compiler_intermediates(binary)
        evidence.record["coverage"][name] = metrics
    for leaf, generator in (("vad", vad_vectors), ("log", log_vectors)):
        name = leaf+"-cycle-boundaries"
        vectors = evidence.path / (name+".txt")
        count = generator(vectors)
        binary = compile_tb(evidence, leaf+"_tb", simulator="iverilog", name=name)
        execute_tb(evidence, binary, vectors, name, count, "iverilog")
    vectors = evidence.path / "fork.txt"
    vectors.write_text("256\n")
    for simulator in ("iverilog", "verilator"):
        name = "fork-"+simulator
        binary = compile_tb(evidence, "fork_tb", simulator=simulator, name=name)
        execute_tb(evidence, binary, vectors, name, 256, simulator)
        clean_compiler_intermediates(binary)
    for depth in (1, 17, 32):
        name = f"epoch-boundaries-depth{depth}"
        vectors = evidence.path / (name+".txt")
        count, metrics = boundary_vectors(vectors, depth, 17)
        binary = compile_tb(evidence, "boundary_tb", {"DEPTH": depth}, name=name)
        execute_tb(evidence, binary, vectors, name, count)
        clean_compiler_intermediates(binary)
        evidence.record["coverage"][name] = metrics
    vectors = evidence.path / "frame-stalls.txt"
    count, metrics = acoustic_top_vectors(vectors, 17, taps=1, depth=17)
    binary = compile_tb(evidence, "acoustic_top_tb", {"DEPTH": 17, "AEC": 1},
                        name="frame-stalls")
    execute_tb(evidence, binary, vectors, "frame-stalls", count)
    clean_compiler_intermediates(binary)
    evidence.record["coverage"]["frame_stalls"] = metrics
    evidence.save()
