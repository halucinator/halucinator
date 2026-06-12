# Copyright 2026 Christopher Wright

"""probe_at_pc.py -- dump registers + dereferenced memory at one or more PCs.

Answers: "what were the exact arg/pointer values entering this instruction?" and
"which method does this C++ indirect call actually dispatch to?" (via a pointer
walk obj -> vtable -> slot).

Env:
  HAL_DIAG_CFGS / HAL_DIAG_SYMS / HAL_DIAG_SECS   (see _common.py)
  PROBE_PCS   '0x20174..,0x2001cc14'  PCs to break on (address-filtered hook)
  REGS        'r0,r1,r9,lr'           registers to print          (default r0-r3,lr,sp,pc)
  DEREF       'r0; r9; r0 * +8 *'     pointer-walk specs, ';'-separated. Each spec
                                      is space-separated steps: first is a reg, then
                                      '*' (deref 32-bit) or '+N'/'-N' (offset).
                                      e.g. 'r0 * +8 *' = mem[mem[r0]+8] (vtable slot).
  HITS        per-PC max captures     (default 1 = one-shot per PC)
  EXIT_AFTER  total captures then exit (default 0 = never; rely on watchdog)

Example (find a C++ vtable dispatch target at a call site):
  HAL_DIAG_CFGS=cfg.yaml HAL_DIAG_SYMS=syms.csv PROBE_PCS=0x2001cc14 \
  REGS=r0,lr DEREF='r0 * +8 *' python3 probe_at_pc.py
"""
import os
import sys

try:
    from halucinator.diagnostics import _common as C
except ImportError:  # direct-path execution
    import _common as C
import unicorn


def _read32(uc, addr):
    try:
        return int.from_bytes(uc.mem_read(addr & 0xFFFFFFFF, 4), "little")
    except Exception:  # noqa: BLE001
        return None


def _eval(uc, spec):
    steps = spec.split()
    val = uc.reg_read(C.ARM_REGS[steps[0].lower()])
    for st in steps[1:]:
        if st == "*":
            val = _read32(uc, val)
            if val is None:
                return None
        elif st[0] in "+-":
            val = (val + int(st, 0)) & 0xFFFFFFFF
    return val


def install(backend):
    uc = backend._uc
    pcs = C.parse_addrs(os.environ["PROBE_PCS"])
    regs = [r.strip().lower() for r in os.environ.get("REGS", "r0,r1,r2,r3,lr,sp,pc").split(",") if r.strip()]
    derefs = [d.strip() for d in os.environ.get("DEREF", "").split(";") if d.strip()]
    hits_max = C.env_int("HITS", 1)
    exit_after = C.env_int("EXIT_AFTER", 0)
    state = {"hits": {p: 0 for p in pcs}, "total": 0}

    def cb(u, address, size, _ud):
        if state["hits"].get(address, 0) >= hits_max:
            return
        state["hits"][address] += 1
        state["total"] += 1
        parts = [f"r:{r}=0x{u.reg_read(C.ARM_REGS[r]):08x}" for r in regs if r in C.ARM_REGS]
        for d in derefs:
            v = _eval(u, d)
            parts.append(f"[{d}]=" + ("UNMAPPED" if v is None else f"0x{v:08x}"))
        sys.stderr.write(f"PROBE @0x{address:08x}  " + "  ".join(parts) + "\n")
        sys.stderr.flush()
        if exit_after and state["total"] >= exit_after:
            os._exit(0)

    for p in pcs:
        uc.hook_add(unicorn.UC_HOOK_CODE, cb, begin=p, end=p)
    sys.stderr.write(f"PROBE: armed at {[hex(p) for p in pcs]}\n")


if __name__ == "__main__":
    C.run(install)
