<!-- Copyright 2026 Christopher Wright -->

# Example × Backend support matrix

HALucinator ships six emulation backends. Every example harness now selects
the backend via the `HAL_EMULATOR` environment variable (default `avatar2`),
so any example can be pointed at any backend without editing it:

```bash
HAL_EMULATOR=qemu bash test/multi_arch/arm32/run_tests.bash
```

Backends: `unicorn` (in-process), `avatar2` (QEMU via avatar2), `qemu`
(direct QEMU over GDB-RSP+QMP), `renode`, `ghidra`, `libafl-qemu`
(`QEMUBackend` subclass on the libafl-qemu-bridge build).

This matrix was **measured 2026-06-27** on the `halucinator:bpv5-updated`
container image, running this branch on the current `upstream/master`. The
image's avatar-qemu fork is built for **arm, aarch64, mips, ppc, ppc64**;
libafl-qemu is built for **arm only**; there is **no i386** build. Each cell is
one isolated `test/<example>/run_tests.bash` run via
`test/tools/backend_matrix.sh`, with a 300 s timeout and a clean teardown (kill
stale QEMU/gdb + wait for the GDB port to free) between runs.

Legend: ✅ pass · ⏱ timeout (300 s, no completion) · ❌ fail (nonzero exit).

## multi_arch — UART end-to-end (the portable example suite)

| arch  | unicorn | avatar2 | qemu | renode | ghidra | libafl-qemu |
|-------|:-------:|:-------:|:----:|:------:|:------:|:-----------:|
| arm32 |   ✅    |   ✅    |  ✅  |   ✅   |   ✅   |     ✅      |
| arm64 |   ✅    |   ✅    |  ✅  |   ✅   |   ✅   |     ⏱¹     |
| mips  |   ✅    |   ✅    |  ✅  |   ⏱²   |   ✅   |     ⏱¹     |
| ppc   |   ✅    |   ✅    |  ✅  |   ✅   |   ✅   |     ⏱¹     |
| ppc64 |   ⏱³    |   ✅    |  ⏱⁴  |   ✅   |   ✅   |     ⏱¹     |

**23 / 30 pass.** The core backends — `unicorn`, `avatar2`, `qemu` (direct), and
`ghidra` — pass on **arm32, arm64, mips, ppc**. `renode` passes everywhere except
mips. `libafl` passes on its only built arch (arm32). The remaining cells are
environment/known gaps, not regressions:

1. **libafl-qemu (non-arm)**: only the ARM `qemu-system-arm` libafl-bridge binary
   is built in this image, so arm64/mips/ppc/ppc64 have no libafl binary — the
   harness never gets a banner and hits the 300 s timeout. Build
   `HALUCINATOR_QEMU_LIBAFL_<ARCH>` to enable them. (arm32 passes.)
2. **renode / mips**: renode's MIPS platform never reaches the UART banner —
   a renode MIPS-platform gap, independent of this branch.
3. **unicorn / ppc64**: in-process unicorn never produces UART output for ppc64 —
   a unicorn PPC64 coverage gap.
4. **qemu / ppc64**: the avatar-qemu **ppc64 gdbstub stalls/closes** on the
   `P`→`G` register-write fallback used to set the entry PC during init, so
   execution never progresses (300 s timeout). avatar2 avoids this by driving the
   real `gdb-multiarch` binary (its ppc64 cell passes). A narrow ppc64-only fix in
   `_GDBClient` register writes is tracked as a follow-up.

**Before this branch**, every non-ARM `multi_arch` arch *failed on
avatar2/qemu/libafl* because the harnesses pass `HALUCINATOR_QEMU_<ARCH>=""`
(empty when unset) and `hal_config.get_qemu_path` treated empty as an invalid
explicit path. That is fixed here (empty → use the default built-QEMU path),
which is what turns the avatar2/qemu/ghidra columns above green.

## Other examples

- **test/zephyr/**, **test/firmware-rehosting/p2im-drone**: their `run.sh`
  harnesses are parametrized for `HAL_EMULATOR` on this branch (default
  `avatar2`), so they accept any backend without edits.

> The experimental per-arch **interrupt-delivery** suite (`test/multi_arch_irq`)
> is a separate, incomplete framework effort and is **not part of this branch**.

## Reproducing the matrix

The runner used (clean teardown + GDB-port wait per run) lives in
`test/tools/backend_matrix.sh`:

```bash
# in the container, with backends installed
bash test/tools/backend_matrix.sh

# or a single family/backend subset:
EXAMPLES="multi_arch/arm32 multi_arch/mips" \
  BACKENDS="qemu avatar2" bash test/tools/backend_matrix.sh
```
