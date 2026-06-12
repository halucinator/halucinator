# Copyright 2026 Christopher Wright

"""Shared plumbing for the diagnostic harnesses.

All harnesses are CONFIG-AGNOSTIC and ADDRESS-PARAMETERIZED via env vars, so the
same script works on any firmware/config — nothing target-specific is baked in.

Common env:
  HAL_DIAG_CFGS   comma-separated -c config files          (required)
  HAL_DIAG_SYMS   symbol csv for -s                        (optional)
  HAL_DIAG_SECS   watchdog seconds before os._exit(0)      (default 180)
  HAL_DIAG_ARGS   extra argv tokens appended verbatim      (optional)
Plus the usual backend knobs you may want to set yourself, e.g.
  HAL_MMU_FLAT_FALLBACK=1  HAL_IRQ_CHUNK=8000

A harness supplies an `install(backend)` callback; we wrap UnicornBackend.init so
the callback runs right after the backend builds its unicorn instance (backend._uc
is then live and the firmware hasn't started). The callback installs unicorn hooks
and returns; all capture happens in those hooks on the single emu thread.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable, List, Tuple

import unicorn
import unicorn.arm_const as arm_const   # noqa: F401  (harnesses import via this module)


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v, 0) if v else default


def parse_addrs(s: str) -> List[int]:
    """'0xA,0xB,0xC' -> [0xA,0xB,0xC]."""
    return [int(x, 0) for x in s.replace(" ", "").split(",") if x]


def parse_ranges(s: str) -> List[Tuple[int, int]]:
    """'0xLO-0xHI,0xLO2-0xHI2' -> [(lo,hi),...]."""
    out: List[Tuple[int, int]] = []
    for part in s.replace(" ", "").split(","):
        if not part:
            continue
        lo, hi = part.split("-")
        out.append((int(lo, 0), int(hi, 0)))
    return out


def in_ranges(addr: int, ranges: List[Tuple[int, int]]) -> bool:
    for lo, hi in ranges:
        if lo <= addr < hi:
            return True
    return False


def _argv() -> List[str]:
    cfgs = os.environ.get("HAL_DIAG_CFGS")
    if not cfgs:
        sys.stderr.write("HAL_DIAG_CFGS is required (comma-separated -c config files)\n")
        raise SystemExit(2)
    argv = ["hal"]
    for c in cfgs.split(","):
        argv += ["-c", c]
    syms = os.environ.get("HAL_DIAG_SYMS")
    if syms:
        argv += ["-s", syms]
    argv += ["--emulator", "unicorn"]
    extra = os.environ.get("HAL_DIAG_ARGS")
    if extra:
        argv += extra.split()
    return argv


def run(install: Callable[[object], None], *, deadline: int | None = None) -> None:
    """Wrap UnicornBackend.init with `install(backend)`, arm a watchdog, run main()."""
    if deadline is None:
        deadline = env_int("HAL_DIAG_SECS", 180)
    from halucinator.backends import unicorn_backend as ub

    _orig = ub.UnicornBackend.init

    def _patched(self):  # noqa: ANN001
        _orig(self)
        try:
            install(self)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"DIAG: install() failed: {e!r}\n")

    ub.UnicornBackend.init = _patched

    def _watchdog():
        sys.stderr.write("DIAG: watchdog fired -- exiting\n")
        sys.stderr.flush()
        os._exit(0)

    threading.Timer(deadline, _watchdog).start()
    sys.argv = _argv()
    from halucinator import main
    main.main()


# convenience: ARM register name -> unicorn const, for harnesses that take REGS=
ARM_REGS = {
    "r0": arm_const.UC_ARM_REG_R0, "r1": arm_const.UC_ARM_REG_R1,
    "r2": arm_const.UC_ARM_REG_R2, "r3": arm_const.UC_ARM_REG_R3,
    "r4": arm_const.UC_ARM_REG_R4, "r5": arm_const.UC_ARM_REG_R5,
    "r6": arm_const.UC_ARM_REG_R6, "r7": arm_const.UC_ARM_REG_R7,
    "r8": arm_const.UC_ARM_REG_R8, "r9": arm_const.UC_ARM_REG_R9,
    "r10": arm_const.UC_ARM_REG_R10, "sl": arm_const.UC_ARM_REG_R10,
    "r11": arm_const.UC_ARM_REG_R11, "fp": arm_const.UC_ARM_REG_R11,
    "r12": arm_const.UC_ARM_REG_R12, "ip": arm_const.UC_ARM_REG_R12,
    "sp": arm_const.UC_ARM_REG_SP, "lr": arm_const.UC_ARM_REG_LR,
    "pc": arm_const.UC_ARM_REG_PC, "cpsr": arm_const.UC_ARM_REG_CPSR,
}
