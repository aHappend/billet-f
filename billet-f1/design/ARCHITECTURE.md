# BILLET-F1 architecture

## Organization

The implementation uses one signed 16x16 multiplier, a full-width sequential
accumulator, two coefficient register banks, a shifted sample-history array,
and one registered output slot. Computations do not overlap. This is a
resource-sharing choice, not a measured physical-area claim.

```text
billet_f1_top
  g_supported.u_coeff_bank : billet_f1_coeff_bank
  g_supported.u_mac        : billet_f1_mac
  g_supported.u_round_sat  : billet_f1_round_sat
```

The legal-parameter generate branch is `g_supported`. The alternative branch
emits an explicit parameter diagnostic and ties public outputs to zero; it
does not instantiate a malformed datapath for unsupported tap counts. Derived
address widths remain at least one even for diagnostic-only configurations.
All four module definitions reside in correspondingly named `rtl/*.sv` files.

## Ownership and interfaces

| Module | Owned state/function | Internal interface |
| --- | --- | --- |
| `billet_f1_top` | Public arbitration and output valid/data registers | Generates accepted `start`, `cfg_write` and `commit` pulses; connects tap address/coefficient and final sum/rounded data |
| `billet_f1_coeff_bank` | Two T-by-16 banks, active-bank bit, T written bits | `cfg_write`, `cfg_addr`, `cfg_data`; `commit`; `staging_complete`; asynchronous `read_addr`/signed `read_data` |
| `billet_f1_mac` | T-by-16 history, tap counter, full-width accumulator and busy | `start`, signed `sample_data`; `coeff_addr`/signed `coeff_data`; `busy`, combinational `final_valid` and signed `final_sum` |
| `billet_f1_round_sat` | Stateless ties-to-even rounding and saturation | Signed `accumulator` to signed 16-bit `result` |

Internal pulse inputs are already qualified by the top-level public handshake.
They are not independently queued. Child modules are implementation components;
the reusable public protocol and legal-parameter diagnostic belong to the top.

## Schedule and state lifetimes

At acceptance E0 the MAC shifts history from high to low index and inserts
the newest sample at index zero. It clears the accumulator and tap index,
then sets busy. Because nonblocking history updates settle after E0, tap zero
reads the newly accepted sample in time for E1.

On each busy edge, a signed 32-bit product is sign-extended to `ACC_W` and
added without truncation to the accumulator. The tap counter selects both
history and active coefficient. On the last tap, `final_sum` is the current
accumulator plus the current product, not the previous partial sum.
`final_valid` is combinationally true during that last busy cycle. The top
registers its rounded value on that same final edge E_T. There is no additional
registered completion or rounding stage.

Completion clears busy. At E_(T+1), output retirement and next acceptance can
coincide. If retirement is blocked, the registered slot remains stable and
no new computation starts. The top gives completion priority over retirement;
the acceptance schedule guarantees the slot was empty when completion occurs.
See the pre-edge/post-edge table in `SPEC.md`, including T=1.

| Resource | Producer | Lifetime / release |
| --- | --- | --- |
| History | Accepted samples only | Retained across commits; cleared by reset |
| Active coefficient bank | Successful commit | Read-only until idle/empty successful commit swaps ownership |
| Staging coefficient bank | Accepted configuration writes | May be written during computation or output stalls; becomes active on commit |
| Completeness bitmap | Accepted writes | Cleared on every successful commit and reset |
| Accumulator / tap counter | MAC | E0 through E_T; cleared on completion or reset |
| Output slot | Completed MAC via rounding | E_T through handshake, or reset abort |

## Arithmetic construction

Signed 16-bit operands feed a signed 32-bit product. The product is explicitly
sign-extended to exactly `32+$clog2(T)` bits before addition. This width
contains every possible T-product sum, including all-negative operands
producing positive products; no partial saturation is permitted.

The rounding module sign-extends the accumulator by one bit and arithmetically
shifts right by 15 to obtain the floor quotient. The original low 15 bits
encode the nonnegative remainder even for negative two's-complement values.
An increment is applied for a remainder above 16384 or an exact tie with odd
quotient. Comparisons against signed -32768 and 32767 follow the increment,
then only an in-range value is narrowed to 16 bits.

The extra quotient representation prevents rounding overflow; it is not an
extra accumulator bit or an intermediate precision change.

## Atomicity and dependency guarantees

| Consumer assumption | Provider guarantee |
| --- | --- |
| MAC history does not change while busy | Top accepts input only when idle; MAC shifts only on `start` |
| MAC coefficients do not change during an output | Bank writes only inactive storage; top commits only when idle and output-empty |
| Every new active bank is fully specified | Bank bitmap requires every address written since the previous commit/reset |
| Top final sum includes the last product | MAC exposes combinational last-tap sum before E_T |
| Completion cannot overwrite a stalled output | Top starts only with an empty slot or simultaneous retirement, then blocks further starts while busy |
| Public outputs are zero in reset | Top gates combinational public outputs by `rst_n`; every stateful module also resets asynchronously |
| No stale result survives reset | History, accumulator, sequencing, banks, bitmap and output slot all reset |

Configuration/commit arbitration blocks writes whenever `commit_valid` is
asserted, regardless of readiness. Commit also blocks sample acceptance.
Commit readiness deliberately ignores `m_ready`: a valid output must retire
before, not on, the commit edge. No coefficient generation tag is necessary
because the ownership and drain rules prohibit overlap.

## Storage and implementation limitations

Logical payload storage is 16*T history bits plus 32*T coefficient bits,
T completeness bits and one active-bank bit. Sequencing adds `ADDR_W` tap
bits, `ACC_W` accumulator bits and busy; the output slot adds 16 data bits
and valid. These are source-level inventories, not synthesized statistics.
Register arrays have asynchronous reads and asynchronous reset; no SRAM,
block-RAM mapping or target-specific primitive is promised.

The design has one clock domain. Integration must provide a suitable clock,
honor ready/valid stability, and manage reset release for its target system.
No CDC, physical timing closure, device-specific timing constraint or hardware
measurement is claimed. Functional and generic-synthesis conclusions remain
pending independent verification.
