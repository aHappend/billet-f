# BILLET-F4 frozen interface and numerical specification

## Scope and provenance

Top: `billet_f4_top`. Target: Intel Arria 10 GX `10AX115N2F45E1SG`,
100 MHz or faster (clock period <= 10 ns). The immutable nine files in
`billet_f3_reference/rtl/` are transaction-level compatibility inputs, not a
numerical oracle. No repository source index or `design/GENERATIONAL_HANDOFF.md`
was supplied. Historical parent Quartus timing and PPA are therefore unavailable;
no historical pass, failure or measurement is inferred.

The delivered frontend implements a fixed 16 kHz, non-overlapping 512-point
Hann/DFT, 64-band HTK Mel and normalized unsigned 16-bit token profile. It does
not claim checkpoint-specific Anvil-E or Gemma preprocessing/API compatibility.
Programmable SRC coefficients establish anti-aliasing only when suitable
low-pass banks are actually programmed.

## Cycle and degenerate-boundary table (locked before RTL implementation)

Transfers sample pre-edge `valid && ready`. E0 is input acceptance; publication
means settled post-edge state and cannot retire on that same edge.

| Operation | Publication, excluding stalls | Boundary and priority |
| --- | --- | --- |
| Reset | All public outputs zero while `rst_n=0` | Asynchronous assertion aborts reservations; release enables acceptance on the first rising edge |
| Bypass | E0; earliest retirement E1 | Retirement and new acceptance may coincide; new payload wins |
| FIR, G=ceil(TAP_COUNT/MAC_LANES) | E(G+4) | E1..EG operand reads, then product, balanced reduction, accumulation, rounding; TAP_COUNT=1 with four lanes zeros unused lanes |
| SOS, S sections | E(7*S+1) | Seven edges per section; final PCM rounding on a separate edge |
| Either FIR/SOS cascade | E(G+7*S+6) | Inter-block transfer occurs on the next rising edge |
| SRC, T taps | E(T+3) | E1..ET reads, product one edge later, accumulation another edge later, output rounding separately; T=1 remains legal |
| SRC phase scheduler | See ARCHITECTURE.md | Retirement alone advances phase; bounded subtract cycles handle all L/M in 1..8, including non-coprime ratios |
| Window | E2 | E0 capture, E1 product, E2 quantize/write; final slot seals frame only after write |
| Spectrum bin | D517 | D1..D512 reads, D2..D513 products, D3..D514 sums, D515 average, D516 squares; transfer/next-bin start D518 |
| Mel band | B260 | B1..B257 reads, B2..B258 products, B3..B259 sums; transfer/next-band start B261 |
| Log | L7; earliest retirement L8 | Eight globally stallable registers including L0 capture |
| Mode change | Only after engines and complete frames drain | Suppresses new stream input; discards incomplete frames and clears new-path histories/error, never inherited FIR history |
| Configuration | Mode > SRC commit > chain > SOS commit > FIR commit | Inactive-bank writes coexist where independent; commits do not consume a simultaneous write |

Legal `TAP_COUNT` and `SRC_TAPS`: 1, 8, 16, 32, 64. Legal `MAC_LANES`: 1, 2, 4.
Legal `SOS_COUNT`: 1..4. Defaults: 32, 32, 1, 2 respectively.
Address widths are at least one; illegal top parameters select a diagnostic-only
generate branch with explicit `PARAMETER ERROR`. No binary mask is used for
non-power-of-two ratio wrap. A complete spectral frame has 257 beats;
`first && last` is never legal. A single SRC tap and a single SOS section require
the same registered stages as larger configurations.

## Public interface

All original `billet_f3_top` parameters/ports retain their names, widths and
signedness. Added ports:

| Ports | Format |
| --- | --- |
| `feature_valid`, `feature_ready`, `feature_mode` | Descriptor handshake; mode unsigned 2 bits |
| `src_cfg_valid`, `src_cfg_ready`, `src_cfg_channel`, `src_cfg_phase`, `src_cfg_tap`, `src_cfg_data` | Channel 2 bits, phase 3 bits, tap max(1,clog2(SRC_TAPS)), signed 18-bit Q2.16 |
| `src_commit_valid`, `src_commit_ready`, `src_commit_channel`, `src_l`, `src_m` | Channel 2 bits; unsigned ratios 4 bits, each 1..8 |
| `p_valid`, `p_ready`, `p_power`, `p_channel`, `p_bin`, `p_last` | Unsigned 32-bit power, channel 2 bits, bin 9 bits |
| `t_valid`, `t_ready`, `t_data`, `t_channel`, `t_band`, `t_last` | Unsigned 16-bit token, channel 2 bits, band 6 bits; last iff band=63 |
| `feature_error` | Sticky malformed-spectral-frame error |

Every scalar control is one bit. Mode 0 routes legacy PCM to `m_*`; mode 1
routes legacy/SRC to `m_*`; mode 2 routes legacy/SRC/window/DFT/Mel/log to `t_*`;
mode 3 routes `p_*` to Mel/log. Disabled ingress ready and disabled egress valid
are zero. Invalid payloads are zero. Sources and sinks use held ready/valid
transactions; all output payload and metadata remain stable throughout stalls.

## Integer arithmetic

Every stated rounding is nearest, ties to even (RNE). For signed v shifted by q,
take floor a=v>>q, remainder r=v-a*2^q, and increment iff r exceeds half or
equals half with a odd. Saturate only at the specified boundaries.

FIR coefficients are signed Q1.15. Sum all 16x16 products exactly in
32+ceil(log2(TAP_COUNT)) bits, RNE shift 15, saturate signed16. New sample is
tap 0. Per-channel inactive-bank completeness is required for commit.
Both banks reset logically to zero; commit preserves FIR history.

SOS coefficients b0,b1,b2,a1,a2 are signed18 Q2.16. Input PCM shifts left 8
into signed32. Each section computes
`b0*x+b1*x1+b2*x2-a1*y1-a2*y2` exactly in signed53, RNE shifts 16 and saturates
signed32. Final state is separately RNE shifted 8 and saturated signed16.
Commit requires all physical sections' five coefficients, swaps the bank and
clears that channel's SOS state. Chain commit clears all SOS state.
Chain modes: 0 bypass, 1 FIR, both with sections=0; 2 SOS, 3 FIR->SOS,
4 SOS->FIR with sections=1..SOS_COUNT. Reset selects mode 1.

SRC has eight phases, double-banked coefficients and per-channel history.
Commit requires all taps in phases 0..L-1 written since the last commit.
It clears only that channel's SRC history/progression and incomplete PCM frame.
Reset ratios are 1/1 with exact phase0/tap0 coefficient 65536 and other
coefficients zero. For input x[n], negative indices are zero:

`y[j] = sat16(RNE(sum_k x[floor(j*M/L)-k]*C[j*M mod L,k],16))`.

Use at least 34+ceil(log2(SRC_TAPS)) accumulator bits. Emit only anchors already
arrived; no implicit tail flush. Finish outputs for the current anchor before
the next globally ordered input. Skipped anchors still enter history.
Phase progression uses registered compare/subtract, never division/modulo.

## Feature mathematics and framing

Per channel, group 512 accepted SRC outputs. Window:
`W[n]=RNE(65536*(1-cos(2*pi*n/511))/2)`,
`xw[n]=sat16(RNE(x[n]*W[n],16))`.
Complete buffers are dispatched round-robin beginning at channel 0.
Unread buffers are reserved and cannot be overwritten.

For bins k=0..256, r=(k*n) mod 512, signed18 tables are
`C[r]=RNE(65536*cos(2*pi*r/512))`,
`S[r]=RNE(-65536*sin(2*pi*r/512))`.
Signed43 exact sums R and I are RNE shifted 25 into signed18 averages.
`P[k]=sat_unsigned32(ar*ar+ai*ai)` using an unsigned37 square sum.
There is no detrending, overlap, interior-bin doubling or hidden scaling.
Constants are embedded synthesizable ROM source; traversal uses nine-bit wrap.

Direct spectral frames require bins 0..256 in order, one constant channel,
last true exactly at bin256. A malformed accepted beat sets sticky error and
suppresses that frame; discard until an accepted last, including the offending
beat itself. Good subsequent frames recover. Reset/mode change clears error.

HTK knots:
`f_i=700*(10^((i/65)*log10(1+8000/700))-1)`, i=0..65.
For band b and f=16000*k/512, the triangle amplitude is
`max(0,min((f-f_b)/(f_(b+1)-f_b),(f_(b+2)-f)/(f_(b+2)-f_(b+1))))`.
Weights are unsigned17 Q1.16, including 65536. All 64 bands have nonzero support.
Exact unsigned64 weighted sums are RNE shifted16 into unsigned48 energy.
No area normalization or additional spectrum scaling.

For nonzero E, k=floor(log2(E)),
`u=floor((E-2^k)*65536/2^k)`, j=u>>12, t=u&4095.
Seventeen Q0.16 endpoints are `B[j]=RNE(65536*log2(1+j/16))`.
`log_q16=(k<<16)+B[j]+RNE((B[j+1]-B[j])*t,12)`.
`relative_q16=log_q16-(30<<16)`.
`db_q16=RNE(relative_q16*197283,16)`.
Clamp d to [-96*65536,0], then
`token=sat_unsigned16(RNE((d+96*65536)*21845,21))`.
E=0 produces token0. Bands emit in increasing order, frames contiguous.
Independent verification must bound PWL mathematical error including
quantization to <=0.001 log2 units; token parity uses the frozen PWL rule.

## Acceptance and ownership

Designer owns `rtl/*.sv` and `design/*.md`; independent Verifier owns
`tests/*.py`, numerical models and generated testbenches. Designer may perform
only a fast syntax/elaboration check, not functional simulation or synthesis.
The mandatory independent commands are:

```
python3 tests/verify.py --suite behavior
python3 tests/verify.py --suite sensitivity
python3 tests/verify.py --suite synthesis
python3 tests/verify.py --suite reproduction
```

No arguments runs those four suites. `--suite portable-smoke` is the clean-copy
reproduction child. Tools: Python3 standard library, Icarus, vvp, Verilator,
Yosys; no dependencies are installed by the scripts. Native shared-runtime gates
own timing lint, Quartus 17.1 <=10 ns/WNS>=0 STA and three consecutive
deterministic Arria10 PCIe Gen3x8 HIL passes. Generic synthesis is not timing.

Behavior must cover all 60 inherited parameter combinations and all SRC tap
boundaries; seeds 11,29,47; all 64 L/M ratios on four channels with >=256
inputs/channel/case; all five legacy modes with >=512 inputs/channel/seed;
>=8 PCM frames/channel/seed for 8,16,32,48 kHz to16 kHz plus 24->16 kHz;
every DFT bin, Mel energy and top token; all257 spectral impulses and malformed
recovery; exponent/PWL boundaries and >=4096 random energies; reset, priorities,
bank rewriting, ties, saturation, gaps, 1/7/127-cycle stalls and exact cycles.
Independent mathematics and immutable-parent differential checks are separate.
Anti-alias measurement programs an independently generated normalized 32-tap
1/3 windowed-sinc bank: equal amplitude12000 1kHz/12kHz tones at48kHz,
discard >=64 input transient samples, require >=35dB rejection and <=1dB pass
amplitude error, retaining coefficients/sample counts/RMS.

Sensitivity requires six successfully compiled disposable mutants numerically
or protocol-rejected: FIR rounding, SOS feedback sign, SRC phase, Mel weight,
PWL interpolation, stalled-token retention. Compiler failure/timeout is not a
kill. Synthesis uses real Yosys complete default/minimum/maximum tops, connected
arithmetic/state, consistency and zero-latch/div/mod assertions. Reproduction
copies only deliverable sources/docs/tests and read-only parent, verifies hashes,
and executes real Icarus leaf and compiled Verilator SRC-to64-token comparison.
No future product PDF is an engineering prerequisite. The shared runtime
authors/reviews/renders that report only after engineering acceptance.

Each execution uses a unique `evidence/<stem>-<timestamp_or_hash>/`, retaining
argv/cwd, hashes, tools, times, exit/timeout and raw logs. Failed receipts remain.
No runtime metadata, credentials, private host paths or transcripts are shipped.
Compact Icarus runs stay <=500k cycles; substantial frames/matrices/mutations
use compiled Verilator. No per-beat console tracing or per-request flushing.
