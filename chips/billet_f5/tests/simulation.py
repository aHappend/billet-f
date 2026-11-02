"""Compile fresh simulators; compare online in testbenches with bounded output."""

from pathlib import Path
import re
import shutil


def compile_tb(evidence, top, parameters=None, simulator="verilator", source_root=None,
               name=None):
    source_root = source_root or evidence.root
    name = name or top
    work = evidence.path / name
    work.mkdir(exist_ok=False)
    rtl = [p.relative_to(evidence.root).as_posix()
           for p in sorted((source_root / "rtl").glob("*.sv"))]
    tb = f"tests/{top}.sv"
    if simulator == "iverilog":
        binary = work / "simulation.vvp"
        argv = ["iverilog", "-g2012", "-Wall", "-s", top, "-o",
                evidence.relative(binary)]
        for key, value in (parameters or {}).items():
            argv += ["-P", f"{top}.{key}={value}"]
        argv += [tb, *rtl]
    else:
        binary = work / ("V" + top)
        argv = ["verilator", "--binary", "--timing", "--assert",
                "-Wno-fatal", "--top-module", top, "--Mdir",
                evidence.relative(work), "-j", "2"]
        argv += [f"-G{k}={v}" for k, v in (parameters or {}).items()]
        argv += [tb, *rtl]
    evidence.run(argv, name + "-compile")
    if not binary.is_file():
        raise AssertionError(f"{name}: compiler did not create executable")
    return binary


def execute_tb(evidence, binary, vectors, name, count, simulator="verilator",
               rejection=False):
    evidence.artifact(vectors)
    argv = ([evidence.relative(binary)] if simulator == "verilator" else
            ["vvp", evidence.relative(binary)])
    argv += ["+vectors=" + evidence.relative(vectors)]
    code, stdout, stderr = evidence.run(
        argv, name + "-simulate", expected_exit=None if rejection else 0)
    text = stdout.read_text(errors="replace") + stderr.read_text(errors="replace")
    if rejection:
        if code == 0 or "mismatch" not in text.lower():
            raise AssertionError(f"{name}: mutant not rejected by numerical mismatch")
        return text
    match = re.search(r"SUMMARY \w+ comparisons=(\d+)", text)
    if not match or int(match.group(1)) != count:
        raise AssertionError(f"{name}: missing or incorrect comparison summary")
    evidence.compared(name, count, simulator=simulator,
                      stdout=evidence.relative(stdout))
    return text


def clean_compiler_intermediates(binary):
    work = binary.parent
    for p in work.iterdir():
        if p.is_file() and p.suffix in {".o", ".a", ".gch"}:
            p.unlink()
    for p in work.glob("*.d"):
        p.unlink()


def disposable_sources(evidence, name):
    root = evidence.path / name
    root.mkdir(exist_ok=False)
    (root / "rtl").mkdir()
    for path in (evidence.root / "rtl").glob("*.sv"):
        shutil.copy2(path, root / "rtl" / path.name)
    return root
