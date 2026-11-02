# BILLET-F4 architecture and integration

## Connected hierarchy

```
billet_f4_top
  u_legacy : billet_f3_top
    u_fir : billet_f2_top
      u_history / u_coeff_bank / u_mac / u_round_sat
    u_sos : billet_f3_sos_engine
      u_coeff_bank / u_state_round / u_output_round
  u_src : billet_f4_src
  u_spectrum : billet_f4_spectrum
    u_window / u_twiddles / signed rounding stages
  u_mel : billet_f4_mel
    u_weights
  u_log : billet_f4_log
    u_lut
```

Legal datapaths reside in `g_supported`, preserving the inherited supported/
unsupported generate convention. Compile only `rtl/*.sv` for F4: never include
the immutable parent directory in the same hierarchy compilation because the
legacy module names intentionally match. Differential verification compiles
the parent separately.

All added datapaths are connected in the required modes. The legacy hierarchy
and leaf interfaces remain; FIR memories now have registered reads and logical
reset validity, and MAC arithmetic is retimed. SOS operands, five products,
pair reductions, total numerator, feedback subtraction, state quantization and
history update occupy separate edges. The final PCM rounding is separate.
Transaction values, update priorities and inherited history-clear semantics
remain the compatibility target; old ready waveforms and cycle latency do not.

## Reset, descriptors and reservations

Drive `rst_n` low before sending any descriptor or sample, then release with
stable inputs. All public outputs, including readiness, are zero during reset.
After release use pre-edge ready/valid to determine actual transfers. Reset
aborts all outstanding transactions, clears configuration completeness and
logical histories, resets chain to FIR-only and feature mode to legacy-only.
FIR/SOS coefficients reset to zero; SRC resets to exact identity at ratio1/1.

Program every required inactive-bank entry using its write handshake before
asserting a commit. A valid but illegal/incomplete descriptor holds ready low
and is not consumed. Withdraw it to permit other work. Preserve request
payload until accepted. An accepted FIR commit retains history; an SOS commit
clears that channel's SOS history; a chain descriptor clears all SOS history.
SRC commit clears that channel's SRC history/phase and partial PCM frame, and
discards a matching partially collected direct spectral frame. It does not clear
the sticky framing error or the discard-until-last recovery state.
SRC phases outside the committed L are not required and cannot be read.
Reusing an inactive bank still requires writing a fresh complete staging mask.

Requests inhibit new external stream admission. Already reserved work continues
through the current mode until complete engines/frames/outputs drain.
`legacy_reserved` tracks actual legacy input acceptance and output retirement,
including simultaneous bypass retirement/admission. Feature-mode acceptance
requires this reservation and all new engines to be empty, then clears new-path
histories and incomplete frames/error without resetting legacy state.
An incomplete spectral frame or a discard-until-last state is not an engine
reservation and therefore cannot deadlock a mode change.

At a common eligible boundary priority is feature mode, SRC commit, then
inherited chain/SOS/FIR commit priority. Inactive-bank writes are independent:
SRC writes may coexist with inherited activity/configuration, but not with a
feature request or SRC commit request. Inherited writes retain the parent's
request inhibition. New-path reservations must empty before inherited descriptor
acceptance when those paths are in use.

## SRC scheduling and storage

Each channel owns its active-bank bit, default-identity flag, staging-written
mask, L/M, circular head, valid-history count, current phase and anchor gap.
History is one synchronous single-write storage array; its read checks valid
count before using data. Coefficients are synchronous storage, with an exact
identity mux until the first channel commit. Reset/mode changes invalidate
history rather than clearing every memory word.

E0 accepts an input and writes the newest circular slot. If its anchor is
eligible, E1..ET read tap/sample pairs, E2..E(T+1) register products,
E3..E(T+2) accumulate exact signed products and E(T+3) publish rounded PCM.
A skipped anchor only updates history and decrements its channel's gap.
The global top reservation prevents the next input entering the legacy chain
until all SRC outputs for the current accepted anchor have retired.

Let R0 be retirement of an SRC output. At R0, register `phase_work=phase+M`
and `distance=0`. Each following edge whose work>=L subtracts L and increments
distance. The first following edge with work<L commits the new phase. Thus,
with q=floor((phase+M)/L), normalization finishes at R(q+1), with q in0..8.
If q=0, that edge is reused-anchor E0; its next read is E1 and output appears
T+3 edges later. If q>0, store gap=q-1 and return idle at R(q+1).
The next q-1 accepted input anchors are skipped; the following one starts the
next calculation. Phase changes only after actual output retirement. All
L/M in1..8, including8/8 and non-coprime ratios, use the same bounded scheduler;
there are no division/modulo operators. No tail samples or outputs are invented.

For mode1 ratio1/1, without upstream/output stalls, SRC's serialized input
interval is T+6 edges: T+3 publication, next-edge retirement, one subtraction,
one normalization/return-to-idle edge and next-edge input acceptance. The top's
legacy processing adds its own latency before SRC admission. Interpolation
adds serialized reused-anchor calculations; this is not a one-PCM-per-cycle
frontend.

## Frame buffers, spectrum and Mel

The spectrum engine owns one512x16-bit frame buffer per channel (4096 bytes
total). Each channel's count identifies its incomplete frame; a full bit reserves
all512 words until its last power bin retires. The window unit is serialized:
E0 captures PCM and synchronous Hann read, E1 registers a signed product,
E2 quantizes/writes and advances the count/seals the full flag. A new window
sample is accepted no earlier than E3. Full buffers backpressure only their
channel; the shared window slot backpressures all channels while occupied.

Round-robin dispatch starts at channel0. After choosing a complete frame the
next search begins at the following channel, wrapping in2 bits. No read frame
can be overwritten. Incomplete frames do not enter dispatch. All257 powers
from a selected frame are contiguous. Per-bin D0 initializes count, twiddle
phase and exact43-bit accumulators; D1..D512 synchronously read operands,
D2..D513 multiply, D3..D514 accumulate, D515 round averages, D516 square/sum,
D517 publish. The next bin starts on power retirement D518 without stalls.
Nine-bit phase addition performs modulo512 traversal.

Mel owns one257x32-bit power buffer (1028 bytes), accepting only while collecting
or discarding a frame. It validates each accepted bin/channel/last before writing.
Any malformed frame yields no energies/tokens, sets sticky error and recovers
at an accepted last. A well-formed last reserves the buffer for all64 bands.
The first band's B0 is the edge after that last; subsequent B0 coincides with
the prior band's energy retirement. B1..B257 read power and synchronous ROM
weights, B2..B258 multiply, B3..B259 accumulate, B260 publish rounded energy.
All64 bands are ordered; only after band63 energy retirement can a new spectral
frame overwrite the buffer.

Each spectral bin supports at most two triangular bands. The Mel ROM stores
two7-bit band selectors and two17-bit weights, with127 denoting absent support.
This is mathematically the same fixed64x257 triangular table but only257x48
bits of constant storage, avoiding a giant combinational arithmetic ROM.
Hann, twiddle and Mel tables have synchronous registered reads; log endpoints
use a small combinational16-entry case table. Constants were generated from
the SPEC equations with90-digit standard-library Decimal arithmetic,
Taylor-series trigonometry and ties-to-even quantization; no external memory
files, private paths or runtime generated data are required. Independent
verification regenerates expectations from mathematics, not these ROMs.

## Log pipeline and throughput limits

The log stage has eight globally enabled registers. Capture is L0; grouped
leading-bit selection/normalization L1; endpoint/interpolation product L2;
rounded interpolated log2 L3; signed dB product L4; RNE/clipping L5; unsigned
normalization product L6; rounded/saturated token L7. Every valid/channel/band/
last/zero tag advances under the same enable. If the final valid token stalls,
the entire pipeline holds and input ready drops. Otherwise consecutive energies
can enter on successive edges; earliest no-stall retirement is L8.

A serialized DFT frame takes approximately257*518=133126 cycles plus dispatch.
Mel takes approximately64*261=16704 cycles plus initial start. Staging can
overlap a later DFT with a prior Mel frame, but a new DFT stalls at its output
when Mel's single power buffer is reserved. These are analytical schedule
counts, not measured throughput or timing closure. At100MHz DFT alone needs
about1.33ms/frame and Mel about0.167ms/frame, well below a32ms frame duration,
subject to input processing and backpressure. Sustained ingress depends on
FIR/SOS latency, SRC ratio, channel distribution and output readiness.
The multi-channel buffers absorb one complete frame per channel, not unbounded
bursts; upstream must honor backpressure. No independently consumed PCM/token
fork exists.

## Reproduction and pending independent checks

From a delivered root, the static top compilation is:

```
iverilog -g2012 -s billet_f4_top -t null rtl/*.sv
```

This compilation checks syntax/elaboration only, not numerical behavior or
synthesis. Functional reproduction and four mandatory commands are specified
in SPEC.md and are owned by the independent Verifier. Required installed tools
are Python3, Icarus/vvp, Verilator and Yosys; tool versions must come from actual
execution receipts, not assumed host versions. No tool installation, private
host device addressing or prebuilt binary is required. The shared runtime owns
native timing lint, Quartus17.1 STA and board HIL. None is replaced by cycle
tables, ROM generation or static elaboration. Historical parent STA/PPA remains
unavailable because GENERATIONAL_HANDOFF.md was absent. Engineering acceptance
and the runtime-authored, independently approved product PDF remain pending.
