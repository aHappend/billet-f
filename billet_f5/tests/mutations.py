"""Executable numerical sensitivity in disposable copies, never production RTL."""

import hashlib

from scenarios import beamformer_vectors, nlms_vectors, vad_vectors, log_vectors
from simulation import (compile_tb, execute_tb, disposable_sources,
                        clean_compiler_intermediates)


MUTANTS = [
    ("fraction-complement", "billet_f5_beamformer.sv",
     "10'sd256-$signed", "10'sd255-$signed", "beamformer", {"DEPTH": 8}),
    ("microphone-seven-omitted", "billet_f5_beamformer.sv",
     "s_mics[16*mic+:16]", "((mic==7)?16'sd0:s_mics[16*mic+:16])",
     "beamformer", {"DEPTH": 8}),
    ("nlms-gradient-sign", "billet_f5_nlms.sv",
     "gradient_q<=error_q*vector_q[tap];",
     "gradient_q<=-(error_q*vector_q[tap]);", "nlms", {"TAPS": 1}),
    ("double-talk-removed", "billet_f5_nlms.sv",
     "wire freeze_decision=(far_power<32'd65536) ||\n"
     "            ({2'b00,desired_power}>(({2'b00,far_power}<<1)+34'd65536));",
     "wire freeze_decision=(far_power<32'd65536);", "nlms", {"TAPS": 1}),
    ("weight-leakage-removed", "billet_f5_nlms.sv",
     "$signed({weights[tap][23],weights[tap]})-$signed({leakage[23],leakage});",
     "$signed({weights[tap][23],weights[tap]});", "nlms", {"TAPS": 1}),
    ("vad-on-threshold", "billet_f5_vad.sv",
     "energy_q>=48'd65536", "energy_q>=48'd65537", "vad", {}),
    ("token-db-multiplier", "billet_f4_log.sv",
     "19'sd197283", "19'sd197000", "log", {}),
]


def run_mutations(evidence, selected=None):
    evidence.versions([("verilator", "--version")])
    mutants=[row for row in MUTANTS if selected is None or row[0]==selected]
    if not mutants:
        raise ValueError("unknown sensitivity diagnostic")
    numerical_vectors = {}
    for leaf in dict.fromkeys(row[4] for row in mutants):
        path = evidence.path / (leaf+".txt")
        if leaf=="beamformer":
            count, _ = beamformer_vectors(path, 8, 17)
            parameters={"DEPTH": 8}
        elif leaf=="nlms":
            count, _ = nlms_vectors(path, 1, 17, full=False)
            parameters={"TAPS": 1}
        elif leaf=="vad":
            count=vad_vectors(path); parameters={}
        else:
            count=log_vectors(path); parameters={}
        binary=compile_tb(evidence, leaf+"_tb", parameters, name="control-"+leaf)
        execute_tb(evidence, binary, path, "control-"+leaf, count)
        clean_compiler_intermediates(binary)
        numerical_vectors[leaf]=(path,count)
    outcomes=[]
    for name, filename, old, new, leaf, parameters in mutants:
        source=disposable_sources(evidence, "mutant-"+name)
        target=source/"rtl"/filename
        original=target.read_text()
        replacements=[(old,new)]
        if name=="microphone-seven-omitted":
            replacements.append((
                "s_mics[16*history_mic+:16]",
                "((history_mic==7)?16'sd0:s_mics[16*history_mic+:16])"))
        for before, after in replacements:
            occurrences=original.count(before)
            if occurrences!=1:
                raise AssertionError(
                    f"{name}: mutation site {before!r} count {occurrences} != 1")
        modified=original
        for before, after in replacements:
            modified=modified.replace(before,after)
        target.write_text(modified)
        evidence.artifact(target)
        binary=compile_tb(evidence,leaf+"_tb",parameters,source_root=source,name=name)
        vectors,count=numerical_vectors[leaf]
        text=execute_tb(evidence,binary,vectors,name,count,rejection=True)
        clean_compiler_intermediates(binary)
        outcomes.append({"defect": name, "compiled": True,
                         "numerical_rejection": True,
                         "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
                         "mutant_sha256": hashlib.sha256(modified.encode()).hexdigest(),
                         "diagnostic": next(line for line in text.splitlines()
                                            if "mismatch" in line.lower())})
        evidence.record["metrics"]["mutants"]=outcomes
        evidence.save()
    if selected is None and len(outcomes)<7:
        raise AssertionError("required defect-sensitivity cases missing")
    evidence.record["coverage"]["executed_numerical_mutants"]=len(outcomes)
