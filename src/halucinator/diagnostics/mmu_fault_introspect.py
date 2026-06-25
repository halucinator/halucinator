# Copyright 2026 Christopher Wright

"""mmu_fault_introspect.py -- ARM MMU / data-abort introspection.

For diagnosing the classic Unicorn ARMv5 MMU gate (after the firmware enables the
MMU, translated loads abort because TTBR0/page-walk handling is unreliable). Two
captures:
  - UNMAPPED ABORTS: on the first read/write/fetch to an unmapped address it prints
    the fault addr, access type/size, and the operand registers -- "exactly which
    access aborts, and the pointer behind it."
  - CP15 STATE at a chosen fault PC: reads TTBR0 (best-effort across Unicorn CP_REG
    signatures), takes the faulting VA from a register, derives the active L1 table
    base, computes the L1 descriptor address for that VA, and reads the descriptor
    from physical RAM -- so you can see whether the page table is even installed
    (a common finding is TTBR0==0 -> no translation -> every VA aborts).

If this confirms an MMU translation gate, the fix is usually HAL_MMU_FLAT_FALLBACK=1
(clear SCTLR.M on the first abort -> run flat). This harness is the DIAGNOSIS.

Env:
  HAL_DIAG_CFGS / HAL_DIAG_SYMS / HAL_DIAG_SECS   (see _common.py)
  FAULT_PC   '0x201748f4'   the faulting page-walk instruction (optional)
  VA_REG     'r9'           register holding the faulting VA at FAULT_PC (default r9)
  EXIT_ON    'fault'        'fault' = os._exit after first unmapped abort dump; else keep going
"""
import os
import sys

try:
    from halucinator.diagnostics import _common as C
except ImportError:  # direct-path execution
    import _common as C
import unicorn
import unicorn.arm_const as arm_const

_ACC = {
    unicorn.UC_MEM_READ_UNMAPPED: "READ", unicorn.UC_MEM_WRITE_UNMAPPED: "WRITE",
    unicorn.UC_MEM_FETCH_UNMAPPED: "FETCH", unicorn.UC_MEM_READ_PROT: "READ_PROT",
    unicorn.UC_MEM_WRITE_PROT: "WRITE_PROT", unicorn.UC_MEM_FETCH_PROT: "FETCH_PROT",
}


def _read_ttbr0(uc):
    """Best-effort CP15 TTBR0 (c2,c0,0) read across Unicorn versions."""
    for spec in (
        (15, 0, 0, 2, 0, 0, 0),     # (cp,is64,sec,crn,crm,opc1,opc2) shape
        (15, 0, 2, 0, 0, 0),
        (15, 0, 0, 2, 0, 0),
    ):
        try:
            return uc.reg_read(arm_const.UC_ARM_REG_CP_REG, spec)
        except Exception:  # noqa: BLE001
            continue
    return None


def _phys_read32(uc, addr):
    try:
        return int.from_bytes(uc.mem_read(addr & 0xFFFFFFFF, 4), "little")
    except Exception:  # noqa: BLE001
        return None


def install(backend):
    uc = backend._uc
    fault_pc = os.environ.get("FAULT_PC")
    va_reg = os.environ.get("VA_REG", "r9").lower()
    exit_on = os.environ.get("EXIT_ON", "fault")
    done = {"v": False}

    def abort_cb(u, access, address, size, value, _ud):
        if done["v"]:
            return False
        done["v"] = True
        regs = "  ".join(f"{r}=0x{u.reg_read(C.ARM_REGS[r]):08x}" for r in ("r0", "r1", "r9", "lr", "pc"))
        sys.stderr.write(f"MMU-ABORT {_ACC.get(access, access)} addr=0x{address:08x} size={size}  {regs}\n")
        ttbr0 = _read_ttbr0(u)
        sys.stderr.write(f"  TTBR0={'?' if ttbr0 is None else hex(ttbr0)}\n")
        sys.stderr.flush()
        if exit_on == "fault":
            os._exit(0)
        return False   # don't try to recover; let the backend handle it

    for h in (unicorn.UC_HOOK_MEM_READ_UNMAPPED, unicorn.UC_HOOK_MEM_WRITE_UNMAPPED,
              unicorn.UC_HOOK_MEM_FETCH_UNMAPPED, unicorn.UC_HOOK_MEM_READ_PROT,
              unicorn.UC_HOOK_MEM_WRITE_PROT, unicorn.UC_HOOK_MEM_FETCH_PROT):
        try:
            uc.hook_add(h, abort_cb)
        except Exception:  # noqa: BLE001
            pass

    if fault_pc:
        fp = int(fault_pc, 0)

        def cp15_cb(u, address, size, _ud):
            ttbr0 = _read_ttbr0(u)
            va = u.reg_read(C.ARM_REGS.get(va_reg, arm_const.UC_ARM_REG_R9))
            line = [f"MMU-STATE @0x{address:08x}  {va_reg}(VA)=0x{va:08x}  TTBR0={'?' if ttbr0 is None else hex(ttbr0)}"]
            if ttbr0:
                l1_base = ttbr0 & ~0x3FFF
                desc_addr = l1_base + ((va >> 20) << 2)
                desc = _phys_read32(u, desc_addr)
                line.append(f"  L1base=0x{l1_base:08x} desc@0x{desc_addr:08x}="
                            + ("UNMAPPED" if desc is None else f"0x{desc:08x}"))
            sys.stderr.write("  ".join(line) + "\n")
            sys.stderr.flush()

        uc.hook_add(unicorn.UC_HOOK_CODE, cp15_cb, begin=fp, end=fp)
        sys.stderr.write(f"MMU: CP15 probe armed at 0x{fp:08x}\n")
    sys.stderr.write("MMU: abort hooks armed\n")


if __name__ == "__main__":
    C.run(install)
