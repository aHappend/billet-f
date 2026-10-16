# Independent BILLET-F3 verification

Run from the delivered project root with Python 3 (standard library only),
Icarus `iverilog`/`vvp`, and Yosys already available on PATH. Missing prerequisites
fail explicitly; the checker does not install anything.

```
python3 tests/verify.py
python3 tests/verify.py --check behavior
python3 tests/verify.py --check numerical
python3 tests/verify.py --check defects
python3 tests/verify.py --check synthesis
python3 tests/verify.py --check portable
```

The first command runs all five mandatory checks. The portable check is a
bounded real clean-directory reproduction, not a substitute for the full
matrix. It copies only `rtl/*.sv`, `design/*.md`, `tests/*` files and
`billet_f2_reference/rtl/*.sv`, then runs the unchanged checker in that new
project root. No generated evidence or runtime metadata is copied as input.

## Requirement-to-check map

| Check | Experiments and pass conditions |
| --- | --- |
| behavior | All 60 legal tuples, every mode and active prefix, at least 64 accepted samples per channel; six specified extended tuples at seeds 11, 29, 97 with at least 1024 per channel/mode. Every result and every pre/post-edge public output must match the independent integer/transaction reference. |
| behavior: compatibility | All 15 FIR parameter pairs, four FIR fixture classes, original parent instance plus independent numerical oracle. All legacy public outputs are compared cycle by cycle with new valid inputs tied low. Parent RTL copies must be byte-identical. |
| behavior: boundaries | Directed invalid/incomplete/duplicate programming, all descriptor encodings, update priorities, both bank toggles, concurrent writes, distinct per-channel coefficients, state clear/history retention, busy/held reset aborts, 32-cycle stalls in each mode and simultaneous retirement/acceptance. Required observed-event coverage bins must be nonzero. Partial SOS banks remain incomplete even with only one active section. |
| behavior: arithmetic | Signed half ties with both quotient parities, cancellation, exact external extrema, positive/negative FIR/state/output saturation. Separate rounding-unit vectors cover exact extrema and extreme accumulators with integer expectations. Illegal top parameters must elaborate safely and then terminate with `PARAMETER ERROR`. |
| numerical | Three frozen stable fixtures, homogeneous lengths 1 through 4 and both mixed LP50/HP50 sequences. For each sequence, all four channels undergo excitation, 32768 zero inputs and 32768 bounded-random inputs, with every output bit-exact. Final 4096 decay outputs must have absolute code at most 1; reference state residuals are reported separately. |
| numerical: errors | Each sequence in SOS-only and both mixed orders receives nonclipping impulse, signed step/DC, low/mid/near-Nyquist tones and bounded random stimuli at absolute input at most 0.25. Independent transposed floating recurrence errors must be at most 0.0004 maximum and 0.0001 RMS, including per-stimulus statistics. |
| numerical: response | Measured RTL impulses of 1024 samples/channel, amplitude 16384, are transformed at 257 DC-to-Nyquist frequencies and compared with analytic complex transfer functions. Maximum complex error must be at most 0.01. Reports include actual gains, meaningful phase, maximum/RMS errors and accompanying time-domain comparison. The mixed FIR fixture has coefficient L1 norm 1. |
| defects | Four separately compiled copied-RTL mutations: feedback sign, old-state update, active bank and mixed order. Identical input/expected files first pass pristine RTL. Each mutant must execute successfully and then receive a structured first public-pin mismatch and nonzero scoreboard exit. Compilation errors, X/Z, timeouts and missing reports are not detections. Saturating FIR stimulus distinguishes the chain orders. |
| synthesis | Five admitted configurations undergo real Yosys hierarchy checks, process lowering, optimization, driver checking and generic synthesis. Machine assertions reject latches, black boxes, unresolved modules, missing child wiring or lost FIR/SOS coefficient/history/state storage. |
| portable | Fresh-source bounded behavior, parent compatibility, all modes/channels, stalls/updates, RTL-versus-floating response, all four defects and default synthesis. The enclosing result requires the child check to pass and retain identical source hashes. Source audit rejects private paths and credential signatures. |

## Independence and sampling

`model.py` uses arbitrary-precision signed integers and specification-defined
floor quotient/remainder rounding. Coefficients, histories and recursion evolve
only from accepted public requests. It neither reads DUT internals nor copies
the scheduler's implementation. The floating oracle is transposed direct form
II, independently formulated from the integer direct form I recurrence.
Analytic response and poles are computed directly from quantized coefficients.
No arbitrary user-coefficient stability guarantee is inferred.

`bridge.sv` drives vector fields while the clock is low, observes settled
pre-edge pins, then observes post-edge pins after nonblocking updates. Reset
starts asserted at time zero and is checked there, in the first rising-edge
Active region, after that edge and on falling edges. Later assertions between
edges must immediately suppress all ready, valid, data and channel outputs.
Unknown or high-impedance bits in the public trace fail comparison, including
when an output is invalid. Held valid outputs must remain unchanged.

The ordinary scoreboard compares the complete public-output word at both
observations on every cycle. It independently derives accepted/retired/aborted
counts, per-channel mode counts, first-result visibility latency and minimum
acceptance intervals from the actual trace and driven requests. These measured
values must agree with the contract-derived ledger; stimulus labels alone
cannot satisfy coverage. Latency is edge distance, not an inclusive cycle count.
The integer model's arithmetic coverage is credited only for runs whose entire
trace passed comparison.

## Retention and scope

Each check creates a fresh `evidence/<check>/run-.../` directory containing a
source snapshot/hashes, tool versions, exact relative argv/cwd, raw child
stdout/stderr, start/end times, exit/timeout receipts, stimulus, expected and
actual traces, measured outputs and structured assertions. The check's
`evidence/<check>/results.json` indexes its latest result without replacing any
run directory. Failures remain retained. Mutation trees and hashes are private
to that run and never modify authoritative RTL. Child wall-clock guards are
86400 seconds, not acceptance thresholds.

Results report actual comparison/transaction counts, latency/intervals, source
line/byte/module inventories, numerical errors and generic synthesis statistics.
Expected stimulus counts on rejected mutants are explicitly separated from
observed comparisons. Reference residual state is not an internal RTL
measurement. Generic cell counts are not physical area or timing results.

Diagnostic-only preparation commands are:

```
python3 tests/verify.py --probe smoke
python3 tests/verify.py --probe synthesis
python3 tests/verify.py --probe numerical
```

These retain unique `evidence/preparation/verifier-smoke-.../receipt.json`
records and never publish mandatory-check results. Preparation is advisory,
not engineering acceptance. Interrupted role execution, passing probes and
repository intake do not certify the full changed product. Independent review
and admitted shared-runtime execution are still required. The product PDF is
authored by the shared runtime only after engineering acceptance; none of these
checks requires that later report.
