# Portable snapshots silently zero MSP/PSP on unprivileged Cortex-M

**Status:** **FIXED** — `_capture_portable_regs`/`_restore_portable_regs` now
read/write the banked MSP/PSP under handler-mode privilege (see *Suggested fix*
below, which is what was implemented; regression test
`test_portable_snapshot_preserves_banked_sp_when_unprivileged`). The **second,
smaller gap** (`PeripheralRegistry` scalar attributes, § at the end) is **still
open** — left as a separate change because widening the generic copy touches
every peripheral model. Originally filed open/unfixed on `dev` as of `ee267d8`.
**Severity:** high — silent corruption, fails hundreds of ms after the operation that caused it
**Affects:** `save_state(portable=True)` / `--snapshot-at` / `--restore`, unicorn backend, ARM profile-M
**Found:** 2026-08-05, while rehosting a FreeRTOS-Plus-TCP `ARM_CM4_MPU` image to M4

> Filed here as a file rather than a GitHub issue because **issues are disabled** on both
> `rehostry/halucinator` and `cwright7101/halucinator`. Move this to the tracker if issues are
> ever enabled.

## Summary

`UnicornBackend._capture_portable_regs()` enumerates the M-profile system registers with a plain
`uc.reg_read`:

```python
# src/halucinator/backends/unicorn_backend.py:2115
elif self._is_arm_profile_m():
    sysregs: Dict[str, int] = {}
    for suffix in self._M_PROFILE_SYSREGS:        # ("MSP","PSP","PRIMASK","BASEPRI","FAULTMASK","CONTROL")
        rid = getattr(arm_const, f"UC_ARM_REG_{suffix}", None)
        ...
        sysregs[suffix.lower()] = uc.reg_read(rid)
```

**QEMU's MRS/MSR special-register helpers return 0 for MSP and PSP when the core is
unprivileged.** So any image that drops privilege checkpoints `MSP = PSP = 0`.

That is not an edge case. It is **every MPU-hardened Cortex-M image**: the FreeRTOS
`ARM_CM4_MPU` port sets `CONTROL = 3` in `prvRestoreContextOfFirstTask`, so all task context runs
unprivileged.

## Why it is nasty

The restore path *is* privileged, so the zeros are written back and **stick**. Nothing fails at
restore time — the log cheerfully reports:

```
Restored snapshot ...; resuming at pc=...
```

and the task resumes correctly on PSP. The machine dies at the **next exception**, which switches
to MSP and pushes its frame at address 0:

```
UC_ERR_WRITE_UNMAPPED at PC=0x0800ab34  lr=0xfffffffc
```

— the `push {r3, lr}` at the top of `prvSVCHandler`, on the first FreeRTOS system call after the
restore. The failure is hundreds of milliseconds and an unrelated-looking stack frame away from
its cause. Diagnosing this as a firmware or config problem costs hours; it looks exactly like a
bad memory map or a broken vector table.

## Reproduce

1. Any Cortex-M image that sets `CONTROL.nPRIV` (any FreeRTOS MPU port will do).
2. `--snapshot-at <addr past the point privilege drops>`.
3. `--restore <snap>`.
4. Let the guest take any exception — an SVC or a SysTick is enough.
5. Observe the unmapped write at 0 from the exception-entry push.

Inspect the snapshot: `m_sysregs.msp` and `m_sysregs.psp` are both `0`.

## Suggested fix

Do the IPSR trick inside `_capture_portable_regs` / `_restore_portable_regs` for profile-M —
temporarily set IPSR non-zero (which is privileged) to read/write the true banked SPs, exactly as
the backend's own exception-entry path already does at `unicorn_backend.py:2980`:

```python
self._uc.reg_write(_A.UC_ARM_REG_IPSR, exc_num)    # -> handler mode (MSP)
```

The machinery is already present in this file; it just is not applied on the snapshot path.

A working reference implementation exists in the study harness
(`hal_freertos_net/netdev.py`: `_with_handler_mode` / `_save_bank_shadow` /
`maybe_restore_bank_shadow`), which sidecars the true SPs as `<snap>.bank.json` and writes them
back after `--restore`. That workaround should not be necessary — it belongs in the core.

## Second, smaller gap found alongside it

`PeripheralRegistry`'s generic strategy deep-copies only **mutable container** attributes of a live
bp handler. Consequences:

- plain `int` / `bool` fields are silently dropped, so a restored handler comes back with its
  counters at zero;
- any `set` / `dict` / `list` on a handler is silently **overwritten** by the checkpoint's copy —
  this broke an exit-on-marker `set` until it was moved to module level.

Handlers that carry state should be required to declare `save_state` / `restore_state` rather than
relying on the generic copy, or the generic copy should cover scalars too.
