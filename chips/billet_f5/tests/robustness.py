"""Executable parameter, cycle, reset, configuration and backpressure coverage."""

import math
import random

from compatibility import MATRIX
from oracle import Beamformer, NLMS, frontend
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


def run_robustness(evidence):
    run_parameters(evidence)
    evidence.versions([("verilator", "--version")])
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
