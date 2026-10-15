# BILLET-F2

Multichannel FIR, double-buffered coefficients and parallel MAC lanes.

**Status:** Engineering accepted.

## Reproduce

Requires Python 3, Icarus Verilog (`iverilog`, `vvp`) and Yosys.
Run the admitted checks from this generation's directory:

```bash
python3 tests/verify.py --suite behavior --output evidence/behavior
python3 tests/verify.py --suite compatibility --output evidence/compatibility
python3 tests/verify.py --suite defects --output evidence/defects
python3 tests/verify.py --suite synthesis --output evidence/synthesis
python3 tests/verify.py --suite portability --output evidence/portability
```

The test driver creates fresh local evidence. A published status is not a new run on your machine.

Use a clean checkout or fresh output directories when a driver refuses existing evidence.

[Specification](design/SPEC.md) · [Architecture](design/ARCHITECTURE.md) · [RTL](rtl/) · [Verification source](tests/) · [Recorded check statuses](STATUS.json)

The independently reviewed product report has not been published yet.

Simulation and generic synthesis do not establish physical signoff or measured silicon performance.
