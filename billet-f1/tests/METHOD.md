# Independent verification method

Run the four project-relative commands in `README.md` from the delivered root.
Python uses only its standard library. Missing Python/Icarus/VVP/Yosys executables
are failures; these scripts never install dependencies. Child simulation and
synthesis deadlines are 3600 seconds. Stimulus is bounded to two million cycles
per simulation, with shorter protocol nonprogress bounds in simulated cycles.

Every invocation creates a new timestamped directory below its `--out` path.
Existing successes and failures are never overwritten. `result.json`,
`claim_to_evidence.json`, `ledger.json`, source snapshots/inventory, tool-version
receipts and raw stdout/stderr bind results to the exact inputs and commands.
Until execution completes, generated expected coverage is not observed coverage.
`coverage_observed.json` is written only after the entire public-pin trace agrees.
An exception exits nonzero; an absent result is not a passing run.

## Independent numerical and protocol references

`reference.py` convolves the accepted sample history and the last committed
coefficient array using arbitrary-precision Python integers. It does not inspect
any DUT internals, model a MAC tap counter or reproduce the RTL accumulation
sequence. `divmod(A,32768)` supplies the specified floor quotient/remainder;
the independent floating calculation uses `math.fsum((x/32768)*(c/32768))`.
Floating tolerance is `1e-12`, in addition to the half-Q1.15-code rounding bound.
Clipping error and total error against unclipped convolution are reported
separately from error against the saturated floating reference.

The protocol reference schedules only the externally specified acceptance,
availability and transfer edges. It models staging writes/commit transactions,
not physical banks. `bridge.sv` drives public pins, rejects X/Z, and records
every cycle immediately before the rising edge, in its Active region before
NBA updates, after NBA settling, and at the falling edge. All five outputs
are compared at all four phases. Reset starts at time zero without an earlier
high pulse; zero-valued reset outputs are also checked at time-zero delta
settling, the first rising edge at time 5 (Active region), after its updates
and at the falling edge. Reset epochs discard outstanding reference work.
Dedicated reset pulses also assert and release entirely between clock edges
while computation or a stalled output is live. Their post-release public pins
must already reflect cleared state before the next rising edge; clock-only
reset implementations cannot hide behind output reset gating. In `vectors.txt`,
the reset field 2 denotes this one-time-unit asynchronous pulse; 0 and 1 are
ordinary held reset levels.
Accepted equals transferred plus explicitly reset-aborted work after drainage.

Directed cases precede seeded traffic. All legal stalled producers hold
valid/payload; the deliberately invalid T=1 address is withdrawn because it
can never handshake. An incomplete commit request is withdrawn as explicitly
required by the programming protocol, while a simultaneously blocked legal
configuration request is held through its later acceptance.

Each full matrix cell requires at least 4096 completed comparisons, 1024
full-range random accepted samples, eight commits, 64 ready input bubbles,
64 valid-output stalled cycles and a 16-cycle contiguous output stall.
The required coverage assertions live in `Contract.summary`; labels on a
sample count only when its output completes and its public trace subsequently
passes comparison. Control-event counters require the applicable modeled
public handshake/precondition and are admitted only after trace comparison.
The always-ready window checks 72 consecutive outputs, records acceptance,
availability and transfer cycles, and asserts T/T+1/T+1 latency/transfer/II.

All four sign/floor-parity classes of exact ties are reachable even at T=1:
coefficient 16384 with samples 1,3,-1,-3. Coefficients 16383/16385 check
adjacent-to-half remainders. For T=1 negative saturation is unreachable:
the minimum product is -32768*32767, whose rounded quotient is -32767.
Positive saturation is reachable at -32768*-32768. A nonempty incomplete
bitmap is also unreachable with only one address: the singleton tests reset
both an empty incomplete staging bank and a completely written staging bank.
For T>1 reset additionally interrupts a nonempty incomplete bank and an
interior MAC edge. These unreachable cases are disclosed rather than counted.

## Banks, tones and retained data

`data/generation.json` describes the bank and extra-boundary rules;
`bank_document()` constructs their exact ideal rational values independently
of RTL. Each behavior run publishes fully enumerated signed codes and SHA256
hashes (compact JSON array encoding) in `coefficient_banks.json`.
All extra directed coefficients and exact per-cycle samples remain in
`vectors.txt`; each cell also publishes every committed bank, commit edge,
reset epoch and bank hash, including its seeded full-range random bank.
`reference_outputs.jsonl` includes the sample, reset epoch, coefficient generation,
accept/availability cycles, integer accumulator/output and floating references;
`actual.txt` retains the independently executed public output observations.
These files permit direct reproduction of all measured traces.

Moving-average and asymmetric positive-triangular banks each receive 512
scored steady-state samples at frequencies 1/64,3/64,7/64,15/64, after T
unscored warmup samples. Input cosine amplitude is 24000 codes, quantized
ties-to-even. An additional independent floating sine/cosine convolution
measures complex response over 512 samples and compares it to the analytical
response within `1e-10`. `frequency_response.json` publishes both responses
and ideal-versus-quantized errors. For every frequency, the analytical response
error is bounded by `sum(abs(ideal[k]-code[k]/32768))`, computed exactly with
rationals before conversion to floating point. This includes clamping at
positive unity; it is not an invented filter-quality threshold.

## Sensitivity, synthesis and portability

Each defect has a separate RTL copy, retained unified patch/hash inventory and
unchanged witness/oracle. The witness first passes on accepted RTL, then the
mutant must compile and execute successfully and have its **first** discrepancy
be an observed output-value mismatch. Tap order uses asymmetric coefficients;
rounding uses a reachable odd-quotient tie; coefficient corruption writes the
last active tap during a computation with populated history. A compile error,
protocol-only discrepancy or timeout does not kill a mutant. Accepted design
and verification source hashes are rechecked before finishing.

Yosys retains hierarchy JSON before synthesis, full scripts/logs, mapped
netlists and generic statistics. Structural assertions inspect the literal
three instances, one signed multiplier (including expression-context sign
extension), full-width addition and state. Hierarchical connection traversal
requires every sample/configuration data bit to reach the output datapath
through actual cell connections. Both pre- and post-synthesis checks reject
latches, including internal latch forms, and Yosys `check -assert` and
`hierarchy -check` reject structural/unresolved-cell errors. Default T=32 is
synthesized without overriding its parameter; the other four are overridden.
Source lines/modules and generic cells/wires/state bits are actual inventories,
not physical area, timing closure or FPGA measurements.

`reproduce` copies only declared RTL, design documents, README and verification
sources/fixtures. It executes new T=1/T=32 seed-17 partial behavior (reduced
random/tone counts, all directed numerical/control checks and measured timing),
all three defect witnesses and default-T synthesis, with fresh binaries and
netlists. Results explicitly identify partial scope; they do not replace the
full 15-cell matrix. No previous binaries, results, preparation evidence,
runtime metadata, account directory or eventual product PDF are inputs.

`python3 tests/preparation.py` is an optional bounded harness diagnostic,
not an acceptance command. It retains a new advisory receipt and snapshots
under `evidence/preparation/`, including failures; `--repair-of` links an
earlier receipt without overwriting it. The shared runtime executes the
admitted full commands after handoff. Only its actual outcomes and independent
review establish engineering acceptance.
