<!-- Copyright 2026 Christopher Wright -->

# `halucinator.diagnostics` — re-hosting diagnostic harnesses

Reusable, **config-agnostic, address-parameterized** harnesses for diagnosing a
firmware re-host: where is it stuck, who drives a hot loop, which MMIO register is
polled, is the MMU page table installed, and a snapshot lab for fast model
iteration. Nothing here is firmware-specific — every address / window / marker is
supplied via env, so the same tool works on any target.

Run any harness as a module:

```bash
python -m halucinator.diagnostics.<name>      # e.g. probe_at_pc
```

## First: use the built-in backend diagnostics where they cover it

The unicorn backend (`halucinator/backends/unicorn_backend.py`) already implements
a lot via env-vars — prefer these when they fit:

| env var | covers |
|---|---|
| `HAL_PC_SAMPLE=1` (+`HAL_PC_SAMPLE_EVERY=N`) | global hot-PC histogram ("where is it stuck") |
| `HAL_CALL_TRACE=<path>` (+`_MAX`) | `bl`/indirect call-edge graph |
| `HAL_STEP_TRACE='0xLO-0xHI[:path]'` | single-step PC+sp+r0-r3+sl+ip+lr over a range |
| `HAL_WATCH_WRITE='0xA,..'` | who writes a field |
| `HAL_PIN_REGS='0xA=0xV,..'` (+`_PC_LO/HI`,`_ARM_PC`) | pin a RAM "ready" flag nothing sets |
| `HAL_TRACK_READS=1` (+`HAL_BSS_START/END`) | first read of an uninit .bss global |
| `HAL_SP_WATCH=1` | stack-pointer warps ≥64MB |
| `HAL_MAP_UNMAPPED=1` | lazily zero-map stray ld/st gaps |
| `HAL_AUTO_COUNTER_ADDRS='0xA,..'` (+`_STEP`) | make a free-running-counter MMIO reg monotonic |
| `HAL_MMU_FLAT_FALLBACK=1`, `HAL_IRQ_CHUNK=N`, `HAL_BREAK_RAM_SPINS=1`, `HAL_RECOVER_BAD_CALLS=1`, `HAL_ARM_CPU_MODEL=` | operational knobs |

These harnesses cover what those env-vars **don't**.

## Common env (all harnesses, via `_common.py`)

`HAL_DIAG_CFGS` (comma-separated `-c` configs, **required**), `HAL_DIAG_SYMS`
(symbol csv), `HAL_DIAG_SECS` (watchdog, default 180), `HAL_DIAG_ARGS` (extra
argv). Set `HAL_MMU_FLAT_FALLBACK=1` / `HAL_IRQ_CHUNK=8000` yourself as needed.
Output goes to stderr; resolve printed addresses with your symbol csv.

## The harnesses

| module | captures | when |
|---|---|---|
| `probe_at_pc` | registers + pointer-deref chains at PCs (one/N-shot); `obj→vtable→slot` walks | "what args/pointers enter this instruction?", "which method does this C++ indirect call dispatch to?" |
| `caller_histogram` | histogram/sequence of the caller `lr` at a function entry (+LR gate) | "who keeps calling this blocking/hot function?" — loop-driver finder (inverse of `HAL_CALL_TRACE`) |
| `mmio_region_sampler` | hot-block **+** most-polled-MMIO histograms, multi-window | "within this subsystem, which block spins, and which device register is it polling?" |
| `mmu_fault_introspect` | ARM CP15 TTBR0 + L1 descriptor at a fault PC; operand regs on the first unmapped abort | "is the page table installed?" — the Unicorn ARMv5 MMU/TTBR gate (fix is usually `HAL_MMU_FLAT_FALLBACK=1`) |
| `snapshot_lab` | boot once → `context_save` + all RAM → restore-and-experiment in ~seconds each | iterating a deep gate without re-booting; needs a `LAB_SPEC` module |
| `modbus_probe` | a stdlib Modbus/TCP client: connect, send one request, print the reply | round-trip a request through a re-hosted Modbus server (e.g. via a socket bridge) |

Each module's docstring has its exact params + an example.

## Examples

```bash
# Who drives a hot function (loop-driver finder):
HAL_DIAG_CFGS=cfg.yaml HAL_DIAG_SYMS=syms.csv TARGET_PC=0x20054d80 MODE=hist \
  python -m halucinator.diagnostics.caller_histogram

# Resolve a C++ vtable dispatch at a call site:
HAL_DIAG_CFGS=cfg.yaml PROBE_PCS=0x2001cc14 REGS=r0,lr DEREF='r0 * +8 *' \
  python -m halucinator.diagnostics.probe_at_pc

# Hot block + the MMIO register polled in that spin:
HAL_DIAG_CFGS=cfg.yaml BLOCK_REGIONS=0x201b0000-0x201d0000 \
  MMIO_REGIONS=0xfffb0000-0xfffc0000 python -m halucinator.diagnostics.mmio_region_sampler

# MMU gate introspection at a faulting page-walk instruction:
HAL_DIAG_CFGS=cfg.yaml FAULT_PC=0x201748f4 VA_REG=r9 \
  python -m halucinator.diagnostics.mmu_fault_introspect

# Probe a re-hosted Modbus server:
python -m halucinator.diagnostics.modbus_probe --host 127.0.0.1 --port 502 --fc 0x2b --data 0e0101
```

## `snapshot_lab` — the `LAB_SPEC` contract

`snapshot_lab` boots once to `BOOT_MARKER`, snapshots CPU+RAM, then restore-and-runs
each experiment (~seconds each instead of a full re-boot). Point it at a small spec
module:

```bash
HAL_DIAG_CFGS=cfg.yaml HAL_DIAG_SYMS=syms.csv LAB_SPEC=my_lab_spec.py \
  python -m halucinator.diagnostics.snapshot_lab
```

The spec defines `BOOT_MARKER`, `SUCCESS_MARKERS` (`{addr:name}`), `INTERMEDIATE`
(`{addr:name}`), `HANG_RANGE`/`HANG_CHUNKS`, and `EXPERIMENTS`
(`[(name, install_fn)]`, where `install_fn(backend)` adds the unicorn hooks for
that experiment). You supply a concrete spec for your target's deep gate
(markers + experiments), keyed to addresses you resolve from your symbol csv.

> A worked re-host that exercises several of these harnesses lives in
> `test/firmware-rehosting/arm-vxworks-plc/` (the ARM/VxWorks PLC example).
