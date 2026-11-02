"""Independent current-generation regression of all inherited engines."""

from scenarios import (legacy_vectors, src_vectors, spectrum_vectors, mel_vectors,
                       log_vectors, beamformer_vectors, nlms_vectors)
from simulation import compile_tb, execute_tb, clean_compiler_intermediates


MATRIX=[
    {"TAPS":1,"LANES":1,"SOS":1,"SRC":1,"DEPTH":1,"AEC":1},
    {"TAPS":8,"LANES":2,"SOS":2,"SRC":8,"DEPTH":8,"AEC":8},
    {"TAPS":16,"LANES":4,"SOS":3,"SRC":16,"DEPTH":17,"AEC":16},
    {"TAPS":32,"LANES":1,"SOS":4,"SRC":32,"DEPTH":32,"AEC":32},
    {"TAPS":64,"LANES":2,"SOS":1,"SRC":64,"DEPTH":8,"AEC":16},
]


def run_compatibility(evidence):
    evidence.versions([("iverilog","-V"),("vvp","-V"),("verilator","--version")])
    # Compact two-simulator portability stays well below 500,000 cycles.
    for leaf,parameters,generator in [
            ("beamformer",{"DEPTH":1},lambda p: beamformer_vectors(p,1,17)[0]),
            ("nlms",{"TAPS":1},lambda p: nlms_vectors(p,1,17,False)[0]),
            ("log",{},log_vectors)]:
        vectors=evidence.path/(leaf+"-smoke.txt")
        count=generator(vectors)
        for simulator in ["iverilog","verilator"]:
            name=leaf+"-"+simulator
            binary=compile_tb(evidence,leaf+"_tb",parameters,simulator,name=name)
            execute_tb(evidence,binary,vectors,name,count,simulator)
            clean_compiler_intermediates(binary)
    for index,parameters in enumerate(MATRIX):
        name=f"inherited-matrix{index}"
        vectors=evidence.path/(name+".txt")
        count,metrics=legacy_vectors(vectors,parameters["TAPS"],parameters["SOS"],
                                     parameters["SRC"])
        binary=compile_tb(evidence,"legacy_tb",parameters,name=name)
        execute_tb(evidence,binary,vectors,name,count)
        clean_compiler_intermediates(binary)
        evidence.record["coverage"][name]=metrics
    for taps in [1,8,16,32,64]:
        name=f"src-taps{taps}"
        vectors=evidence.path/(name+".txt")
        count,metrics=src_vectors(vectors,taps,all_ratios=(taps==1))
        binary=compile_tb(evidence,"src_tb",{"TAPS":taps},name=name)
        execute_tb(evidence,binary,vectors,name,count)
        clean_compiler_intermediates(binary)
        evidence.record["coverage"][name]=metrics
    for leaf,generator in [("spectrum",spectrum_vectors),("mel",mel_vectors)]:
        vectors=evidence.path/(leaf+".txt")
        count=generator(vectors)
        binary=compile_tb(evidence,leaf+"_tb",name=leaf)
        execute_tb(evidence,binary,vectors,leaf,count)
        clean_compiler_intermediates(binary)
    evidence.record["coverage"]["legal_parameter_covering_matrix"]=MATRIX
    evidence.save()
