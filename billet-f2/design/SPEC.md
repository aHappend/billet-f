# BILLET-F2 frozen specification

## Scope and provenance

BILLET-F2 is a four-channel, signed fixed-point FIR with one shared computation
engine, independent channel histories and independent double-buffered
coefficients. The top is `billet_f2_top`. The supplied four files in
`billet_f1_reference/rtl/` are read-only compatibility inputs. Intake supplied no
linked source index, parent specification or admitted baseline results. Inspection
of that RTL establishes the intended F1 behavior, not a passing baseline.

The admitted work order governs acceptance. This document specifies the
implementation contract; it does not claim completed verification. Physical
area, timing closure, silicon security and FPGA measurements are outside scope.

## Parameters and interface

| Parameter | Default | Legal values |
| --- | --- | --- |
| `TAP_COUNT` | 32 | 1, 8, 16, 32, 64 |
| `MAC_LANES` | 1 | 1, 2, 4 |

Every cross-product combination is supported, including one tap with multiple
lanes. There are exactly four channels. Derived local parameters are
`ADDR_W = max(1, ceil(log2(TAP_COUNT)))` and
`ACC_W = 32 + ceil(log2(TAP_COUNT))`, with `ceil(log2(1)) = 0`.
Unsupported parameters elaborate only an isolated diagnostic branch and then
terminate simulation nonzero with `PARAMETER ERROR`. They have no legal datapath.

| Port | Direction | Width and meaning |
| --- | --- | --- |
| `clk` | input | Rising-edge clock |
| `rst_n` | input | Active-low asynchronous reset |
| `s_data` | input | Signed 16-bit Q1.15 sample |
| `s_channel` | input | 2-bit channel |
| `s_valid`, `s_ready` | input, output | Sample handshake |
| `m_data` | output | Signed 16-bit Q1.15 result |
| `m_channel` | output | 2-bit accepted sample channel |
| `m_valid`, `m_ready` | output, input | Result handshake |
| `cfg_valid`, `cfg_ready` | input, output | Coefficient-write handshake |
| `cfg_channel` | input | 2-bit channel |
| `cfg_addr` | input | `ADDR_W`-bit tap address |
| `cfg_data` | input | Signed 16-bit Q1.15 coefficient |
| `commit_valid`, `commit_ready` | input, output | Bank-swap handshake |
| `commit_channel` | input | 2-bit channel |

All four channel encodings are legal. Transactions occur only on rising edges
where pre-edge valid and ready are both high. Producers hold valid and payload
while stalled. A stalled output holds valid, data and channel until retirement
or reset. Each accepted sample yields one ordered output unless reset cancels
it. There is no input queue, no concurrent computation, and one output register.

## Numerical contract

For channel `c`, the exact accumulator for its accepted sample `n` is

```text
A[c,n] = sum(k=0..TAP_COUNT-1, signed16(h[c,k]) * signed16(x[c,n-k]))
```

Unavailable history is zero. Tap zero multiplies the newest accepted sample;
activity on other channels never advances this history. Products are exact
signed 32-bit values, sign-extended to `ACC_W` before any reduction or
accumulation. There is no intermediate truncation, rounding or saturation.
Unused tail lanes contribute zero.

Divide `A` by 32768, round to nearest integer with ties to even for either sign,
then saturate to `[-32768, 32767]`. Equivalently, let
`q = floor(A / 32768)` and `r = A - 32768*q`, with `0 <= r < 32768`.
Increment `q` if `r > 16384`, or if `r == 16384` and `q` is odd, then clamp.
The independent Python oracle must use arbitrary-precision integer
quotient/remainder arithmetic, not translated RTL intermediates. Required error
is zero output-code differences, independent of lane count.

## Ownership and control

Each channel has two coefficient banks, an active selector and one
`TAP_COUNT`-bit staging-written mask. Writes target only that channel's inactive
bank. Duplicate addresses overwrite data but do not count as different taps.
Every tap must receive a fresh accepted write after reset or the last successful
commit. Completeness changes after a write edge; the final missing write cannot
enable a commit on the same edge.

A commit atomically flips only its channel's selector, clears only its mask and
preserves all histories. Old active contents become staging storage, but do not
count as fresh writes. There is no pending-commit queue.

Bank ownership is globally frozen during computation and through output
retirement. Even another channel cannot commit during this interval, including
the edge retiring an output. The sample's channel and bank are captured at its
acceptance edge. Inactive-bank writes can continue during computation and output
stalls. A commit request, ready or not, suppresses all sample and configuration
acceptance. Exact readiness is:

```text
s_ready      = rst_n && !busy && (!output_valid || m_ready) && !commit_valid
cfg_ready    = rst_n && !commit_valid && (cfg_addr < TAP_COUNT)
commit_ready = rst_n && staging_complete[commit_channel] && !busy && !output_valid
```

`commit_ready` does not depend on `commit_valid`. The address comparison is
effective even for one tap: address one is rejected. With `commit_valid=0`,
sample and configuration handshakes may coincide; the sample uses active
coefficients, not staging writes.

## Cycle & Degenerate-Boundary Table

Let `G = ceil(TAP_COUNT / MAC_LANES)`. All observations explicitly distinguish
pre-edge handshakes from post-edge registered state.

| Event or boundary | Pre-edge condition | Post-edge effect |
| --- | --- | --- |
| Reset assertion between edges | `rst_n` falls | Asynchronously clear histories, both banks, masks, selectors, control and output state; all public outputs zero |
| Reset held at a clock edge | `rst_n=0` | All state remains cleared; readiness, valid, data and channel zero |
| First edge after reset release | Empty state, zero banks | Legal handshakes work normally; a sample starts a zero-coefficient computation |
| Sample edge E0 | `s_valid && s_ready` | Shift only selected history, insert sample, capture channel/bank, clear accumulator, set busy; no new valid output |
| Ej, `1 <= j < G` | Busy, nonfinal group | Accumulate one full-width lane group; remain busy and output invalid |
| EG | Busy, final group | Capture rounded final sum and channel in output; clear busy; assert output valid |
| EG+1, no backpressure | Old output valid and `m_ready=1` | Retire output; optional replacement input starts with the same E0 semantics |
| Output stall | Valid and `m_ready=0` | Hold output and ownership; no sample or commit acceptance; staging writes allowed absent commit request |
| Commit at C | Complete channel, no busy or valid output | Flip addressed bank, clear that mask; no sample/configuration accepted |
| First new-bank sample | Commit request deasserted after C | Earliest acceptance C+1, using entirely the new bank |
| Final missing write | Pre-edge incomplete mask | Completeness becomes true after edge, not before |
| Output retire plus commit request | Pre-edge output valid | Retire output but reject commit; earliest commit next edge, EG+2 |
| Sample plus configuration | No commit request, both ready | Shift selected history and write inactive bank independently |
| Incomplete commit held | Commit requested but incomplete | No sample or write acceptance; reset can cancel request-related state |
| `TAP_COUNT=1`, any legal lanes | One active lane, zero tails | E1 produces output, earliest retirement/replacement E2; address one rejected |
| `TAP_COUNT=8`, lanes 1/2/4 | 8/4/2 groups | Exact latency 8/4/2 cycles and unstalled initiation interval 9/5/3 cycles |
| Other legal tuples | Power-of-two tap count, grouped stepping | No modulo wrap assumption for completion; tail lanes explicitly gated |
| Unsupported parameter | Diagnostic-only generate branch | No malformed datapath dimensions; nonzero `PARAMETER ERROR` simulation exit |

Acceptance-to-valid latency is exactly `G` cycles independent of `m_ready`.
Earliest retirement is `G+1` cycles after acceptance. Unstalled initiation
interval is `G+1`, not `G`; no acceptance occurs on computing edges E1 through
EG because their pre-edge busy is true. Backpressure can extend retirement and
initiation intervals without changing the result or ownership.

When `m_valid=0`, `m_data` and `m_channel` are zero. Reset cancels all accepted
but unretired work, staged updates and any blocked commit state without ghost
results. The external producer must withdraw a canceled request as appropriate;
the DUT does not queue requests. Reset tests must assert a genuine transition,
not conceal unknown observations.

## Independent acceptance obligations

The Verifier owns `tests/*.py`, including `tests/verify.py`, all stimulus,
independent models and generated checks. The Designer owns only `rtl/*.sv` and
these design documents. The mandatory suites and commands are recorded in
`ARCHITECTURE.md`. Real Icarus behavior and F1 lockstep, explicit X/Z rejection,
independent numerical comparison, executed semantic defects and real Yosys
synthesis/latch checks remain required. A static compiler pass is not acceptance.

Behavior requires all 15 legal tuples at seeds 17, 89, 233 (45 runs), at least
256 compared retirements per channel per run, four accepted distinct-generation
commits per channel, duplicate writes, incomplete commit rejection, at least
16 stalled-output cycles per channel, computation/stalled-output/blocked-commit
reset cancellation, and all-channel refill after the final reset. Numerical
and protocol directed cases apply where reachable; single-tap cases do not
replace multi-tap saturation and lane-cancellation coverage. Coverage must count
observed transitions or independently checked numerical cases, not labels.

The F1 suite requires every legal tap count at all three seeds, `MAC_LANES=1`,
channel zero only, and at least 256 compared retirements per run. Compare
pre-edge readiness, post-edge output, and accepted values to both unchanged F1
and the independent oracle, including stalls, priorities and cancellation.

Measure actual acceptance-to-valid and retirement latencies, unstalled and
stalled acceptance intervals, and old/new bank-generation boundaries. Assert
`G` and `G+1` in an unstalled phase. Kill three separate semantic mutants:
cross-channel history, in-flight/stalled bank ownership, and a nonzero
four-lane reduction path for at least eight taps. Require a passing baseline
and explicit behavioral/protocol mismatches, not tool failures.

Synthesize all 15 legal tuples with Yosys `synth -noabc`, retain pre-flatten
hierarchy and final statistics/netlists, run `check -assert`, reject latch cells,
and check substantive connected storage and parallel multipliers. Exercise
illegal taps 0/3/65 and illegal lanes 0/3/8 with the other parameter legal.
Report actual counts, source/module sizes and zero-code error, not physical PPA.
Fresh-tree portability must run behavior 8/4/17, F1 compatibility 1/17 and
synthesis 8/4 using only declared sources, documentation, tests and parent RTL.

Retain commands, return codes, raw logs, versions, source identities, seeds,
failures and repairs. Missing prerequisites, partial results, unknowns, assertions
or coverage deficits fail acceptance; do not install tools or weaken thresholds.
Any wall-clock child timeout must be at least 3600 seconds. The shared runtime
and independent Reviewer own acceptance. The approved product PDF follows
engineering acceptance and is not a prerequisite to design verification.
