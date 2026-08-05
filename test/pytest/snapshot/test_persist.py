"""Disk-persistence tests: a snapshot written by one process must restore in a
fresh one. The load-bearing assertion is the CROSS-PROCESS round-trip: save a
unicorn snapshot to a file, restore it in a brand-new python process into a
freshly-init'd backend, and get a byte-identical machine image back.
"""
import hashlib
import pickle
import subprocess
import sys
import textwrap

import pytest

from halucinator.backends.hal_backend import Snapshot, SnapshotError
from halucinator.snapshot import (
    SystemSnapshot,
    load_snapshot_file,
    save_snapshot_file,
)
from halucinator.snapshot.persist import FORMAT_VERSION

from .test_backend_snapshot import (
    FLASH_BASE,
    FLASH_SIZE,
    RAM_BASE,
    RAM_SIZE,
    DictBackend,
)

try:
    import unicorn  # noqa: F401
    _HAVE_UNICORN = True
except ImportError:
    _HAVE_UNICORN = False


class TestFileRoundTrip:
    def test_generic_snapshot_survives_the_file(self, tmp_path):
        b = DictBackend()
        b.write_memory(RAM_BASE, 1, b"persist-me!", 11, raw=True)
        b.write_register("r3", 0x33333333)
        snap = b.save_state()

        p = save_snapshot_file(snap, tmp_path / "s.halsnap")
        loaded, header = load_snapshot_file(p)

        assert header["kind"] == "backend"
        assert header["backend_type"] == "DictBackend"
        assert header["format_version"] == FORMAT_VERSION

        # Clobber, then restore from the LOADED (deserialized) snapshot.
        b.write_memory(RAM_BASE, 1, b"XXXXXXXXXXX", 11, raw=True)
        b.write_register("r3", 0)
        assert b.restore_state(loaded) is True
        assert b.read_memory(RAM_BASE, 1, 11, raw=True) == b"persist-me!"
        assert b.read_register("r3") == 0x33333333

    def test_system_snapshot_kind_round_trips(self, tmp_path):
        b = DictBackend()
        snap = SystemSnapshot(backend=b.save_state(),
                              peripherals={"model:fake": {"rx": [1, 2, 3]}})
        p = save_snapshot_file(snap, tmp_path / "sys.halsnap")
        loaded, header = load_snapshot_file(p)
        assert header["kind"] == "system"
        assert isinstance(loaded, SystemSnapshot)
        assert loaded.peripherals == {"model:fake": {"rx": [1, 2, 3]}}
        assert b.restore_state(loaded.backend) is True


class TestUnicornVersionProbe:
    def test_returns_none_when_unicorn_absent(self, monkeypatch):
        """unicorn is an optional dependency; _unicorn_version must return
        None (not raise) when it isn't importable."""
        import sys
        from halucinator.snapshot import persist
        # sys.modules[name] = None makes `import name` raise ImportError.
        monkeypatch.setitem(sys.modules, "unicorn", None)
        assert persist._unicorn_version() is None


class TestHeaderValidation:
    def test_rejects_non_snapshot_file(self, tmp_path):
        p = tmp_path / "junk.halsnap"
        p.write_bytes(b"this is not a snapshot")
        with pytest.raises(SnapshotError):
            load_snapshot_file(p)

    def test_rejects_wrong_magic(self, tmp_path):
        import gzip
        p = tmp_path / "alien.halsnap"
        with gzip.open(p, "wb") as gz:
            pickle.dump(({"magic": "NOTHAL"}, None), gz)
        with pytest.raises(SnapshotError, match="bad magic"):
            load_snapshot_file(p)

    def test_rejects_future_format_version(self, tmp_path):
        import gzip
        p = tmp_path / "future.halsnap"
        with gzip.open(p, "wb") as gz:
            pickle.dump(({"magic": "HALSNAP", "format_version": 999}, None), gz)
        with pytest.raises(SnapshotError, match="format_version"):
            load_snapshot_file(p)

    def test_rejects_kind_payload_mismatch(self, tmp_path):
        import gzip
        p = tmp_path / "lie.halsnap"
        header = {"magic": "HALSNAP", "format_version": FORMAT_VERSION,
                  "kind": "system", "unicorn_version": None}
        with gzip.open(p, "wb") as gz:
            pickle.dump((header, Snapshot("X", 1, None)), gz)
        with pytest.raises(SnapshotError, match="kind"):
            load_snapshot_file(p)

    @pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn not installed")
    def test_rejects_nonportable_unicorn_snapshot(self, tmp_path):
        """A native context blob pickles fine but is process-local — the save
        must refuse it (with the portable=True fix in the message) rather
        than write a file that crashes some future process."""
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
        b.init()
        with pytest.raises(SnapshotError, match="portable=True"):
            save_snapshot_file(b.save_state(), tmp_path / "uc.halsnap")
        assert not (tmp_path / "uc.halsnap").exists()

    @pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn not installed")
    def test_unicorn_version_mismatch_warns_but_loads(self, tmp_path,
                                                      monkeypatch, caplog):
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        b = UnicornBackend(arch="cortex-m3")
        b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
        b.init()
        p = save_snapshot_file(b.save_state(portable=True),
                               tmp_path / "uc.halsnap")

        monkeypatch.setattr("halucinator.snapshot.persist._unicorn_version",
                            lambda: "0.0.0-other-build")
        with caplog.at_level("WARNING"):
            loaded, header = load_snapshot_file(p)
        assert loaded.backend_type == "UnicornBackend"
        assert any("unicorn" in r.message for r in caplog.records)


class TestAtomicity:
    def test_failed_save_leaves_no_file(self, tmp_path):
        import threading
        bad = Snapshot(backend_type="X", version=1,
                       data={"lock": threading.Lock()})  # unpicklable
        dest = tmp_path / "never.halsnap"
        with pytest.raises(SnapshotError):
            save_snapshot_file(bad, dest)
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []  # no temp litter either


# ---------------------------------------------------------------------------
# Portable-capture fidelity: the enumerated form must carry the system state
# a context blob would have (banked modes + CP15 on A-profile, MSP/PSP &
# friends on M-profile) — this is what the M340/ARM926 rehost depends on.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn not installed")
class TestPortableFidelity:
    def _fresh(self, arch, cpu_model=None):
        import os
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend
        old = os.environ.get("HAL_ARM_CPU_MODEL")
        if cpu_model:
            os.environ["HAL_ARM_CPU_MODEL"] = cpu_model
        try:
            b = UnicornBackend(arch=arch)
            b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rwx"))
            b.init()
        finally:
            if cpu_model:
                if old is None:
                    os.environ.pop("HAL_ARM_CPU_MODEL", None)
                else:
                    os.environ["HAL_ARM_CPU_MODEL"] = old
        return b

    def test_vfp_and_v7_cp15_round_trip_on_cortex_a15(self, tmp_path):
        """A VFP-capable A-profile core (cortex-a15) must round-trip the
        VFP register file and the v7 CP15 regs (VBAR/TTBR1/TPIDR*) through a
        portable disk snapshot — the fidelity gap findings 2 & 3 flagged."""
        import unicorn.arm_const as a
        b = self._fresh("arm", cpu_model="UC_CPU_ARM_CORTEX_A15")
        uc = b._uc
        # Plant FP state and a relocated vector base + a thread-id reg.
        uc.reg_write(a.UC_ARM_REG_D8, 0x1122334455667788)
        uc.reg_write(a.UC_ARM_REG_D0, 0xCAFEF00DDEADBEEF)
        uc.reg_write(a.UC_ARM_REG_CP_REG, (15, 0, 0, 12, 0, 0, 0, 0x70000000))  # VBAR
        uc.reg_write(a.UC_ARM_REG_CP_REG, (15, 0, 0, 13, 0, 0, 2, 0xABCD0000))  # TPIDRURW

        snap = b.save_state(portable=True)
        assert snap.data["vfp"]["d8"] == 0x1122334455667788
        assert snap.data["cp15"]["vbar"] == 0x70000000
        assert snap.data["cp15"]["tpidrurw"] == 0xABCD0000

        p = save_snapshot_file(snap, tmp_path / "a15.halsnap")
        loaded, _ = load_snapshot_file(p)
        b2 = self._fresh("arm", cpu_model="UC_CPU_ARM_CORTEX_A15")
        assert b2.restore_state(loaded) is True
        uc2 = b2._uc
        assert uc2.reg_read(a.UC_ARM_REG_D8) == 0x1122334455667788
        assert uc2.reg_read(a.UC_ARM_REG_D0) == 0xCAFEF00DDEADBEEF
        assert uc2.reg_read(a.UC_ARM_REG_CP_REG, (15, 0, 0, 12, 0, 0, 0)) == 0x70000000
        assert uc2.reg_read(a.UC_ARM_REG_CP_REG, (15, 0, 0, 13, 0, 0, 2)) == 0xABCD0000

    def test_a_profile_banked_and_cp15_round_trip(self, tmp_path):
        import unicorn.arm_const as a
        b = self._fresh("arm")
        uc = b._uc
        cpsr_id = a.UC_ARM_REG_CPSR
        orig_cpsr = uc.reg_read(cpsr_id)

        # Plant distinct values in three banked modes + one CP15 reg.
        planted = {0x12: ("irq", 0x20001111), 0x13: ("svc", 0x20002222),
                   0x11: ("fiq", 0x20003333)}
        for mode, (_tag, sp) in planted.items():
            uc.reg_write(cpsr_id, (orig_cpsr & ~0x1F) | mode)
            uc.reg_write(a.UC_ARM_REG_SP, sp)
        uc.reg_write(cpsr_id, orig_cpsr)
        dacr_spec = (15, 0, 0, 3, 0, 0, 0)
        uc.reg_write(a.UC_ARM_REG_CP_REG, dacr_spec + (0x55555555,))

        snap = b.save_state(portable=True)
        assert snap.data.get("portable") is True
        # It must actually have captured the planted banked values.
        assert snap.data["banked"]["irq"]["sp"] == 0x20001111
        assert snap.data["banked"]["svc"]["sp"] == 0x20002222
        assert snap.data["banked"]["fiq"]["sp"] == 0x20003333
        assert snap.data["cp15"]["dacr"] == 0x55555555

        # Round-trip through DISK into a FRESH backend (same process — the
        # cross-process case is covered below; this isolates fidelity).
        p = save_snapshot_file(snap, tmp_path / "aprof.halsnap")
        loaded, _ = load_snapshot_file(p)
        b2 = self._fresh("arm")
        assert b2.restore_state(loaded) is True
        uc2 = b2._uc
        for mode, (_tag, sp) in planted.items():
            uc2.reg_write(cpsr_id, (uc2.reg_read(cpsr_id) & ~0x1F) | mode)
            assert uc2.reg_read(a.UC_ARM_REG_SP) == sp, hex(mode)
        uc2.reg_write(cpsr_id, orig_cpsr)
        assert uc2.reg_read(a.UC_ARM_REG_CP_REG, dacr_spec) == 0x55555555
        assert uc2.reg_read(cpsr_id) == orig_cpsr

    def test_m_profile_sysregs_round_trip(self, tmp_path):
        import unicorn.arm_const as a
        b = self._fresh("cortex-m3")
        b._uc.reg_write(a.UC_ARM_REG_MSP, RAM_BASE + 0x4000)
        b._uc.reg_write(a.UC_ARM_REG_PSP, RAM_BASE + 0x2000)
        b._uc.reg_write(a.UC_ARM_REG_PRIMASK, 1)

        snap = b.save_state(portable=True)
        assert snap.data["m_sysregs"]["msp"] == RAM_BASE + 0x4000
        assert snap.data["m_sysregs"]["psp"] == RAM_BASE + 0x2000
        assert snap.data["m_sysregs"]["primask"] == 1

        p = save_snapshot_file(snap, tmp_path / "mprof.halsnap")
        loaded, _ = load_snapshot_file(p)
        b2 = self._fresh("cortex-m3")
        assert b2.restore_state(loaded) is True
        assert b2._uc.reg_read(a.UC_ARM_REG_MSP) == RAM_BASE + 0x4000
        assert b2._uc.reg_read(a.UC_ARM_REG_PSP) == RAM_BASE + 0x2000
        assert b2._uc.reg_read(a.UC_ARM_REG_PRIMASK) == 1

    def test_portable_resume_executes_identically(self):
        """The portable form must be as faithful a resume point as the native
        context blob: run the same code from both and compare images."""
        import struct
        b = self._fresh("cortex-m3")
        insns = struct.pack("<HHHH", 0x2001, 0x2102, 0x2203, 0x4770)
        b.write_memory(RAM_BASE, 1, insns, len(insns), raw=True)
        b.write_register("pc", RAM_BASE)
        b.write_register("lr", RAM_BASE + len(insns))
        b.write_register("sp", RAM_BASE + 0x2000)

        native = b.save_state()
        portable = b.save_state(portable=True)

        b.set_breakpoint(RAM_BASE + 6)
        b.cont()
        run_native_regs = {n: b.read_register(n) for n in b.list_registers()}

        assert b.restore_state(portable) is True
        b.cont()
        run_portable_regs = {n: b.read_register(n) for n in b.list_registers()}
        assert run_portable_regs == run_native_regs

        assert b.restore_state(native) is True  # leave nothing weird behind


# ---------------------------------------------------------------------------
# The point of the module: restore in a FRESH PROCESS.
# ---------------------------------------------------------------------------

_CHILD = textwrap.dedent("""
    import hashlib, struct, sys
    from halucinator.backends.hal_backend import MemoryRegion
    from halucinator.backends.unicorn_backend import UnicornBackend
    from halucinator.snapshot import load_snapshot_file

    FLASH_BASE, FLASH_SIZE = {flash_base}, {flash_size}
    RAM_BASE, RAM_SIZE = {ram_base}, {ram_size}

    # Fresh backend, same config as the producer — the documented restore flow.
    b = UnicornBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion("flash", FLASH_BASE, FLASH_SIZE, "rwx"))
    b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
    b.init()

    snap, header = load_snapshot_file(sys.argv[1])
    assert b.restore_state(snap) is True, "restore_state refused the snapshot"

    # Resume: run the same 3-instruction sequence to its breakpoint.
    b.set_breakpoint(FLASH_BASE + 6)
    b.cont()

    h = hashlib.sha256()
    for base, end, _p in sorted(b._uc.mem_regions()):
        h.update(bytes(b._uc.mem_read(base, end - base + 1)))
    for name in b.list_registers():
        h.update(struct.pack("<Q", b.read_register(name) & (2**64 - 1)))
    print(h.hexdigest())
""")


@pytest.mark.skipif(not _HAVE_UNICORN, reason="unicorn not installed")
class TestCrossProcess:
    def test_restore_in_fresh_process_resumes_identically(self, tmp_path):
        """Producer boots + snapshots + runs to completion; a child process
        restores the file and runs the same stretch. Both final machine
        images must hash identically."""
        import struct
        from halucinator.backends.hal_backend import MemoryRegion
        from halucinator.backends.unicorn_backend import UnicornBackend

        b = UnicornBackend(arch="cortex-m3")
        b.add_memory_region(MemoryRegion("flash", FLASH_BASE, FLASH_SIZE, "rwx"))
        b.add_memory_region(MemoryRegion("ram", RAM_BASE, RAM_SIZE, "rw"))
        b.init()

        # MOV r0,#1; MOV r1,#2; MOV r2,#3; BX LR  (Thumb)
        insns = struct.pack("<HHHH", 0x2001, 0x2102, 0x2203, 0x4770)
        b.write_memory(FLASH_BASE, 1, insns, len(insns), raw=True)
        b.write_memory(RAM_BASE, 1, b"boot-state-marker", 17, raw=True)
        b.write_register("pc", FLASH_BASE)
        b.write_register("lr", FLASH_BASE + len(insns))
        b.write_register("sp", RAM_BASE + 0x2000)

        snap_file = save_snapshot_file(b.save_state(portable=True),
                                       tmp_path / "boot.halsnap")

        # Producer's ground-truth run.
        b.set_breakpoint(FLASH_BASE + 6)
        b.cont()
        h = hashlib.sha256()
        for base, end, _p in sorted(b._uc.mem_regions()):
            h.update(bytes(b._uc.mem_read(base, end - base + 1)))
        for name in b.list_registers():
            h.update(struct.pack("<Q", b.read_register(name) & (2**64 - 1)))
        expected = h.hexdigest()

        child_src = _CHILD.format(flash_base=FLASH_BASE, flash_size=FLASH_SIZE,
                                  ram_base=RAM_BASE, ram_size=RAM_SIZE)
        result = subprocess.run(
            [sys.executable, "-c", child_src, str(snap_file)],
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (
            f"child failed:\nstdout={result.stdout}\nstderr={result.stderr}")
        assert result.stdout.strip() == expected, (
            "fresh-process resume diverged from the producer's run")
