# BILLET-F1

Programmable-coefficient FIR with explicit numerical and update rules.

**Status:** Engineering accepted.

## Reproduce

Requires Python 3, Icarus Verilog (`iverilog`, `vvp`) and Yosys.
Run the admitted checks from this generation's directory:

```bash
python3 tests/verify.py behavior --taps 1,8,16,32,64 --seeds 17,89,203 --out evidence/behavior
python3 tests/verify.py defects --out evidence/defects
python3 tests/verify.py synthesis --taps 1,8,16,32,64 --out evidence/synthesis
python3 tests/verify.py reproduce --out evidence/reproduce
```

The test driver creates fresh local evidence. A published status is not a new run on your machine.

Use a clean checkout or fresh output directories when a driver refuses existing evidence.

[Specification](design/SPEC.md) · [Architecture](design/ARCHITECTURE.md) · [RTL](rtl/) · [Verification source](tests/) · [Recorded check statuses](STATUS.json)

[Reviewed product report](REPORT.pdf) · [LaTeX source](REPORT.tex)

Simulation and generic synthesis do not establish physical signoff or measured silicon performance.
