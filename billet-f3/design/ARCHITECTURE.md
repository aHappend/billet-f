# BILLET-F3 architecture and integration

## Structure and ownership

`design/SPEC.md` freezes interfaces, exact numerical rules, legal configuration,
cycle boundaries and unchanged acceptance requirements. This document describes
the implementation, not a verification verdict.

```
billet_f3_top
  g_supported.u_fir : billet_f2_top (unaltered parent)
    g_supported.u_coeff_bank : billet_f2_coeff_bank
    g_supported.u_history    : billet_f2_history
    g_supported.u_mac        : billet_f2_mac
    g_supported.u_round_sat  : billet_f2_round_sat
  g_supported.u_sos : billet_f3_sos_engine
    g_supported.u_coeff_bank   : billet_f3_sos_coeff_bank
    g_supported.u_state_round  : billet_f3_round_sat (53 -> 32, shift 16)
    g_supported.u_output_round : billet_f3_round_sat (32 -> 16, shift 8)
```

The literal `u_fir` and `u_sos` instances reside in the legal-parameter generate
branch. All five `rtl/billet_f2_*.sv` files are byte-for-byte copies of the
read-only `billet_f2_reference/rtl/` files. Do not modify or co-compile duplicate
module definitions from both trees in one library. A parent-comparison test
must arrange separate compilation or a private test-only renamed copy.

New modules are `billet_f3_top`, `billet_f3_sos_engine`,
`billet_f3_sos_coeff_bank`, and `billet_f3_round_sat`; there are nine RTL module
definitions total. Designer owns RTL and design documentation; independent
Verifier owns all test sources, numerical models, fixtures and evidence.

## Reservation, routing and timing

The wrapper has one transaction-state register, not an additional result
pipeline. States distinguish idle, registered bypass, FIR-only, SOS-only,
FIR-first/SOS-last and SOS-first/FIR-last. The descriptor cannot change while
any transaction or output is pending, so it need not be copied per sample.
Data/channel live in the selected child or in the bypass register. Output
selection exposes the final child's existing registered result directly.

At the initial edge, a top-level input acceptance starts the first child on
that same edge. The first child computes independently of top-level downstream
readiness. In a mixed chain its registered output is accepted by the second
child at the following edge. This is a real registered block boundary, not
combinational chaining. The sole reservation guarantees the second child is
empty and ready, so first-stage output readiness is unconditional in a
first-stage state. This avoids cross-coupled combinational ready paths. The
first child is retired as the second starts.
Only final-output retirement frees the top-level reservation.

`slot_available = idle || (m_valid && m_ready)` allows same-edge final retirement
and next-input acceptance. The selected first child's readiness also qualifies
top readiness. Each child supports retirement and restart on the same edge;
the unaltered FIR path therefore retains the parent's default cycle behavior.
No update is accepted on a final-retirement edge: updates require the already
empty pre-edge state. Any asserted update valid blocks new samples but does
not interrupt an accepted sample or prevent its retirement.

The cycle/degenerate table in SPEC is normative. In particular, with
G=ceil(TAP_COUNT/MAC_LANES) and C active SOS sections:

| Mode | Edge-distance result latency | Minimum input edge interval |
| --- | --- | --- |
| bypass | 0 | 1 |
| FIR | G | G+1 |
| SOS | C | C+1 |
| either mixed order | G+C+1 | G+C+2 |

At defaults G=32. In FIR-only reset mode the analytical latency is 32 edges
and input interval 33. With both default sections enabled, SOS is 2/3 and a
mixed chain is 35/36. These are architectural schedules awaiting independent
cycle measurements, not measured throughput claims.

## Shared SOS datapath

One section is processed per busy cycle. Five signed 18x32 multipliers feed
explicit 50-to-53-bit sign extensions and the exact sum
`b0*x+b1*x1+b2*x2-a1*y1-a2*y2`. The state rounding unit performs signed
floor-quotient ties-to-even rounding and saturation. Its 32-bit result is
simultaneously stored in y1 and passed to the next section. Nonblocking
assignments preserve old x1/y1 in x2/y2. Only the last section writes the
external output register, using the second rounding unit. There is no hidden
per-section conversion to 16 bits and no output-completion bubble.

Channel, active bank, section index, active count and current input are captured
or retained in the engine. The coefficient bank read port selects the current
channel/bank/section's five coefficients combinationally. The engine captures
the active bank at sample acceptance; quiescent updates additionally guarantee
it cannot change during execution. Staging writes can overlap filtering
without affecting this read bank.

Four arrays x1,x2,y1,y2 each have `4*SOS_COUNT` entries of 32 bits. The state
index is `channel*SOS_COUNT+section`. The physical count is parameterized, while
the programmed count selects only an ascending active prefix. Inactive states
are untouched by samples, but cleared along with all physical sections on the
relevant update. The maximum index is 15. A two-bit section index covers
0..3; a separate three-bit count represents all four sections without wrap.

Numerical ranges and the 53-bit accumulation proof are in SPEC. The reusable
rounding module is instantiated with `(IN_W,SHIFT,OUT_W)` equal to `(53,16,32)`
and `(32,8,16)`. Its supported integration domain is `OUT_W>=2`,
`IN_W>=OUT_W`, and `1<=SHIFT<IN_W`; top-level legal parameters always meet it.
The parent arithmetic and rounding module are deliberately not replaced.

## Storage and update ownership

These are logical storage budgets, not synthesized cell counts or physical
area measurements:

| Storage | Logical data bits |
| --- | --- |
| FIR coefficients, two banks / four channels | 128*TAP_COUNT |
| FIR history, four channels | 64*TAP_COUNT |
| SOS coefficients, two banks / four channels / five per section | 720*SOS_COUNT |
| SOS recursive history, four values / four channels | 512*SOS_COUNT |
| FIR / SOS written masks | 4*TAP_COUNT / 20*SOS_COUNT |
| FIR / SOS active bank selectors | 4 / 4 |

At defaults these arrays/masks/selectors total 8784 bits, excluding datapath,
transaction, descriptor, output and control registers. This follows directly
from 4096+2048+1440+1024+128+40+8, not from a synthesis report.
Asynchronous clearing makes these register-like stores; no SRAM macro is
promised. Generic optimization may change storage realization without changing
the public contract.

The top-level arbiter enforces quiescence and asserted-valid priority:

1. Chain request, legal descriptor only: installs global mode/count and clears
   all SOS history; retains every FIR history/coefficient and SOS coefficient.
2. SOS commit, complete physical bank only: swaps that channel's coefficient
   bank, clears its mask and all its physical SOS histories.
3. FIR commit, complete TAP_COUNT bank only: swaps that channel's FIR bank,
   clears its mask and retains its FIR history.

Both staging interfaces may transfer on one edge, including during processing,
unless any update valid is asserted. Each bank's own logic gives commit priority
over write, although top gating prevents that collision. Incomplete or invalid
updates never handshake and intentionally stop new samples; higher invalid
requests still block lower updates. See SPEC for the producer liveness rule.

| Consumer assumption | Guarantee |
| --- | --- |
| FIR sees stable active coefficients through MAC | Only inactive writes; top admits commits only while empty |
| SOS executes against one bank/count | Bank/count captured at start; descriptor/commits globally quiescent |
| Chain state advances once per accepted sample | One top reservation; busy-only section updates; no updates during held output |
| Second stage can own first result | Direct ready/valid transfer; first output retained until accepted |
| Final result survives backpressure | Child output register (or bypass register) changes only on production/retirement |
| Channel histories stay isolated | Channel captured, independent per-channel storage and bank selection |

Reset is asynchronous active-low throughout. All public outputs are explicitly
gated by reset, including combinational readiness, data and channel. The reset
edge clears selectors, masks, both coefficient banks, all histories, descriptor,
transaction state and output reservations. No output is valid after release
until a post-reset sample has been accepted and processed. Integrators must
assert reset, release it under their clock/reset-domain discipline, fully write
and commit desired coefficients, optionally handshake a legal chain descriptor,
then stream data while respecting readiness.

## Design alternatives and limits

Direct form I was frozen to expose feedback signs and simultaneous old-state
updates. A shared sequential engine avoids speculative pipelines and section
replication. The 32-bit recursive state retains eight more fractional bits than
the external interface and quantizes only once between FIR and SOS. The five
multipliers are parallel within one section; this is not a five-cycle MAC.

There is no arbitrary descriptor list, repeated FIR instance, per-channel mode,
clock-domain crossing, software register bus or extra outstanding queue.
Unstable user coefficients can saturate or exhibit limit cycles; stability
claims are limited to the independently evaluated declared fixtures and stimuli.
No physical PPA, timing closure, FPGA measurements, silicon-security claim or
formal proof is made.

## Sources, commands and evidence boundary

Compile deliverable RTL from the project root with the top selected explicitly:

```
iverilog -g2012 -s billet_f3_top -t null rtl/*.sv
```

This is a static syntax/elaboration command only, not a functional test.
The independent commands, prerequisites, bounded portable check and result paths
are specified in SPEC. `python3 tests/verify.py` is the full acceptance entry;
`python3 tests/verify.py --check portable` is bounded reproduction. Tests and
execution results are pending at design handoff. Independent execution must
record actual tool versions, counts, latency/throughput, numerical response,
source sizes, generic netlist statistics and retained failures.

Only `rtl/*.sv`, `design/*.md`, `tests/*` and the read-only parent RTL are
reproduction sources. Generated `evidence/` is not an input; runtime metadata,
account directories and transcripts must not enter a deliverable source copy.
The product PDF is authored/reviewed/rendered by the shared runtime after
engineering acceptance and is never a prerequisite for checking RTL.
