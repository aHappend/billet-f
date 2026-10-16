# BILLET-F3 frozen design contract

This is the implementation contract admitted before RTL construction. It does
not certify behavioral, numerical or synthesis acceptance. Top: `billet_f3_top`.
The independent Verifier owns `tests/*`; the shared runtime produces the
independently approved product PDF only after engineering acceptance.

## Parameters and interface

Exactly four channels are supported. `TAP_COUNT` is one of 1, 8, 16, 32, 64
(default 32); `MAC_LANES` is 1, 2, 4 (default 1); `SOS_COUNT` is 1, 2, 3, 4
(default 2). Every combination is legal, including excess FIR lanes at one tap.
`ADDR_W=max(1,ceil(log2(TAP_COUNT)))`. Invalid parameters use diagnostic-safe
ports and a generate guard, and terminate simulation with `PARAMETER ERROR`.
Non-power-of-two tap counts are not supported.

All interfaces use the same `clk` and asynchronous active-low `rst_n`. A transfer
occurs only when valid and ready are both high immediately before a rising edge.

| Ports | Direction at top | Width / representation |
| --- | --- | --- |
| `s_data`, `s_channel`, `s_valid` | input | signed 16, unsigned 2, Boolean |
| `s_ready` | output | Boolean |
| `m_data`, `m_channel`, `m_valid` | output | signed 16, unsigned 2, Boolean |
| `m_ready` | input | Boolean |
| `cfg_valid`, `cfg_channel`, `cfg_addr`, `cfg_data` | input | Boolean, 2, ADDR_W, signed 16 |
| `cfg_ready` | output | Boolean |
| `commit_valid`, `commit_channel` | input | Boolean, 2 |
| `commit_ready` | output | Boolean |
| `sos_cfg_valid`, `sos_cfg_channel`, `sos_cfg_section`, `sos_cfg_addr`, `sos_cfg_data` | input | Boolean, 2, 2, 3, signed 18 |
| `sos_cfg_ready` | output | Boolean |
| `sos_commit_valid`, `sos_commit_channel` | input | Boolean, 2 |
| `sos_commit_ready` | output | Boolean |
| `chain_valid`, `chain_mode`, `chain_sections` | input | Boolean, 3, 3 |
| `chain_ready` | output | Boolean |

No new control may float: legacy integration ties the three new valid inputs
low and their request fields to zero. Parent stream/configuration/commit ports
are preserved with their original signedness and widths.

| chain_mode | Operation | Legal chain_sections |
| --- | --- | --- |
| 0 | registered bypass | 0 |
| 1 | FIR | 0 |
| 2 | SOS prefix | 1 through SOS_COUNT |
| 3 | FIR then SOS prefix | 1 through SOS_COUNT |
| 4 | SOS prefix then FIR | 1 through SOS_COUNT |
| 5 through 7 | invalid, never accepted | none |

The global descriptor applies to every channel. A prefix executes sections
0 through `chain_sections-1` in ascending order. Reset selects mode 1, count 0.

## Cycle & Degenerate-Boundary Table

Let E0 be an input acceptance edge, G=ceil(TAP_COUNT/MAC_LANES), and C the
active section count. All visibility entries refer to settled **post-edge**
values, whereas handshakes use **pre-edge** values.

| Boundary / case | Required state or result |
| --- | --- |
| Active reset, including assertion between edges | All public ready/valid low; output data/channel zero; abort traffic; clear both coefficient banks, masks, selectors, FIR histories and SOS state |
| First edge after reset release, no requests | No output; FIR-only mode; input ready; legal staging addresses ready |
| E0, bypass | Capture all input bits/channel in the sole output reservation; output visible post-E0, never before E0 |
| E0, FIR first | The literal parent FIR accepts on E0; groups execute E1 through EG |
| E0, SOS first | Engine captures on E0; sections execute E1 through EC |
| FIR-only / SOS-only result | Visible after EG / EC; first retirement on next edge |
| Mixed chain | Second block accepts the registered first result on the next edge; final result visible after E(G+C+1) |
| Continuous traffic, no stalls/controls | Input edge intervals: bypass 1, FIR G+1, SOS C+1, mixed G+C+2 |
| Retirement plus new input | Allowed on the same edge in every mode; old output retires, new transaction owns reservation |
| Pending output plus update request | No update on retirement edge; update waits for genuinely empty boundary |
| TAP_COUNT=1, MAC_LANES=1,2,4 | G=1; extra lanes contribute zero; ADDR_W=1 |
| SOS_COUNT=1, C=1 | One section execution; no extra completion stage |
| Invalid/zero/negative parameter | No illegal datapath elaborated; explicit diagnostic, not accidental width failure |
| All update-valid combinations | chain > SOS commit > FIR commit, based on asserted valid, not higher ready |
| Staging write plus sample | Both may transfer; write affects only inactive bank |
| Any update-valid plus staging write/sample | Neither staging write nor sample transfers |
| SOS commit / chain handshake plus state execution | Mutually exclusive by global quiescence; state clear has priority |

Edge-distance latency is 0/G/C/G+C+1, not an inclusive pipeline-cycle count.
There is capacity for exactly one unfinished top-level sample. Computation
continues under output backpressure, but state advances only for that sample.
Output data/channel are zero when invalid and stable through every stalled
valid cycle. Interleaving cannot mix histories. FIR history advances only on
actual FIR traversal.

## Exact arithmetic

External data and FIR coefficients are signed 16-bit codes at scale 2^-15:
[-1, 1-2^-15]. The five supplied parent modules are retained byte-for-byte,
including full 32-bit products, `32+ceil(log2(TAP_COUNT))` sum width, rounding
right 15 and signed 16-bit saturation. Coefficient 32767 has gain 32767/32768,
not exact unity.

SOS uses normalized direct form I, implicit a0=1. Coefficient addresses are
0=b0, 1=b1, 2=b2, 3=a1, 4=a2. Signed 18-bit coefficient codes have scale 2^-16
and range [-2, 2-2^-16]. Per-channel/per-section x1,x2,y1,y2 are signed 32-bit
codes at scale 2^-23: [-256, 256-2^-23]. A section input x is 32 bits; external
input promotion is exact sign extension followed by left shift 8.

```
A = b0*x + b1*x1 + b2*x2 - a1*y1 - a2*y2
y = sat32(round_even(A / 2^16))
x2 <- old x1; x1 <- x; y2 <- old y1; y1 <- y
```

Every product is signed 50-bit. Each product is explicitly sign-extended to
53 bits before addition/subtraction. In code units, |coefficient|<=2^17 and
|state|<=2^31, so |product|<=2^48 and |five-product sum|<=5*2^48<2^51.
Signed 53 bits covers [-2^52,2^52-1], including every coefficient/state extreme
and feedback negation. There is no truncation before rounding; saturated y is
stored and is the next section's input. The cascade is rounded right 8 and
saturated to signed 16 bits only once at its output. FIR->SOS promotes the
already rounded FIR result; SOS->FIR rounds before FIR. Bypass is bit-preserving.

For signed v and shift k: q=floor(v/2^k), r=v-q*2^k; increment q iff
r>2^(k-1), or r=2^(k-1) and q is odd. Saturation follows rounding. This applies
to negative ties as well as positive ties.

## Configuration, reset and liveness

Both FIR and SOS stage writes target only the selected channel's inactive bank.
FIR commit requires all TAP_COUNT addresses written since the previous commit
and preserves FIR history. SOS write readiness requires section<SOS_COUNT and
address<5. SOS commit requires all five addresses of **every physical section**
for that channel, regardless of active prefix. Duplicate writes replace data,
but cannot stand in for missing addresses. Commit toggles only that channel's
bank, clears its written mask and clears all that channel's SOS state.

Updates require no unfinished sample and no pending output. Any asserted update
valid suppresses both kinds of staging write and all new samples. Priority is
chain_valid, then sos_commit_valid, then commit_valid: a higher asserted request
blocks lower requests even if invalid or incomplete. A chain handshake installs
the descriptor, clears every channel's SOS state, and retains FIR coefficients
and history. Active banks cannot change during a transaction.

Invalid descriptor/address never handshakes. Incomplete commit never handshakes.
An invalid or incomplete update deliberately blocks samples until its producer
withdraws it or makes it acceptable; producers must eventually release such
requests. In particular, withdraw an incomplete commit to write missing entries.
Invalid staging writes do not block unrelated sample traffic. Reset aborts
everything and clears all storage and output reservations.

## Frozen fixtures and independent acceptance

Exact coefficient codes `[b0,b1,b2,a1,a2]`, scale 2^-16:

| Fixture | Codes | Analytic property to verify independently |
| --- | --- | --- |
| LP50 | [4096,8192,4096,-65536,16384] | repeated pole 0.5, unity DC |
| HP50 | [36864,-73728,36864,-65536,16384] | repeated pole 0.5, zero DC, unity Nyquist |
| LP75 | [1024,2048,1024,-98304,36864] | repeated pole 0.75, unity DC |

Fixture claims exclude arbitrary user coefficients and intentional clipping.
The independent Verifier supplies these fixtures, exact recursive integer
reference, independently formulated floating recurrence and analytic complex
response. No RTL-internal values may substitute for the reference.

Acceptance remains the admitted full work order, including:

- All 60 legal parameter tuples, >=64 accepted samples/channel/mode, every SOS
  prefix; the six extended tuples (1,1,1), (1,4,4), (8,2,2), (16,4,1),
  (32,1,2), (64,4,4), each seeds 11,29,97 and >=1024 samples/channel/mode.
- Impulses, signed steps, DC, tones, bounded random inputs, all channels,
  arithmetic ties/extrema/cancellation/state and output saturation, at least
  32 consecutive output stalls/mode, resets during work/held output, all update
  priorities, bank toggles, missing/duplicate writes, history/state retention,
  invalid requests and simultaneous retirement/acceptance.
- Cycle-by-cycle original FIR comparison for all 15 parent tuples, in addition
  to the independent FIR arithmetic oracle.
- Fixture cascades of lengths 1..4, mixed LP50/HP50 in both orders, all channels:
  excitation then >=32768 accepted zero inputs/channel and separately >=32768
  bounded-random samples/channel. Final 4096 zero-input outputs have |code|<=1.
- Nonclipping |x|<=0.25 time-domain error: maximum <=0.0004, RMS <=0.0001.
  Measured RTL impulse response >=1024 samples, amplitude 16384, at 257 uniform
  DC-to-Nyquist frequencies: maximum absolute complex-response error <=0.01
  for stable chains and FIR L1<=1. Report actual gains, meaningful phases,
  maximum/RMS errors, residual output and reference-state peaks.
- Four separately compiled and executed mutations: feedback sign, recursive
  old-state update, coefficient bank, mixed chain order. Identical workloads
  must pass pristine RTL first and reject each mutant by the ordinary scoreboard
  with a first mismatch, not compilation/timeout/log matching. Chain order
  requires a finite-precision/nonlinear discriminator.
- Real Yosys default/minimum/overhang/intermediate/maximum synthesis,
  hierarchy/connectivity/state checks, no latches/black boxes/conflicting
  drivers; all 60 legal elaborations and explicit invalid parameter diagnostics.
- Clean-directory bounded reproduction using declared sources only, with
  compatibility, all modes, channels, stall/update, RTL/floating comparison,
  all four mutation workloads and default synthesis.

No mismatches, lost/duplicate/reordered samples, active X/Z, unexpected
handshakes, reset violations or stalled-output changes are acceptable.
Thresholds and scope above are not relaxed by this implementation document.

## Reproduction and evidence

From the delivered project root, prerequisites are Python 3 standard library,
`iverilog`, `vvp`, and `yosys`; do not install tools as part of checking.
Actual versions and results must come from independent execution, not this
document. Full command: `python3 tests/verify.py`. Individual admitted commands:

```
python3 tests/verify.py --check behavior
python3 tests/verify.py --check numerical
python3 tests/verify.py --check defects
python3 tests/verify.py --check synthesis
python3 tests/verify.py --check portable
```

The last command is the bounded clean-directory check, not full acceptance.
Verifier implementation and execution are pending at Designer handoff.
Declared inputs: `rtl/*.sv`, `design/*.md`, `tests/*`, and read-only
`billet_f2_reference/rtl/*.sv`. Generated results belong under
`evidence/{behavior,numerical,defects,synthesis,portable}/`, each exposing
`results.json`. Fresh retained runs must include source hashes, exact relative
commands, tool versions, counts/cycles, seeds/configurations, raw stdout/stderr,
exit/timeout and retained failure/repair links. Child execution guards, if
needed, must be >=3600 seconds. Runtime metadata, private paths and transcripts
are not deliverable sources. The final product PDF is not a verification input.

Required reports distinguish actual transaction/configuration/mutation counts,
cycle latency/throughput, source lines/bytes/module count, generic synthesis
statistics and measured numerical response. None implies physical area, timing
closure, FPGA measurement, silicon security, arbitrary-coefficient stability or
formal proof.
