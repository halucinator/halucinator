# Copyright 2026 Christopher Wright

"""snapshot_lab.py -- boot ONCE, snapshot, then restore-and-experiment in seconds.

The iteration-speed multiplier for deep gates: instead of re-booting (tens of
seconds) for every model tweak, boot once to a chosen marker, snapshot the full
unicorn CPU context + all RAM, then for each experiment RESTORE the snapshot,
install that experiment's hook bundle, and run a bounded IRQ-draining chunk loop
until a success marker / hang / off-rails / timeout -- ~1-2 s per experiment.

This is the GENERALIZED form of a per-firmware experiment lab. All addresses are
supplied by a small EXPERIMENT SPEC module you point at via env -- nothing here
is firmware-specific; you supply a concrete spec for your target.

Env:
  HAL_DIAG_CFGS / HAL_DIAG_SYMS                 (see _common.py)
  LAB_SPEC      path to a python file defining the spec (see below)   (required)
  LAB_BOOT_SECS native-boot watchdog seconds                          (default 120)
  LAB_EXP_SECS  per-experiment budget seconds                         (default 90)
  LAB_CHUNK     instructions per emu_start chunk                      (default 8000)

The LAB_SPEC module must define:
  BOOT_MARKER       : int        PC to boot to, then snapshot
  SUCCESS_MARKERS   : {int:str}  PCs whose hit = experiment succeeded (reported)
  INTERMEDIATE      : {int:str}  PCs to log-and-continue past (milestones)
  HANG_RANGE        : (lo,hi)    PC range that, if sampled for HANG_CHUNKS
                                 consecutive chunks, means "pended/idle"
  HANG_CHUNKS       : int        (e.g. 400)
  EXPERIMENTS       : [(name, install_fn)]   install_fn(backend) adds uc hooks

NOTE: PC-writing hooks disrupt unicorn's emu_start instruction `count` cap, so an
experiment can overshoot LAB_EXP_SECS -- the wall-clock deadline still bounds it.
Once such hooks are part of the real config, validate natively (run_cfg.py), not
in the lab.
"""
import importlib.util
import os
import sys
import time

try:
    from halucinator.diagnostics import _common as C
except ImportError:  # direct-path execution
    import _common as C
import unicorn


def _load_spec(path):
    spec = importlib.util.spec_from_file_location("lab_spec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snapshot(uc):
    ctx = uc.context_save()
    mem = [(b, bytes(uc.mem_read(b, e - b + 1))) for (b, e, _p) in uc.mem_regions()]
    return ctx, mem


def _restore(uc, snap):
    ctx, mem = snap
    for b, data in mem:
        uc.mem_write(b, data)
    uc.context_restore(ctx)


def _drain_irqs(backend):
    pend = getattr(backend, "_pending_irqs", None)
    apply = getattr(backend, "_apply_pending_irq", None)
    if pend and apply:
        while pend:
            apply(pend.pop(0))


def _chunks(backend, targets, inter, deadline, hang_range, hang_chunks, chunk):
    uc = backend._uc
    rd = backend.read_register
    thumb = 1 if getattr(backend, "_is_thumb", False) else 0
    lo, hi = hang_range
    streak = 0
    while time.time() < deadline:
        _drain_irqs(backend)
        pc = rd("pc")
        try:
            uc.emu_start(pc | thumb, 0xFFFFFFFF, timeout=0, count=chunk)
        except unicorn.UcError:
            pass
        npc = rd("pc")
        if npc in targets:
            return ("SUCCESS", npc)
        if npc in inter:
            sys.stderr.write(f"  >> {inter[npc]}\n")
            # nudge past the marker so we don't re-hit it forever
            continue
        if lo <= npc <= hi:
            streak += 1
            if streak >= hang_chunks:
                return ("HANG", npc)
        else:
            streak = 0
    return ("TIMEOUT", None)


def make_loop(spec_path):
    spec = _load_spec(spec_path)
    boot_secs = C.env_int("LAB_BOOT_SECS", 120)
    exp_secs = C.env_int("LAB_EXP_SECS", 90)
    chunk = C.env_int("LAB_CHUNK", 8000)

    def lab_loop(backend):
        uc = backend._uc
        t0 = time.time()
        sys.stderr.write("LAB: booting to BOOT_MARKER...\n")
        res, _ = _chunks(backend, {spec.BOOT_MARKER}, getattr(spec, "INTERMEDIATE", {}),
                         time.time() + boot_secs, getattr(spec, "HANG_RANGE", (0, 0)),
                         10 ** 9, chunk)
        if res != "SUCCESS":
            sys.stderr.write(f"LAB: did not reach BOOT_MARKER ({res})\n")
            return
        sys.stderr.write(f"LAB: reached boot marker in {time.time()-t0:.0f}s; snapshotting\n")
        snap = _snapshot(uc)
        mb = sum(len(d) for _, d in snap[1]) >> 20
        sys.stderr.write(f"LAB: snapshot {mb} MB\n")
        for name, install in spec.EXPERIMENTS:
            _restore(uc, snap)
            try:
                install(backend)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"LAB [{name}]: install failed {e!r}\n")
                continue
            t = time.time()
            res, addr = _chunks(backend, dict(spec.SUCCESS_MARKERS), getattr(spec, "INTERMEDIATE", {}),
                                time.time() + exp_secs, getattr(spec, "HANG_RANGE", (0, 0)),
                                getattr(spec, "HANG_CHUNKS", 400), chunk)
            tag = spec.SUCCESS_MARKERS.get(addr, hex(addr) if addr else "-")
            sys.stderr.write(f"LAB [{name}]: {res} {tag}  ({time.time()-t:.1f}s)\n")
            sys.stderr.flush()
        sys.stderr.write("LAB: done\n")

    return lab_loop


def main():
    spec_path = os.environ.get("LAB_SPEC")
    if not spec_path:
        sys.stderr.write("LAB_SPEC=<path to experiment spec module> is required\n")
        raise SystemExit(2)
    from halucinator import main as halmain
    halmain._in_process_dispatch_loop = make_loop(spec_path)
    sys.argv = C._argv()
    halmain.main()


if __name__ == "__main__":
    main()
