# BILLET-F1 programmable FIR

Synthesizable SystemVerilog FIR IP with signed Q1.15 samples and coefficients,
full-precision sequential accumulation, ties-to-even rounding, signed output
saturation and atomic double-buffered coefficient commits.

## Sources and integration

The top is `billet_f1_top`, defined in `rtl/billet_f1_top.sv`. Compile all four
`rtl/*.sv` files. `TAP_COUNT=32` is the default; only 1, 8, 16, 32 and 64 are
supported. Address width is derived as `max(1,ceil(log2(TAP_COUNT)))`; samples,
coefficients and outputs are always signed 16 bits.

`design/SPEC.md` defines the complete public port list, numerical formula,
ready equations, cycle table and acceptance contract.
`design/ARCHITECTURE.md` describes module ownership and internal connections.
These documents are requirements and design rationale, not verification claims.

Assert active-low `rst_n` to clear both coefficient banks, history and pending
work. After release, zero-bank filtering is legal immediately. To program a
filter, write each staging address using `cfg_valid && cfg_ready`, then assert
`commit_valid` until `commit_ready` accepts it. Deassert the request after
acceptance. Every subsequent commit requires rewriting every staging address.
Do not hold an incomplete commit request while trying to write: requests block
configuration transfers. At T=1 only address zero is legal.

Drive input samples through `s_valid/s_ready`; consume results through
`m_valid/m_ready`. Producers hold valid and payload stable while stalled.
Tap zero multiplies the newest accepted sample. A commit preserves history,
but reset aborts history, in-flight computation and unconsumed output.
Results become available T cycles after sample acceptance; the earliest
transfer and sustained always-ready initiation interval are T+1 cycles.
These are specified cycle counts, not measurements from an executed test.

## Reproduction interface

Run commands from the delivered project root. Required executable dependencies
are Python 3 (standard library only), Icarus Verilog (`iverilog` with SystemVerilog
2012 support and `vvp`) and Yosys with SystemVerilog support. Actual versions
must be captured by independent execution receipts; no particular installed
version or passing result is claimed here. Missing prerequisites are failures,
not authorization to install tools.

The independent Verifier supplies `tests/verify.py` and all `tests/*` sources
and fixtures. Once those files are present, the admitted commands are:

```sh
python3 tests/verify.py behavior --taps 1,8,16,32,64 --seeds 17,89,203 --out evidence/behavior
python3 tests/verify.py defects --out evidence/defects
python3 tests/verify.py synthesis --taps 1,8,16,32,64 --out evidence/synthesis
python3 tests/verify.py reproduce --out evidence/reproduce
```

The full behavior check uses independent integer and floating convolution,
publishes actual coefficient banks, response/error data and measured cycles,
and exercises reset, streaming stalls and atomic commits. Mutation checks
must execute three defective RTL variants and observe numerical mismatches.
Synthesis checks all supported tap counts for hierarchy, connectivity and
absence of latches/black boxes. `reproduce` is explicitly a fresh-directory
partial subset, not a replacement for the full matrix.

Declared deliverable inputs are `rtl/*.sv`, `design/SPEC.md`,
`design/ARCHITECTURE.md`, `README.md` and verifier-owned `tests/*`, including
fixtures under `tests/data/`. Generated results belong beneath the four
`evidence/` paths above and include a machine-readable claim-to-evidence index,
tool receipts, source hashes and exact relative commands. They must not depend
on prior binaries, preparation evidence, private host paths or runtime metadata.

The shared runtime authors, independently reviews and renders the product PDF
after engineering acceptance. The PDF is not an input to engineering checks.
No physical area, timing closure, silicon-security or FPGA result is claimed.
