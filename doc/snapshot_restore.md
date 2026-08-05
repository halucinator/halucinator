# State snapshot / restore

Save the complete state of an emulation — guest CPU + RAM, host-side
peripheral-model state — and restore it later: milliseconds later in the same
process (in-memory checkpoints, boot-once-experiment-forever), or days later in
a different process (persistent checkpoints that survive crashes and reboots).

Motivating workflows:

* **Deep-gate iteration.** Rehosting a firmware whose interesting behavior is
  minutes of boot away (large RTOS images) means every model tweak
  costs a full re-boot. Snapshot once at a marker, then restore-and-experiment
  in ~1–2 s per attempt. (`diagnostics/snapshot_lab.py` prototyped this
  in-process; this feature is its generalized, supported form.)
* **Fast resume.** A harness restores a known-good checkpoint before
  every input. This needs the fast in-memory path (`snapshot_is_fast()`).
* **Surviving process death.** A snapshot persisted to disk restores into a
  freshly started emulator with the same config — work is never lost to a
  crash, reboot, or closed session.

## Architecture: three layers, one coordinator

Machine state is spread across three places; each layer owns
`save_state()`/`restore_state()` for its slice, and a coordinator composes
them:

| Layer | State | Where implemented |
|---|---|---|
| 1 | Guest CPU + RAM (+ device state the emulator models natively) | `HalBackend.save_state` (generic fallback), specialized per backend (`UnicornBackend` native fast path) |
| 2 | Python host-side peripheral-model state: uart rx buffers, gpio pins, interrupt maps, cached bp-handler instances, `hal_stats` | `snapshot/peripheral_registry.py` |
| 3 | External device processes (zmq peripheral server clients) | `snapshot/device_layer.py` (HAL side) + `external_devices/snapshotable.py` (device side) |

The coordinator (`snapshot/system_snapshot.py`) bundles the layers into a
`SystemSnapshot` and restores them together (backend, then peripherals, then
devices), reporting per-layer failure via `RestoreResult`.

### Layer 3 — external devices

zmq PUB/SUB has **no membership** — HALucinator cannot enumerate connected
device processes. Layer 3 is therefore a cooperative, opt-in protocol with a
collection window (`DeviceLayer(timeout=...)`): on save, HAL broadcasts
`Peripheral.SnapshotDevices.save` and whoever answers within the window is in
the snapshot; on restore, every device *captured in the snapshot* must ack
within the window or the restore fails (all-or-nothing — the machine cannot
come back whole without them). Stateless devices (display-only terminals,
bridges whose live sockets can't be checkpointed anyway) simply don't
participate. Device authors opt in with
`external_devices.snapshotable.SnapshotableDevice(ioserver, device_id,
get_state, set_state)`; `device_id` must be stable across restarts.

Layer 3 is opt-in per call (`system_snapshot(..., device_layer=DeviceLayer())`)
because the window costs a full timeout on every save — fine for disk
checkpoints, wrong for per-input restores.

### Layer 1 — backend guest state

* **Generic fallback** (`HalBackend.save_state`): enumerate `list_registers()`
  + read every writable, non-`emulate` `MemoryRegion`. Works on any backend
  that exposes the read/write primitives. Registers are captured tolerantly (a
  name a given stub can't read/write is skipped with a warning, since register
  sets vary across GDB stubs); memory is strict (a failed region read raises).
  `emulate=`-backed MMIO regions are skipped — they are Layer-2 state, not RAM.
* **Unicorn native** (`UnicornBackend.save_state`): `uc.context_save()` plus a
  bulk copy of every mapped page. A handful of milliseconds; byte-identical
  round-trip; `snapshot_is_fast() == True`.
* **Portable form** (`save_state(portable=True)`): plain-python capture for
  disk persistence — see “Portability” below.

**Per-backend Layer-1 support:**

| Backend | Path | Fidelity | Fast | Verified |
|---|---|---|---|---|
| Unicorn | native + portable | full (CPU incl. banked/CP15/VFP + RAM) | ✅ | unit + e2e (local) |
| Ghidra | generic reg+RAM | CPU regs + RAM | — | live round-trip (Docker) |
| QEMU | generic reg+RAM | CPU regs + RAM (guest); QEMU-internal core devices not captured | — | live round-trip (Docker) |
| LibAFL-QEMU | generic reg+RAM (via QEMUBackend) | same as QEMU | — | live round-trip (Docker) |
| Renode | generic reg+RAM | CPU regs + RAM (register writes need `start`) | — | live round-trip, memory (Docker) |
| Avatar2 | generic reg+RAM | CPU regs + RAM (guest) | — | live round-trip (Docker) |

All six backends' live round-trips pass inside the CI Docker image (real
qemu-system-arm / libafl-qemu / renode / ghidra). Note: QEMU's GDB-RSP memory
transfer is chunked (a whole-region read exceeds the stub's packet size and
returns E22 otherwise).

For the QEMU/Renode families the generic path captures the guest CPU + RAM;
peripheral state lives in Layer 2 (Python models). Emulator-*internal* core
devices that aren't forwarded to Python (e.g. an in-QEMU interrupt controller)
are not captured by the generic path — a native `savevm`/`Save` override is a
possible future enhancement (see roadmap).

### Layer 2 — peripheral registry

`PeripheralRegistry` discovers every Layer-2 state holder live at snapshot
time (nothing hard-coded): all `@peripheral_model` classes, every cached
bp-handler instance (`intercepts.initalized_classes`), and the global
`hal_stats.stats`. Two capture strategies:

1. **Explicit** `save_state()`/`restore_state()` on the model (uart, gpio,
   interrupts) — knows exactly which fields are state; restores IN PLACE so
   external aliases stay valid.
2. **Generic deep-copy** for everything else — every mutable container
   attribute is deep-copied and restored in place (`SnapshotableModel` mixin
   exposes this as an opt-in default).

## Failure contract

The contract everywhere is *whole or nothing*:

* `save_state` **raises `SnapshotError`** rather than returning a partial
  snapshot — possessing a `Snapshot`/`SystemSnapshot` means it is complete.
* `restore_state` **validates before mutating** (`backend_type` +
  `SNAPSHOT_VERSION`) and returns `False` with the guest untouched on
  mismatch. A bad snapshot can never half-corrupt a machine.
* `system_restore` stops at the first layer that refuses and names it in
  `RestoreResult.layer` — the caller gets a clear “inconsistent, re-init”
  signal instead of a silently half-restored machine.
* Disk writes are atomic (temp file + `os.replace`) — a failed save never
  leaves a partial file.

Take snapshots with the guest **stopped** so the layers are mutually
consistent.

**Layer-3 restore is atomic in *reporting*, not in *effect*.** Backend and
peripheral (Layers 1–2) restore is truly all-or-nothing — nothing external is
touched until every in-process layer succeeds. But external devices (Layer 3)
mutate their *own* real state when they apply a restore, and there is no
cross-device rollback: if device A acks ok and device B then fails, A has
already moved. `system_restore` reports the failure (`layer="devices"`) so the
caller knows the fleet is inconsistent and must **re-drive the restore** (it
is idempotent — re-applying the same states heals A and retries B) or re-init.
Order is chosen to minimize this window: the fragile external layer restores
**last**, after the in-process layers have already succeeded. Building true
two-phase prepare/commit across arbitrary device processes is out of scope.

## Portability: the process-local-context discovery

The fast unicorn path stores a native `UcContext`. Empirical finding
(2026-07-16, unicorn 2.1.4): a `UcContext` **pickles without complaint and
then SIGBUSes when restored in a different process** — same unicorn build,
same CPU model, same memory map. The blob embeds process-local pointers.
(Same-process pickle round-trips work, which makes this a nasty silent trap —
hence `save_snapshot_file` hard-rejects native contexts.)

Disk persistence therefore requires the **portable form**, which enumerates
architectural state into plain python values: the general register file, plus
the per-architecture system state — A-profile ARM banked registers, a curated
CP15 set and VFP/NEON; M-profile ARM system registers (MSP/PSP/PRIMASK/
BASEPRI/FAULTMASK/CONTROL) and VFP. Other architectures capture the general
register file only; see `_capture_portable_regs` in
`backends/unicorn_backend.py` for the exact per-arch inventory.

Every system register read/write is individually guarded, so a register a
given CPU model doesn't implement is simply skipped (absent from the
snapshot, skipped on restore) — the same portable capture works from ARM926
up to cortex-a15 without per-model branching.

**Machine fingerprint.** A portable snapshot records the arch, CPU model, and
mapped memory layout it was taken on. `restore_state` compares this against
the live backend **before any write** and refuses a mismatch — so restoring a
snapshot onto a different machine config fails cleanly instead of half-writing
memory that doesn't line up.

## Disk format

See the module docstring of `snapshot/persist.py`, which defines it:
`save_snapshot_file(payload, path)` / `load_snapshot_file(path) -> (payload,
header)`, one gzip stream over a pickled `(header, payload)` tuple, header
validated before the payload is returned, atomic write via `os.replace`.

## Usage

```python
from halucinator.snapshot import (
    system_snapshot, system_restore,
    save_snapshot_file, load_snapshot_file,
)

# In-memory checkpoint (fast path — iterative / experiment loops):
snap = system_snapshot(backend)                    # guest stopped
... run, mutate, crash ...
result = system_restore(backend, snap)
assert result.ok, result.message

# Persistent checkpoint (survives the process):
snap = system_snapshot(backend, portable=True)
save_snapshot_file(snap, "boot.halsnap")

# ... later, in a NEW process started with the SAME config (same memory
# regions mapped, same handlers installed -- otherwise the machine
# fingerprint check refuses the restore):
snap, header = load_snapshot_file("boot.halsnap")
result = system_restore(backend, snap)
```

CLI (in-process unicorn backend):

```bash
# boot once to main, checkpoint, exit:
halucinator -c machine.yaml -c addrs.yaml --emulator unicorn \
    --snapshot-at main --snapshot-out boot.halsnap
# any number of later runs skip the boot entirely:
halucinator -c machine.yaml -c addrs.yaml --emulator unicorn \
    --restore boot.halsnap
```

MCP session tools: `save_snapshot` (no path → fast in-memory snapshot_id,
path → portable .halsnap), `restore_snapshot(snapshot_id|path)`,
`list_snapshots`, `delete_snapshot`. Take checkpoints while stopped; a
restore also revives a session whose firmware had run off the rails.

## Tests

`test/pytest/snapshot/` covers the backend, registry, coordinator and
persistence layers, including a fresh-process round-trip:

```bash
PYTHONPATH=src:test/pytest/helpers python3 -m pytest test/pytest/snapshot/
```
