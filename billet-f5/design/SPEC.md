# BILLET-F5 admitted product contract

## Scope, provenance and acceptance authority

Implement a synthesizable fifth-generation eight-microphone adaptive
beamforming, NLMS acoustic echo cancellation and voice-driven Log-Mel speech
frontend. Top is `billet_f5_top`; target is Intel Arria 10 GX
`10AX115N2F45E1SG` at >=100 MHz (clock period <=10.0 ns).
Preserve F1/F2/F3/F4 multichannel FIR, cascaded SOS IIR, polyphase resampler,
and 64-band Log-Mel engine. Both inherited and acoustic streams operate
independently and concurrently.

The eighteen-module source index is in `design/GENERATIONAL_HANDOFF.md`.
`billet_f4_reference/rtl/*.sv` is read-only provenance, not an independent
oracle. Preserve all eighteen deliverable copies initially byte-for-byte,
including interfaces, parameters, hierarchy, coefficient banks, reset and
numerical modes. Any necessary integration/timing repair must be documented
and independently regression-tested. No parent specification or verification
suite was supplied. Historical parent post-fit setup WNS1.663 ns and hold
WNS0.037 ns at100 MHz do not certify F5. Historical L3 evidence establishes
bridge connectivity only, not hardware acoustic processing.

Designer owns `rtl/*.sv` and design documentation and may run at most fast
static syntax/elaboration. Independent Verifier owns `tests/*`, models,
testbenches, mutations and functional/synthesis execution. Independent Reviewer
admits engineering evidence. The shared runtime owns execution, native timing
and hardware gates, and subsequent independent report production. The final
PDF is required after engineering acceptance, never as an L1 prerequisite.
No runtime-owned metadata is a product source or verification dependency.

## Interface and legal settings

Retain the exact inherited `billet_f4_top` parameter/port names, widths and
signedness. Literal instance `billet_f4_top u_legacy` connects them directly,
without new-path arbitration or timing changes.

| Parameter | Default | Legal values |
|---|---:|---|
| TAP_COUNT | 32 | 1,8,16,32,64 |
| MAC_LANES | 1 | 1,2,4 |
| SOS_COUNT | 2 | 1..4 |
| SRC_TAPS | 32 | 1,8,16,32,64 |
| DELAY_DEPTH | 8 | 1,8,17,32 |
| AEC_TAPS | 16 | 1,8,16,32 |

ADDR_W=max(1,clog2(TAP_COUNT)), SRC_AW=max(1,clog2(SRC_TAPS)),
DELAY_W=max(1,clog2(DELAY_DEPTH)). Illegal settings use a diagnostic-only
generate branch, elaborate without invalid widths/arrays and terminate
simulation nonzero with `PARAMETER ERROR`. Do not instantiate the invalid
datapath ahead of that diagnostic (lesson exp-6d5cc92e6f5a86894eaae275).

New top ports, unsigned unless marked signed:

| Ports | Direction and width | Meaning |
|---|---|---|
| a_mics | input128 | Eight signed16 samples, mic i at bits16*i+:16 |
| a_far | input signed16 | Synchronized far-end reference |
| a_valid / a_ready | input / output1 | Complete tuple transfer |
| a_cfg_valid / a_cfg_ready | input / output1 | Inactive delay-bank write |
| a_cfg_mic | input3 | Microphone index0..7 |
| a_cfg_delay | input DELAY_W+8 | Unsigned Q8 delay |
| a_commit_valid / a_commit_ready | input / output1 | Atomic acoustic epoch change |
| a_aec_enable / a_gate_enable | input1 each | Captured on commit |
| audio_valid / audio_ready | output / input1 | Post-AEC audio stream |
| audio_data | output signed16 | Beamformed echo-cancelled sample |
| audio_frozen | output1 | Exact adaptation-freeze decision for sample |
| a_t_valid / a_t_ready | output / input1 | Acoustic token stream |
| a_t_data / a_t_band / a_t_last | output16 /6 /1 | Token, band0..63, final-band marker |
| vad_valid / vad_ready | output / input1 | One event per complete frame |
| vad_active / vad_energy | output1 /48 | Current-frame decision and spectral energy |

All new state uses one clk and active-low asynchronous rst_n, with control
`always @(posedge clk or negedge rst_n)`. Unreset memory is allowed only with
reset fill/valid metadata preventing unwritten contents from being observed.
All transfers occur on a rising edge with valid&&ready immediately before it.
Sources hold valid and payload when stalled. All new public ready, valid,
data/status outputs are zero during reset. Output payload is zero whenever
its valid is low. Reset aborts pending transactions and partial frames without
phantom output. The first released edge may accept a legal input.

Required literal new instances: `billet_f5_beamformer u_beamformer`,
`billet_f5_nlms u_aec`, `billet_f5_audio_fork u_audio_fork`,
`billet_f4_spectrum u_acoustic_spectrum`, `billet_f5_vad u_vad`,
`billet_f4_mel u_acoustic_mel`, `billet_f4_log u_acoustic_log`;
`billet_f5_udiv u_divider` is inside u_aec. Additional small control/rounding
helpers are permitted; these functional instances cannot be replaced or
flattened in source.

The acoustic frontend is mono at nominal16 kHz, with complete synchronous
tuples rather than eight arbitrated inputs. No new-path resampling is inserted;
the inherited configurable resampler remains available. The fork delivers
each sample exactly once to public audio and spectrum, with independent
acknowledgements, not ready-dependent valids or repeated transfers.
Spectrum/Mel/log use channel zero internally. All bins visit Mel regardless
of VAD; only complete64-token frames are forwarded or discarded after their
decision. Independent output stalls cannot lose, duplicate or misassociate
samples, decisions or tokens. With continuously ready sinks and no config
traffic, sustain >=16000 tuples/s at100 MHz, including AEC_TAPS32.

## Configuration and epochs

The low eight delay bits are f in0..255; upper DELAY_W bits are k in
0..DELAY_DEPTH-1. Retain DELAY_DEPTH previous samples per microphone, in
addition to selecting current input. Maximum k may require age DELAY_DEPTH.
History advances by accepted tuples, never elapsed clocks. Missing history
is exactly zero. Non-power-of-two rings use explicit wrap.

Writes target an inactive eight-entry bank and set a per-entry written mask.
An invalid k drives a_cfg_ready low without state modification. Commit
requires all eight inactive entries written. Any valid commit request blocks
new acoustic input and configuration writes while already accepted work
continues draining. Acceptance waits for all beamformer/AEC/fork samples and
all complete spectrum/Mel/log/VAD results to retire. An incomplete spectrum
frame is discardable and cannot permanently prevent readiness.

Commit atomically swaps delay banks, captures enables, clears acoustic
histories, adaptive weights/powers, partial frames, VAD/hangover and staging
mask. No old-epoch sample or token may appear after commit acceptance.
Withdrawing a pending request performs no swap. Reset selects eight zero
delays, AEC enabled, gating enabled and logically zero adaptive histories and
weights. Acoustic control never blocks inherited traffic; inherited control
priority/rejection behavior is unchanged.

## Exact numerical rules

RNE(z,s) divides signed integer z by2^s, rounding nearest with ties to even,
including negative inputs. satW is signed W-bit saturation. Widths are
minimum exact widths, not permission to truncate intermediate results.
The independent Python model uses arbitrary-precision integers and explicit
signed conversion, rounding and saturation.

### Beamformer

For microphone i at accepted index n:

```
u = x_i[n-k_i]
v = x_i[n-k_i-1]
b_i = sat16(RNE((256-f_i)*u + f_i*v, 8))
b = sat16(RNE(sum_i(b_i), 3))
```

Round each microphone before summation; all eight equal weights are1/8.
The far-end sample travels with the same transaction, without spatial filtering.

### NLMS echo cancellation

Signed24 weights represent w_j/2^22. Signed16 x_j is far history a_far[n-j],
including current x_0 and startup zero-fill.

```
D = 65536 + sum_j(x_j*x_j)                 # unsigned >=36 bits
y = sat16(RNE(sum_j(w_j*x_j), 22))         # exact signed dot >=45 bits
e = sat16(b-y)
Ed_new = Ed - (Ed>>5) + ((b*b)>>5)         # unsigned32
Ex_new = Ex - (Ex>>5) + ((x_0*x_0)>>5)     # unsigned32
freeze = (Ex_new < 65536) or (Ed_new > 2*Ex_new+65536)
```

Power estimates update once per transaction, and the freeze comparison is
widened. If frozen, weights remain bit-identical, including no leakage.
Otherwise all taps use old weights, the same error and captured history:

```
leaked_j = w_j - RNE(w_j,16)
delta_j = trunc_toward_zero((e*x_j*2^21)/D)
w_new_j = sat24(leaked_j + delta_j)
```

Normalized step is exactly1/2, regularization65536, leakage approximately
1/65536 per adapting sample. No delta clipping before final weight saturation.
Numerator uses unsigned56-bit magnitude and nonzero unsigned36-bit divisor;
restore the quotient sign. Division is one-bit-per-cycle restoring division,
not a combinational operator.

Enabled audio is e and audio_frozen is the exact protection decision.
Disabled audio is b, audio_frozen=1, and weights do not change; history/powers
may advance on the same schedule. Commit resets adaptive state. Spectral VAD
does not freeze AEC merely because far-only echo has speech-band energy;
sample-power protection avoids startup convergence deadlock.

### Spectral, Mel and token arithmetic

Preserve the parent's512-sample non-overlapping symmetric-Hann transform,
257 real bins,64 bands and token encoding:

* Hann[n]=RNE(65536*(1-cos(2*pi*n/511))/2,0).
* Twiddles are cosine and negative sine at2*pi*q/512, signed Q16.
* Windowed sample=sat16(RNE(sample*Hann,16)).
* Exact real/imag sums are independently RNE-shifted25 and saturated18.
  Their squared-magnitude sum saturates to unsigned32.
* HTK Mel breakpoints j=0..65:
  f_j=700*((1+8000/700)^(j/65)-1). Evaluate each triangle at
  16000*k/512 and RNE-quantize weights to unsigned Q16.
  Band energy=RNE(sum(power*weight),16), held unsigned48.
* Log endpoints i=0..16 are RNE(65536*log2(1+i/16),0).
  Retain4-bit interval/12-bit residual interpolation and its RNE rules.
  Relative log2 reference is30*65536; conversion multiplier197283,
  RNE shift16; clip to[-96*65536,0].
  Token=RNE((clipped+96*65536)*21845,21), saturated65535.
  Zero energy gives token zero.

Reconstruct reference constants mathematically, never from DUT ROM extraction.
Separately assert generated constants match every shipped ROM entry.

### Spectral-energy VAD

Sum unsigned power for inclusive bins4..128 into unsigned48 E per complete
frame. ON=65536, OFF=32768, hangover3. Initially active=false, hang=0.
If E>=ON: current frame active, hang=3. Else if previously active and
E>=OFF: remain active and retain hang. Else if previously active and hang>0:
remain active and decrement hang. Else inactive and hang=0.

Emit exactly one E/decision event per complete frame, including silence.
With gating enabled emit exactly bands0..63 for active frames, no transfers
for inactive frames; last is true only for band63. Gating disabled emits
all frames but retains VAD events. Serialize retirement so stalled events
cannot attach decisions to another frame's tokens.

## Exact cycle contract

E0 is the accepting rising edge using pre-edge ready/valid. Post-edge values
are after nonblocking assignments settle; newly asserted valid transfers no
earlier than the following edge. The detailed boundary table and inherited
registered sequences are in `design/ARCHITECTURE.md`.

Beamformer is an eight-register-stage globally stallable pipeline:
E0 selected u/v and far capture/history advance; E1 two products;
E2 their sums; E3 individual rounded microphones; E4 four pair sums;
E5 two quartet sums; E6 eight-way sum; E7 rounded beam and aligned far.
No-stall first transfer is E8. Blocking final valid disables global advance.

AEC with T taps: E0 captures transaction/vector and power squares.
For j=0..T-1, E(1+3j) captures old weight/sample, E(2+3j) products/square,
E(3+3j) accumulates dot/D. Power estimates update E1, freeze at E2.
E(3T+1) registers y, E(3T+2) e.
For tap j let B=3T+3+60j: B gradient product; B+1 numerator/leakage load;
B+2..B+57 exactly56 divider iterations; B+58 signed quotient plus leakage;
B+59 saturation/conditional write. Frozen/disabled uses identical latency.
After E(63T+3) output valid asserts; first transfer is next edge.
No next transaction until this output transfers. Fork adds one storage stage
and independent destination acknowledgement state.

VAD last-bin acceptance E0 closes the sum; E1 decision/hangover; E2 event
and frame gate valid. Event payload remains stable until transfer.
Parent log is eight globally enabled stages: E0 input, E7 visible output,
E8 earliest transfer. End-to-end token timing is elastic, not fixed;
association, complete gating, progress and throughput are mandatory.
Do not invent shorter inherited FIR/SOS/SRC/spectrum/Mel latencies.

## Boundaries and implementation constraints

Exercise DELAY_DEPTH1 fractions0/255; depth17 wrapping; maximum k/f;
AEC_TAPS1; startup D regularization; signed full-scale; negative/RNE ties;
accumulator headroom; weight/coefficient saturation; zero far; disabled AEC;
reset during division. Acoustic frame size is fixed512: a one-sample partial
frame is legal, discardable only on reset/commit, never a complete frame.
Exercise commit while audio/event/last-token are stalled, a commit held across
partial-frame drain, simultaneous inherited/acoustic traffic, independent
destination stalls and inherited invalid-descriptor rejection.

All RTL must be modular and synthesizable, with zero combinational `/` or `%`
operators, no mixed blocking/nonblocking state assignments in sequential
blocks, bounded registered logic depth and no intentional latches, generated
clocks, floating-point datapaths or unresolved arithmetic black boxes.
Use explicit wraps and registered reductions. Preserve the divider iteration
boundary unless an independently admitted timing repair updates cycle docs
without changing numerical behavior. Large memories with byte enables require
explicit byte-lane unpacked arrays. Preserve efficient synchronous RAM
inference where applicable. No generic-synthesis timing waiver; a Coordinator
fallback requires actual post-synthesis Quartus STA and independent admission.

## Mandatory independent L1 acceptance

Every `tests/verify.py` check is self-contained and executes missing
prerequisites. Use Python standard-library models, real Icarus/vvp,
compiled Verilator and Yosys. No private path, credentials, local JTAG/PCIe
device, runtime-state or final-report prerequisite. Fail explicitly on
nonzero exit, timeout, X/Z in observed valid payloads, mismatch, missing
coverage or artifact. Expected results derive from public accepted inputs
and independent mathematics, never DUT traces. Small Icarus tests remain
under500000 cycles; medium/long adaptation, multi-frame, seeded and mutation
sweeps use compiled Verilator. Avoid per-cycle output and per-request fflush;
compare online or with bounded binary files and retain bounded failure windows.
Remove only known settled transient compiler intermediates.

### Compatibility

Independently exercise all five inherited chain modes, four channels, staging
completeness/swaps, history isolation, reset and randomized backpressure.
Document a covering matrix actually exercising every legal inherited parameter
value, not necessarily its Cartesian product. Execute all64 L/M pairs1..8
on the SRC leaf with actual output-count and exact-sample comparisons.
Check512-point zero/DC/impulse/bin-tone/multitone/seeded-random frames,
all257 bins/all64 bands, log endpoints/interpolation/clipping, and malformed
external-power frame discard/recovery through inherited top. Compare at least
one complete inherited FIR->SOS->SRC->spectrum->Mel->log frame through F5
against the independent oracle; historical acceptance is not a substitute.

### Acoustic seeds17,29,43

Run each seed separately, every AEC_TAPS1/8/16/32 per seed with bit-exact
audio/freeze, and every DELAY_DEPTH across the combined matrix.
Per tap-count/seed use16384 far-only training samples then4096 evaluation,
independent seeded broadband far in[-4000,4000]. T1 echo h0=1/2;
T>=8 h0=1/2,h3=1/4,h7=-1/8. Independently round echo and present coherently
to all eight zero-delay microphones. Require evaluation
ERLE=10*log10(sum(desired^2)/sum(error^2))>=20 dB for every case.
Report exactly zero residual separately, never hide it with epsilon.
Then1024 independent near-end samples amplitude<=12000 with far active:
>=95 percent frozen in final512, exact output and no weight/leakage changes
on frozen samples. Hierarchical weights are supplemental only.
After removing near-end use2048 recovery and4096 evaluation, ERLE>=18 dB.
Include reference-silence freeze and exact nonzero-weight leakage phases.

With AEC disabled, bit-exactly compare every beamformed sample at all
integer/fraction boundaries and randomized legal vectors. For each seed use
>=4096 post-warmup samples of coherent broadside speech-band tones plus
independent equal-power microphone noise; output SNR improvement>=7 dB
against mean input-channel SNR.

For ULA spacing0.04 m, sound speed343 m/s, sampling16 kHz, test look
angles+30/-30 degrees, desired1000 Hz, opposing-angle interferer2000 Hz.
Generate analytic plane waves before PCM quantization with delay
i*16000*0.04*sin(theta)/343. Steering compensation=max(delay)-delay_i,
RNE-quantized Q8, DELAY_DEPTH8. Require desired gain within1 dB of unity
and SIR improvement>=6 dB for both directions. Separate real DUT runs
measure desired/interferer contributions under identical configuration.

Exercise VAD exact ON/OFF/adjacent values, three-frame hangover, silence,
speech, converged far-only echo and double-talk. Per seed at least12
consecutive complete acoustic frames include both emission and suppression
with independent randomized audio/event/token stalls. Compare every audio
sample, event energy/decision and emitted64-token frame against oracle.
Include gate-disabled emission, AEC-disabled operation, epoch change,
mid-frame reset and simultaneous legacy traffic. Exactly one event per frame
and exactly predicted token count/order. Observe silence absence after
bounded drain, not by skipping comparison. Measure steady-state input
intervals with T32 and continuously ready sinks: >=16000 tuples/s at100 MHz.

### Robustness and defect sensitivity

Assert exact pre/post-edge beamformer/AEC/VAD/log timing and all boundary,
reset, invalid-parameter and configuration/backpressure interactions.
Every legal parameter must actually elaborate; every new parameter boundary
must functionally execute. Illegal examples: TAP_COUNT0/3, MAC_LANES0/3,
SOS_COUNT0/5, SRC_TAPS0/3, DELAY_DEPTH0/2, AEC_TAPS0/3.
Preserve compilation failures and route diagnostic guard repairs to Designer;
do not weaken explicit diagnostic expectations.

In disposable source copies inject and execute at least six specific defects:
fractional operand/fraction error; microphone omission; NLMS normalization
or gradient-sign error; removed double-talk protection; removed leakage;
VAD threshold/frame-gating error. Each must compile, execute and be rejected
by an unchanged numerical/coverage assertion. Compilation failure, timeout,
text search or a predeclared failure does not count. Add token-numerical
mutation if the six do not establish end-to-end token sensitivity.
Never alter production RTL or the oracle to cause mutant failure.

### Synthesis and portability

Real Yosys hierarchy elaboration and generic synthesis for default complete
F5, singleton and maximum new parameters: hierarchy-check, consistency,
zero latches/unresolved black boxes/division/modulo cells. Retain substantive
inherited/acoustic paths, adaptive state, divider, frame storage and token
engine; optimized-away or constant-output top fails. Record versions,
commands and resources without calling generic area Arria post-fit PPA.
Large netlists may be losslessly compressed with verified hashes/byte counts.

Create a fresh bounded directory containing only declared RTL, design docs,
tests and read-only parent RTL. There execute a real compact Icarus
acoustic/legacy smoke, compiled Verilator complete acoustic-frame oracle
comparison and Yosys zero-latch elaboration with fresh outputs and no original
binary/runtime access. Do not recursively invoke full portability suite.
Dependencies and root-relative reproduction commands are in ARCHITECTURE.

## Execution, evidence and delivery

Mandatory commands, unchanged and append-only if supplemental checks arise:

```
python3 tests/verify.py --suite compatibility
python3 tests/verify.py --suite acoustics --seed 17
python3 tests/verify.py --suite acoustics --seed 29
python3 tests/verify.py --suite acoustics --seed 43
python3 tests/verify.py --suite robustness
python3 tests/verify.py --suite defect-sensitivity
python3 tests/verify.py --suite synthesis
python3 tests/verify.py --suite portable
```

Inputs: `rtl/*.sv`, `design/*.md`, `tests/*`,
`billet_f4_reference/rtl/*.sv`. Each invocation creates a new exclusive
`evidence/l1-<suite>-<timestamp_or_hash>/`, including seed in acoustic stem.
Retain receipt.json with exact argv/cwd, source hashes/snapshots, tool versions,
start/end times, timeout/exit, raw stdout/stderr paths, coverage, metrics and
comparison results. Never overwrite occupied runs/failed receipts or reuse
stale success for changed sources. Keep outputs bounded using streaming
comparison, concise output and lossless compression where appropriate.
Necessary advisory static preparation uses a fresh
`evidence/preparation/<stem>-<timestamp_or_hash>/` with equivalent receipt;
Designer does not author functional/synthesis preparation.
Repair handoffs link exact failed and repair receipts.

L1.5 timing lint, L2 Quartus17.1 post-fit STA at100 MHz, and L3 Arria10
PCIe Gen3x8 bridge checks execute natively, not duplicate L1 commands.
Setup and hold WNS must be>=0, plus at least three consecutive deterministic
physical bridge passes. Distinguish bridge connectivity from DSP simulation
and any actual hardware DSP observations. All original checks remain mandatory;
an unchanged handoff does not repair a failing command or occupied output.

Deliver modular synthesizable RTL, this specification, architecture/interface/
timing/reproduction documentation, independently authored verification, and
the independently approved runtime-rendered PDF after engineering acceptance.
No credentials, account paths, private host paths or runtime transcripts in
shipped product sources. No current implementation correctness, functional
metrics, synthesis, timing or hardware acceptance follows from this contract.
