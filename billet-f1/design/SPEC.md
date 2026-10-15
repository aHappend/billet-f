# BILLET-F1 specification

## Scope and frozen parameters

BILLET-F1 is a single-clock streaming programmable-coefficient FIR IP block.
The top is `billet_f1_top`. `TAP_COUNT` defaults to 32; the only supported values
are **1, 8, 16, 32, 64**. Unsupported values, including 0, 2, 3 and 65, produce
an explicit `PARAMETER ERROR` diagnostic before functional traffic. They are
not clamped to a supported configuration.

The derived, non-overridable local parameters are:

* `ADDR_W = max(1, ceil(log2(TAP_COUNT)))`.
* `ACC_W = 32 + ceil(log2(TAP_COUNT))` for supported configurations.

Samples and coefficients are signed 16-bit two's-complement Q1.15 codes.
Products are signed 32-bit integers. The accumulator is exactly 32, 35, 36,
37 or 38 bits for the five supported tap counts respectively. There is no
intermediate truncation, rounding or saturation.

## Cycle & Degenerate-Boundary Table

Handshakes use signal values **immediately before** a rising edge. Registered
observations below refer to values **immediately after** that edge.

| Boundary | Required behavior |
| --- | --- |
| Active reset | Asynchronous active-low `rst_n`; all public outputs, including every ready signal and `m_data`, are zero. All history, coefficients, completeness bits, MAC state and pending output are cleared. |
| First edge after reset release | Zero coefficients and history remain until written/accepted. With no commit request, `s_ready=1`; with a legal configuration address, `cfg_ready=1`. `commit_ready=0`, `m_valid=0`, `m_data=0`. An input may be accepted on this edge. |
| Input edge E0 | `s_valid && s_ready` shifts history exactly once, inserting `s_data` at history index 0. After E0, busy is one; no result for this sample is valid. |
| E1 through E_T | Perform taps 0 through T-1, respectively, using the updated history and the unchanged active bank. |
| Immediately after E_T | Busy is zero and the rounded/saturated result is valid. It cannot transfer at E_T because it was not valid before that edge. |
| E_(T+1), always-ready sink | The output transfers and a new input may be accepted on the same edge. Output availability latency is T cycles, earliest transfer latency is T+1 cycles and sustained initiation interval is T+1 cycles. |
| T=1 | E0 accepts, E1 produces, E2 retires/accepts next. Address width is one bit: address 0 is legal and address 1 is rejected with `cfg_ready=0`. Accumulator width is 32 bits. |
| Other legal T | Every representable configuration address is legal. Non-power-of-two tap counts are unsupported, not a hidden feature. |
| Bubbles | Without an input handshake, history does not shift. |
| Output stall | `m_valid && !m_ready` preserves valid and data bit-for-bit and blocks new inputs. Data is zero whenever valid is zero. |
| Output retirement plus input | Retirement clears the output slot while the next input starts computation. No extra input buffer is present. |
| Completion plus retirement | Newly completed output has priority over retirement. The non-overlapped schedule prevents completion from overwriting an older pending output. |
| Configuration plus computation/output stall | Writes affect only the inactive bank and may proceed in either condition. The active bank remains unchanged. |
| Commit request plus configuration/input | Any asserted `commit_valid` blocks both configuration writes and sample acceptance, even if the commit is incomplete or stalled. |
| Commit plus pending-output retirement | Commit cannot handshake on the retirement edge because an output was pending before that edge. Earliest commit is the next edge. |
| Successful commit | Swap bank roles atomically and clear completeness bits; preserve sample history. A later sample requires a later edge with `commit_valid=0`. |
| Reset plus any operation | Reset wins, aborting computation, pending output and partial programming. No pre-reset output may reappear. |

## Numerical rule

For the nth accepted sample, with absent history interpreted as zero:

```text
A[n] = sum(k=0..T-1, signed(x[n-k]) * signed(c[k]))
q = floor(A[n] / 32768)
r = A[n] - 32768*q                 # 0 <= r < 32768
if r > 16384 or (r == 16384 and q is odd):
    q = q + 1
y[n] = min(32767, max(-32768, q))
```

Thus `c[0]` multiplies the newest sample. Division is round-to-nearest,
ties-to-even for both signs, followed by signed 16-bit saturation. For example,
accumulators -16384 and -49152 round to 0 and -2 respectively; 16384 and 49152
round to 0 and 2. Rounding operates on an extended quotient so incrementing
cannot overflow the accumulator representation.

In real units, `X=x/32768`, `C=c/32768` and the unclipped convolution is
`sum(X*C) = A/2^30`. The output is the nearest Q1.15 code, ties-to-even,
clamped to the representable interval `[-1, 32767/32768]`.

## Public interface

All ports are in the `clk` domain. There are no benchmark or debug ports.

| Port | Direction | Meaning |
| --- | --- | --- |
| `clk` | input | Rising-edge clock |
| `rst_n` | input | Asynchronous active-low reset |
| `s_data` | input signed [15:0] | Input sample |
| `s_valid`, `s_ready` | input, output | Sample ready/valid |
| `m_data` | output signed [15:0] | Filtered sample |
| `m_valid`, `m_ready` | output, input | Output ready/valid |
| `cfg_valid`, `cfg_ready` | input, output | Staging-bank write ready/valid |
| `cfg_addr` | input [ADDR_W-1:0] | Staging coefficient index |
| `cfg_data` | input signed [15:0] | Staging coefficient code |
| `commit_valid`, `commit_ready` | input, output | Atomic bank-swap ready/valid |

A transfer occurs only when valid and ready are both asserted at a rising
edge. Producers retain valid and payload while stalled. Consumers may change
ready independently. Reset aborts outstanding transactions.

The exact ready equations are:

```text
cfg_ready    = rst_n && !commit_valid && (cfg_addr < TAP_COUNT)
commit_ready = rst_n && staging_complete && !busy && !m_valid
s_ready      = rst_n && !busy && (!m_valid || m_ready) && !commit_valid
```

`commit_ready` does not depend on `commit_valid`. Programming an incomplete
bank while holding a commit request is intentionally impossible: withdraw the
request to finish programming. Invalid configuration addresses stall rather
than transferring, including address 1 at T=1.

## Coefficient transactions

Reset clears both banks and selects bank zero as active. Filtering with these
initial zero coefficients is legal before the first commit. Writes go only to
the inactive bank and set an address-written bitmap. Repeated writes are legal:
the latest value wins, and one address cannot substitute for another in the
completeness check.

A successful commit requires every address written since reset or the previous
successful commit, an idle MAC and an empty output slot. It atomically swaps
active/staging ownership and clears the entire written bitmap. The old active
bank becomes staging but must be fully rewritten before another commit.
Commit preserves history. Since swaps cannot occur during computation or
while an output awaits consumption, no output can mix coefficient generations.

Clients deassert `commit_valid` after its handshake unless requesting another
transaction. Each handshake is distinct. A request held through computation
and output backpressure commits only after drainage. Merely asserting a
request does not change banks.

## Verification contract and evidence ownership

The admitted independent verification matrix is all five tap counts with seeds
17, 89 and 203: 15 simulations, each scoring at least 4096 accepted samples,
including at least 1024 random signed/full-range samples, against an independent
integer convolution and a floating convolution. Zero bit mismatches are
required. Check output order/count, reset discards, active X/Z rejection,
stall stability, exact ready equations and pre-edge/post-edge timing.

Each cell must cover positive/negative impulses and DC, alternating extremes,
zero and both full-scale endpoints, mixed signs, long/random bubbles, short
and prolonged output stalls, writes during computation, staging overwrites,
incomplete and busy/stalled commits, complete replacement and history retention,
and reset during idle, computation, output stall and partial programming.
Require at least eight commits, 64 input-bubble cycles and 64 valid-output
stalled cycles, with a contiguous stall of at least 16 cycles. Reset after
acceptance must include T=1 and an interior MAC edge for T>1. Measure at least
64 consecutive always-ready outputs per tap count.

Reachable positive/negative half ties of both quotient parities, adjacent
values and both saturation directions must be checked over the matrix.
Unreachable T=1 cases must not be reported as exercised. Reject unsupported
counts 0, 2, 3 and 65 explicitly.

Verifier publishes fully enumerated banks, generation rules and hashes:
near-unity impulse; moving average (`32768/T` for T>1, `32767` for T=1);
positive triangular with ideal coefficient `(k+1)/(T*(T+1)/2)`; signed
alternating triangular using `(-1)^k`; and full-range stress including
`[32767,32767,0,...]` for T>1 and `[-32768]` for T=1. Quantization is ties-to-even
with signed 16-bit clamping. Additional signed stress vectors may supply
reachable numerical boundaries. Asymmetric banks establish tap ordering.

For moving-average and asymmetric banks at every tap count, score at least
512 steady-state samples at each normalized frequency 1/64, 3/64, 7/64 and
15/64 after warmup. Publish complex response, reproducible traces, maximum
error, MAE, RMSE, clipping counts and measured cycles. Compare floating
convolution to analytical complex response within 1e-10. Against the saturated
floating convolution, absolute normalized error is at most `0.5/32768` plus
documented floating tolerance no larger than 1e-12. Apply the same bound to
unclipped outputs versus unclipped convolution; report clipping error separately.
Publish ideal-versus-quantized response/error and an analytical coefficient
quantization bound, without inventing a filter-quality target.

Three separately simulated disposable RTL mutants must produce observed
oracle mismatches: reversed tap order, truncation instead of ties-to-even,
and active-computation corruption by coefficient writes. Compilation failure
or timeout is not a mutant kill. Preserve each mutant, patch, witness,
first mismatch and raw receipts; prove accepted sources remain unchanged.

Independent Yosys checks cover every legal T, required hierarchy before
flattening, connected state/datapath, no unresolved black boxes and zero
latches including internal latch cell forms. Publish source/module sizes,
generic cell/wire/state statistics and measured cycle behavior, not physical
area, timing closure, silicon-security or FPGA measurements.

Fresh-directory partial reproduction copies only declared design/test sources
and fixtures, runs fresh T=1 and T=32 behavioral subsets, all three defect
witnesses and default-T synthesis, and compares fresh outputs. Preserve source
inventory/hashes, commands and outcomes. This subset is not the full matrix.
Missing tools are explicit failures; do not install them implicitly. Simulation
and synthesis child timeouts are at least 3600 seconds with bounded stimulus
and simulated-cycle nonprogress detection.

Designer owns RTL and these integration documents. Verifier owns `tests/*`,
including `tests/data/`, all functional/numerical/mutation/synthesis checks and
generated evidence. The shared runtime creates and independently reviews the
product PDF only after engineering acceptance; no early check requires it.
This specification records requirements, not executed results.
