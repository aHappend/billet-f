# BILLET-F5 architecture

## Status and ownership

Top: `billet_f5_top`. Target: Intel Arria 10 GX `10AX115N2F45E1SG`,
100 MHz or faster (period <=10.0 ns). Design sources are `rtl/*.sv`.
The eighteen parent modules are copied byte-for-byte from read-only
`billet_f4_reference/rtl/`; their source index and historical physical results
are in `design/GENERATIONAL_HANDOFF.md`. Those results are not F5 signoff.
Independent Verifier owns `tests/*`; the shared runtime owns timing, hardware
gates and the independently approved final product PDF.

## Cycle & Degenerate-Boundary Table

All transfers use **pre-edge** valid AND ready. Newly registered valid is
observable **post-edge**, and cannot transfer on that same edge.

| Boundary | Required implementation |
|---|---|
| Reset asserted | New public outputs, including ready and payload/status, are zero; pending work is aborted. New sequential control uses asynchronous active-low reset. |
| First released edge | Legal ready may be high before this edge; a new input may transfer. No reset-time transfer is counted. |
| Beamformer E0..E7 | E0 captures selected history/current operands; E1 products; E2 interpolant sums; E3 individual rounded samples; E4 pair sums; E5 quartet sums; E6 total; E7 rounded output. Earliest output transfer E8. |
| Beamformer blocked final stage | All eight stages and metadata stop together. History advances only on a real input transfer, not on pipeline bubbles. |
| DELAY_DEPTH=1 | Ring head stays zero. Age zero is current; age one is the sole preceding tuple. Fractions 0 and 255 remain meaningful. |
| DELAY_DEPTH=17 | Explicit head wrap at 16; history lookup adds depth on subtraction underflow, never uses a power-of-two mask. |
| Maximum delay | k=depth-1 can interpolate against age depth. Missing ages are zero using fill metadata. |
| Same-edge history read/write | E0 reads old history and writes current tuple; current age zero bypasses memory. Oldest legal history is captured before replacement. |
| AEC singleton | T=1 still performs one dot/square group and one full 56-iteration update; denominator includes regularization. |
| AEC E0..E(3T+2) | E0 captures vector and power squares; E1 updated powers and first operands; E2 freeze and first product; each group accumulates at E(3+3j); y at E(3T+1), e at E(3T+2). |
| AEC tap update B=3T+3+60j | B gradient product; B+1 divider load and leaked old weight; B+2..B+57 56 restoring iterations; B+58 signed quotient plus leakage; B+59 saturation/conditional write. |
| AEC finish | Valid asserts after E(63T+3); first transfer at following edge. Next transaction waits until result transfer. Frozen/disabled samples retain identical latency. |
| Audio fork | One storage stage, two independent pending acknowledgements. A consumer that already accepted is not offered that sample again. |
| VAD closing bin at E0 | E0 closes sum; E1 updates hysteresis; E2 publishes event and held frame decision. Earliest event transfer E3. |
| VAD/event/token overlap | Next spectrum frame cannot transfer bins until both current event and all current tokens (including internally discarded tokens) retire. |
| Incomplete acoustic frame | Spectrum idle excludes partial-frame count. Commit may discard partial samples, but not a window write still pending or a completed frame. |
| Commit versus input/config | Any asserted commit request blocks new tuples/writes. Already accepted work continues. Commit acceptance has clear/swap priority and requires complete staging plus global acoustic drain. |
| Withdrawn commit | No swap, state clear or mode change occurs without the actual commit handshake. |
| Reset/commit versus arithmetic | Clear wins; divider, valid pipelines, history validity, weights, powers and hangover are cleared. |
| Stalled event/last token/audio | Payload and valid remain stable; commit waits. No ready-dependent valid or duplicate fork delivery. |

## Structure and storage

The top retains the entire inherited port/parameter interface and directly
instantiates `billet_f4_top u_legacy`. No new control gates inherited traffic.
Its five chain modes, four channel histories, coefficient banking and feature
selection remain parent behavior.

The independent mono path is:

```
a_mics/a_far -> u_beamformer -> u_aec -> u_audio_fork -> audio consumer
                                              |
                                              +-> u_acoustic_spectrum
                                                       | -> u_vad -> event
                                                       +-> u_acoustic_mel
                                                            -> u_acoustic_log
                                                            -> whole-frame gate
```

`u_divider` is inside `u_aec`. Acoustic spectrum/Mel/log use channel zero.
No new-path resampling is inserted. Complete synchronized eight-microphone
tuples are nominally 16 kHz. AEC history includes the current aligned far sample.

The beamformer holds eight rings of DELAY_DEPTH signed samples, a shared head
and fill count, two eight-delay banks and an inactive-bank written mask.
The inactive bank is not readable until all entries have been written.
Reset uses the zero-delay bank. The beam pipeline carries far metadata through
all eight stages. Ring storage is intentionally small, multi-read register
storage, not a purported single-port RAM.

### Beamformer storage-ownership repair

The retained native failure receipt
`.vulcan/engineering/run-0001/quartus-sta.json` reports Quartus errors
10028/10029 for multiple drivers on flattened beamformer history, followed
by hierarchy failure and STA exit3 without extracted timing paths.
Eight generated clocked writers used dynamically indexed slices of one array;
Quartus did not resolve their disjoint ownership. The repaired RTL retains
that array but gives it one synchronous writer with a constant-bounded
eight-microphone loop. This diagnostic receipt is historical context, not a
product input or execution prerequisite.

Writes remain nonblocking and enabled only by accepted tuples. Operand capture
still reads pre-write history, including the oldest legal age at head wrap.
Reset/commit still invalidate history through head/fill metadata without
resetting storage. Delay banks, arithmetic, far alignment, the eight registered
stages and global stalls are unchanged. Fresh affected checks and native
Quartus execution are required; this repair does not establish timing closure.

AEC holds T signed 24-bit weights, a T-entry captured sample vector, far history,
32-bit power estimates, a 45-bit dot accumulator and 36-bit denominator.
The serial 56-bit magnitude divider retains a 36-bit remainder and uses a
37-bit trial subtract. The weight update uses a signed 58-bit sum, so there is
no premature quotient clipping. All updates use the same old vector/error and
the corresponding old weight; no tap is revisited in a transaction.

The fork owns its storage until both consumers acknowledge it. The spectrum
owns frame storage until bin256 transfers; Mel then owns the 257-bin powers
until band63 transfers. VAD holds the decision independently of event
acknowledgement. A top-level token-retired bit prevents reusing that decision
when the event consumer stalls after all tokens have drained. Every power bin
still visits Mel for suppressed frames.

Commit readiness combines beamformer/AEC/fork emptiness, spectrum idle,
Mel/log idle and VAD idle. This makes accepted old-epoch work observable before
the swap while allowing partial time-domain frames to be discarded. Commit
clears the spectrum and Mel through their existing clear inputs; the drained
log needs no additional reset signal.

## Inherited registered boundaries

These are descriptions of retained RTL, not new shortened latency promises.

| Engine | Registered sequence, without downstream stalls |
|---|---|
| FIR | Top captures input/history and starts MAC. MAC issues synchronous coefficient/history reads in groups of MAC_LANES; read-valid, product-valid and reduction-valid pipelines precede accumulation. Last accumulation raises done; top rounds/stores the completed sum and presents its output. |
| SOS | Input capture; for each section stages 0 operands/coefficient capture, 1 five products, 2 pair reductions, 3 numerator/denominator reductions, 4 subtraction, 5 rounded state, 6 history update/section transition. A finishing cycle rounds/stores the public output. |
| SRC | Input/history acceptance in IDLE; CALC synchronous operand read, product register, accumulation and round-pending; OUTPUT stores/holds one rounded sample. After output transfer, WRAP repeatedly subtracts L from phase+M, counts consumed-input distance, then either computes another phase or returns to input collection. |
| Spectrum window | Accepted sample loads synchronous Hann ROM and captures sample/index; next edge product; following edge rounded sample memory write and count/full update. |
| Spectrum transform | Full-channel selection in IDLE; 512 synchronous frame/twiddle reads per bin; registered products; accumulated real/imag sums; round-pending, rounded averages, square-sum, saturated power register; OUTPUT holds bin until transfer. Repeat bins0..256. |
| Mel | Fill257 powers; START resets reduction; per band257 synchronous power/weight reads, registered products, 64-bit accumulation, round-pending and registered energy; OUTPUT holds band. Repeat bands0..63. |
| Log | Eight globally enabled stages: input energy, normalization/exponent, endpoint/interpolation, interpolated log, dB product, clipped dB, normalization product, token. E0 input is visible after E7 and transferable at E8. |

Inherited chain modes are 0 bypass, 1 FIR, 2 SOS, 3 FIR then SOS, and
4 SOS then FIR. Feature modes are 0 inherited audio, 1 SRC audio,
2 SRC/spectrum/Mel/log, and 3 external-power/Mel/log. Reset selects
chain mode1 and feature mode0.

For exact legacy arbitration and descriptors, the unchanged public interface
and implementations of `billet_f4_top`, `billet_f3_top` and `billet_f2_top`
remain authoritative compatibility inputs. There is no combinational division
or modulo in the acoustic datapath.

## Throughput and physical intent

AEC is deliberately time-multiplexed. Its fixed visible-result latency is
63T+3 edges after acceptance: 2019 clocks for T=32, followed by output
acknowledgement and next acceptance. This is below the 6250-clock nominal
16 kHz sample budget. Beamformer and fork add bounded pipeline storage.
Spectrum computes 257 direct-transform bins, and Mel performs 64 reductions;
their frame work is comfortably below the 3,200,000 clocks available per
512 samples at 16 kHz. Backpressure may intentionally reduce throughput.
These are architectural budgets, not measured throughput or timing closure.

Multiplication, addition/reduction, rounding and saturation are separated at
the admitted registered boundaries. There are no generated clocks. Generic
synthesis does not establish 100 MHz timing. Native timing lint, actual
Quartus17.1 post-fit setup/hold WNS>=0, and three deterministic PCIe bridge
passes remain required. Bridge connectivity is not acoustic execution.

## Integration and reproduction

Defaults: TAP_COUNT=32, MAC_LANES=1, SOS_COUNT=2, SRC_TAPS=32,
DELAY_DEPTH=8, AEC_TAPS=16. Legal settings and numerical rules are in
`design/SPEC.md`. Assert `rst_n=0`, then release with consumers ready as
appropriate. The initial acoustic epoch uses zero delays, AEC enabled and VAD
gating enabled. To configure, write all eight inactive entries with valid/ready
handshakes, hold commit valid while draining consumers, and retain the desired
AEC/gate enables until commit acceptance. Payloads must remain stable while
their valid is stalled.

Required L1 dependencies are Python3 (standard library), Icarus (`iverilog`
and `vvp`), compiled Verilator with a C++ toolchain, and Yosys. No dependency
installation is part of this delivery. Actual tool versions belong in fresh
verification receipts. From the delivered project root:

```
iverilog -g2012 -t null -s billet_f5_top rtl/*.sv
python3 tests/verify.py --suite compatibility
python3 tests/verify.py --suite acoustics --seed 17
python3 tests/verify.py --suite acoustics --seed 29
python3 tests/verify.py --suite acoustics --seed 43
python3 tests/verify.py --suite robustness
python3 tests/verify.py --suite defect-sensitivity
python3 tests/verify.py --suite synthesis
python3 tests/verify.py --suite portable
```

The independent Verifier supplies those test entrypoints and helpers; they are
not designer-authored results. Declared portable inputs are `rtl/*.sv`,
`design/*.md`, `tests/*`, and read-only `billet_f4_reference/rtl/*.sv`.
Fresh generated results live in exclusive `evidence/l1-<suite>-<id>/`
directories. No report, private host path, runtime metadata or previous binary
is an execution prerequisite. Final report generation occurs only after
engineering acceptance.
