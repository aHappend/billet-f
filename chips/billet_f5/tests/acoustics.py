"""The admitted per-seed acoustic comparisons and quantitative thresholds."""

from scenarios import (nlms_vectors, assert_convergence, beamformer_vectors,
                       spatial_cases, spatial_vectors, assert_spatial,
                       acoustic_top_vectors)
from simulation import compile_tb, execute_tb, clean_compiler_intermediates


def run_acoustics(evidence, seed):
    if seed not in (17, 29, 43):
        raise ValueError("admitted acoustic seeds are 17, 29 and 43")
    evidence.versions([("verilator", "--version")])
    evidence.record["metrics"]["nlms"] = {}
    for taps in (1, 8, 16, 32):
        stem = f"nlms-t{taps}-seed{seed}"
        vectors = evidence.path / (stem+".txt")
        count, metrics = nlms_vectors(vectors, taps, seed)
        evidence.record["metrics"]["nlms"][str(taps)] = metrics
        evidence.save()
        binary = compile_tb(evidence, "nlms_tb", {"TAPS": taps}, name=stem)
        execute_tb(evidence, binary, vectors, stem, count)
        assert_convergence(metrics)
        clean_compiler_intermediates(binary)
    for depth in (1, 8, 17, 32):
        stem = f"beamformer-d{depth}-seed{seed}"
        vectors = evidence.path / (stem+".txt")
        count, metrics = beamformer_vectors(vectors, depth, seed, samples=512)
        binary = compile_tb(evidence, "beamformer_tb", {"DEPTH": depth}, name=stem)
        execute_tb(evidence, binary, vectors, stem, count)
        clean_compiler_intermediates(binary)
    binary = compile_tb(evidence, "beamformer_tb", {"DEPTH": 8}, name="spatial")
    spatial_metrics = {}
    for stem, delays, microphones in spatial_cases(seed):
        vectors = evidence.path / (stem+".txt")
        count, metrics = spatial_vectors(vectors, delays, microphones)
        execute_tb(evidence, binary, vectors, stem, count)
        spatial_metrics[stem] = metrics
    clean_compiler_intermediates(binary)
    evidence.record["metrics"]["spatial_runs"] = spatial_metrics
    evidence.record["metrics"]["spatial_acceptance"] = assert_spatial(spatial_metrics)
    vectors = evidence.path / "acoustic-top.txt"
    count, metrics = acoustic_top_vectors(vectors, seed, taps=32)
    binary = compile_tb(evidence, "acoustic_top_tb", {"AEC": 32}, name="acoustic-top")
    execute_tb(evidence, binary, vectors, "acoustic-top", count)
    clean_compiler_intermediates(binary)
    first_epoch = metrics["epochs"][0]
    if first_epoch["frames"]<12 or first_epoch["suppressed_frames"]==0 or \
            first_epoch["active_frames"]==0:
        raise AssertionError("observed emitted/suppressed complete-frame coverage missing")
    evidence.record["metrics"]["frontend"] = metrics
    evidence.record["coverage"].update(
        aec_taps_executed=[1,8,16,32], delay_depth_executed=[1,8,17,32],
        spatial_samples_per_run=4096, steering_degrees=[30,-30],
        complete_frames=sum(row["frames"] for row in metrics["epochs"]))
    evidence.save()
