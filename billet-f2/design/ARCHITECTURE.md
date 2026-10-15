# BILLET-F2 architecture and integration

## Structure and dataflow

Compile all five files in `rtl/`; top `billet_f2_top` directly instantiates the
following named modules inside its legal-parameter generate branch
`g_supported`. Unsupported parameters instantiate none of the datapath.

| Instance | Module | Owned state / responsibility |
| --- | --- | --- |
| `u_coeff_bank` | `billet_f2_coeff_bank` | Four bank pairs, active selectors, fresh-write masks, parallel coefficient reads |
| `u_history` | `billet_f2_history` | Four delay lines, selected-channel shift and parallel history reads |
| `u_mac` | `billet_f2_mac` | Captured channel/bank, busy, tap-group progress, exact products, balanced reduction and accumulation |
| `u_round_sat` | `billet_f2_round_sat` | Combinational signed ties-to-even rounding and saturation |

Top owns one output register, readiness and commit exclusion. The history shift
and MAC capture share the accepted sample edge. Therefore the first computing
edge reads the newly shifted history, with tap zero holding the accepted sample.
Only one computation may run. The MAC selects a group of consecutive taps,
computes up to `MAC_LANES` signed products in parallel, sign-extends each to
`ACC_W`, reduces them with a balanced binary tree and adds the accumulator.
The final group is rounded combinationally and captured directly at EG; there
is no extra registered rounding stage.

The multiplication paths are generated separately, not time-shared within a
group. For lanes 1 the tree is one leaf; for 2 it has two leaves and one adder;
for 4 it has four leaves and three adders. Unused tail taps supply zero operands
and explicitly zero products. `TAP_COUNT=1` thus retains one useful multiplier
even when two or four lanes are requested.

## Internal interfaces

Child parameters use the same legal `TAP_COUNT`, `MAC_LANES` and derived widths
as top; the children are implementation modules instantiated only by the
legal top branch. Public invalid-parameter diagnostics belong to top.
All sequential state uses `always @(posedge clk or negedge rst_n)`.

| Connection | Width / ordering | Meaning |
| --- | --- | --- |
| `sample_accept` | 1 | `s_valid && s_ready`; simultaneously drives history `push` and MAC `start` |
| `active_banks` | 4 | Bit c is channel c's current active bank; zero after reset |
| `staging_complete` | 4 | Bit c is the AND of that channel's fresh-write mask |
| `read_channel` | 2 | MAC's captured input channel; held through computation and output retirement |
| `read_bank` | 1 | MAC's captured `active_banks[s_channel]`; same lifetime |
| `read_base` | `ADDR_W` | MAC tap-group base, zero at start; incremented by lanes only on nonfinal groups |
| `coeff_lanes` | `16*MAC_LANES` | Lane l occupies `[16*l +: 16]`; signed coefficient at `read_base+l` in captured bank/channel |
| `sample_lanes` | `16*MAC_LANES` | Same ordering; history tap `read_base+l` of captured channel |
| `final_valid` | 1 | Combinational `busy` and final-group test, sampled by top at EG |
| `final_sum` | signed `ACC_W` | Exact accumulator plus current full-width reduction |
| `final_channel` | 2 | Captured channel for the final sum |
| `rounded_result` | signed 16 | Combinational round/saturate output consumed at EG |

The packed operand buses are bit containers, not signed arithmetic expressions.
Each MAC lane explicitly interprets its slice as signed 16-bit data before
producing a signed 32-bit product. Each tree node and accumulated sum is signed
`ACC_W`. Thus unsigned concatenation and narrow expression sizing cannot change
the arithmetic. Out-of-range tail reads return zero; no array read is performed
for those lanes. Read-coordinate generation is separate from returned operand
arithmetic, avoiding a combinational scheduling dependency through a shared
coordinate/operand process.

`u_coeff_bank` additionally receives accepted `cfg_write`, channel, address and
data plus accepted `commit` and channel. It emits the selector/completeness
vectors. Storage is two flattened arrays of `4*TAP_COUNT` signed 16-bit words;
index `c*TAP_COUNT+k` denotes channel c, tap k. The mask is one packed
`TAP_COUNT`-bit word per channel. A commit changes only its selected selector
and mask. Configuration writes choose the opposite of the current active bank.
Top prevents simultaneous accepted commits and writes.

`u_history` receives `push`, `push_channel`, and signed `push_data`. Its storage
is one flattened array of `4*TAP_COUNT` signed 16-bit words with the same
channel/tap indexing. Only the selected channel shifts.

`u_mac` receives `start`, input channel, input bank and both packed operand
buses. It emits read coordinates, busy and final signals. Captured channel/bank
are not cleared at completion, so ownership remains stable while the output
stalls. They change only at reset or a new accepted sample after retirement.

`u_round_sat` accepts signed `ACC_W` accumulator bits and emits signed 16-bit
result. Arithmetic right shift yields a floor quotient; the low 15 bits are
the nonnegative remainder, including for negative inputs. A one-bit-wider
quotient/rounding path increments at greater-than-half or odd-half ties, then
compares to signed saturation limits.

## Ownership dependencies and simultaneous events

| Consumer requirement | Provider guarantee |
| --- | --- |
| MAC operands stay associated with the sample | MAC captures channel/bank; top forbids every commit until no busy and no output valid |
| History does not change during computation | Top accepts no sample while busy |
| Coefficients used by a computation cannot be overwritten | Writes address inactive bank; no commit can change ownership |
| Final result fits the exact sum | `ACC_W=32+ceil(log2(TAP_COUNT))`; all leaves extended before reduction |
| Output register is free at completion | No sample starts unless output is empty or retiring; busy excludes another start |
| Commit sees a complete fresh generation | Coefficient masks reset after commit/reset; readiness observes pre-edge mask |
| Simultaneous sample/configuration is safe | Sample captures active bank while configuration targets inactive bank |
| Output retirement cannot authorize a same-edge commit | `commit_ready` uses pre-edge output valid, independent of `m_ready` |

Readiness and the Cycle & Degenerate-Boundary Table in `SPEC.md` are normative.
Top gives final-result capture priority over output retirement; under the
admitted schedule the two cannot coincide. During retirement plus replacement,
the old output clears while the new computation starts independently.

Logical storage capacity, not a synthesis measurement: coefficients use
`128*TAP_COUNT` bits; histories use `64*TAP_COUNT` bits; masks use
`4*TAP_COUNT` bits; active selectors use four bits. MAC and output control/state
are additional. Asynchronous clearing and multiple combinational reads favor
register/mux inference rather than a promised physical RAM macro. No target
frequency, physical memory mapping, area or power is specified.

## Integration

From the delivered root, compile `rtl/*.sv` with SystemVerilog enabled and
select `billet_f2_top`. Defaults are 32 taps and one lane. Drive a real low
reset assertion; hold reset through initialization, then release it with
appropriate clock-domain reset discipline. This design has one clock domain;
external CDC/reset-release integration is the integrator's responsibility.
All public outputs are zero while reset is asserted.

After reset, sampling is permitted immediately with zero coefficients. To
program channel c: with `commit_valid=0`, write all taps into c's staging bank
using cfg handshakes; only then request commit for c and hold the request and
channel until accepted. Deassert the request before sampling or further writes.
Do not hold an incomplete commit while trying to fill it: commit priority
intentionally blocks those writes. Reset or withdrawal of an unaccepted request
allows progress; there is no internal pending-request storage.

Output backpressure is unrestricted. A commit held during computation or an
output stall blocks future inputs/writes but does not stop computation or output
retirement. Keep `m_ready` responsive when waiting for commit; otherwise the
consumer itself prevents release of global ownership.

## Sources, tools and portable reproduction

Design sources are `rtl/*.sv`, `design/SPEC.md`, and `design/ARCHITECTURE.md`.
Read-only compatibility sources are `billet_f1_reference/rtl/*.sv`.
Independent verification sources are `tests/*.py`, authored by Verifier after
this design handoff. Generated logs, netlists, traces and results belong under
the command's `evidence/` output directory, not the RTL source set. Runtime
metadata, private directories, transcripts and pre-existing evidence are not
source inputs and must not be copied into a portable reproduction.

Required tools are Python 3, Icarus Verilog with `-g2012` (`iverilog` and `vvp`)
and Yosys supporting `synth -noabc` and `check -assert`. No exact installed
version or behavioral/synthesis result is claimed by this document; Verifier
must capture actual tool versions in its execution receipts. Do not install
missing tools. These exact project-relative commands are the admitted suite
entry points, to be implemented and executed independently by Verifier:

```sh
python3 tests/verify.py --suite behavior --output evidence/behavior
python3 tests/verify.py --suite compatibility --output evidence/compatibility
python3 tests/verify.py --suite defects --output evidence/defects
python3 tests/verify.py --suite synthesis --output evidence/synthesis
python3 tests/verify.py --suite portability --output evidence/portability
```

The portability suite creates a fresh retained project tree from only declared
sources, then executes behavior 8/4/17, compatibility 1/17 and synthesis 8/4
without depending on the original working directory or generated artifacts.
Any static parsing is advisory only; Verifier owns functional and synthesis
execution. Generic cell counts are not physical area. Actual cycle ranges and
bank boundaries must come from handshake observations, not lane-count estimates.
The shared runtime authors and independently reviews the product PDF only after
engineering acceptance; no final report is required to run these commands.

The diagnostic-only generate guard applies lesson
`exp-6d5cc92e6f5a86894eaae275`; independent reset stimulus should apply
`exp-27d8629432f1196d886cbd13`. These are advisory lessons, not historical proof
of this implementation or dependencies on private provenance files.
