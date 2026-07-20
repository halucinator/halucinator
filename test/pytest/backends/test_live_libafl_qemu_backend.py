"""
Live e2e tests for LibAflQemuBackend.

Spawns the real halucinator/libafl-qemu-bridge ``qemu-system-arm``
binary, connects via GDB+QMP, and exercises the public HalBackend API
(memory r/w, register r/w, breakpoint set/remove). Skipped unless the
binary is on disk — typically pointed at by
``HALUCINATOR_QEMU_LIBAFL_ARM`` or built locally via
``./build_qemu.sh --source libafl-qemu-bridge arm-softmmu``.

Coverage rationale matches the avatar2 / direct-qemu live tests:
cont/single-step are intentionally not exercised here because the bare
Thumb test program omits a Cortex-M3 vector table; the configurable
machine resets to the SP/PC pair at flash[0..7] and the bp can fire
before the test's read completes. Full cont/step is exercised by the
firmware-level ``run_backend_matrix.sh`` harness (which uses real
firmware) and by the in-process live suite (unicorn + ghidra).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import pytest


_PROGRAM = (
    b"\x42\x20"            # 0x00: movs r0, #0x42
    b"\x10\x21"            # 0x02: movs r1, #0x10
    b"\x00\xbf"            # 0x04: nop
    b"\xaa\x22"            # 0x06: movs r2, #0xaa
    b"\x01\x4b"            # 0x08: ldr  r3, [pc, #4]
    b"\x1c\x60"            # 0x0a: str  r4, [r3]
    b"\xfe\xe7"            # 0x0c: b .
    b"\x00\xbf"            # 0x0e: nop
    b"\x00\x00\x00\x20"    # 0x10: literal = 0x20000000
)
_FLASH_BASE = 0x08000000
_RAM_BASE = 0x20000000
_BP_ADDR = _FLASH_BASE + 0x04


def _libafl_qemu_arm_path():
    """Resolve the libafl-qemu-bridge ARM binary the same way the
    backend does, so the skipif and the fixture agree."""
    from halucinator.backends.libafl_qemu_backend import (
        _resolve_libafl_qemu_path,
    )
    return _resolve_libafl_qemu_path("cortex-m3")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.mark.skipif(
    _libafl_qemu_arm_path() is None,
    reason="libafl-qemu-bridge qemu-system-arm not available "
           "(set HALUCINATOR_QEMU_LIBAFL_ARM or run "
           "`./build_qemu.sh --source libafl-qemu-bridge arm-softmmu`)",
)
class TestLibAflQemuBackendLive:
    @pytest.fixture
    def backend(self, tmp_path):
        from halucinator.backends.libafl_qemu_backend import LibAflQemuBackend

        fw = tmp_path / "tinyfw.bin"
        fw.write_bytes(_PROGRAM + b"\x00" * (0x1000 - len(_PROGRAM)))

        conf = {
            "cpu_model": "cortex-m3",
            "entry_address": _FLASH_BASE,
            "init_pc": _FLASH_BASE | 1,
            "init_sp": _RAM_BASE + 0x800,
            "memory_mapping": [
                {
                    "name": "flash",
                    "address": _FLASH_BASE,
                    "size": 0x1000,
                    "permissions": "rwx",
                    "is_special": False,
                    "is_symbolic": False,
                    "file": str(fw),
                },
                {
                    "name": "ram",
                    "address": _RAM_BASE,
                    "size": 0x1000,
                    "permissions": "rw-",
                    "is_special": False,
                    "is_symbolic": False,
                },
            ],
        }
        conf_path = tmp_path / "machine.json"
        conf_path.write_text(json.dumps(conf))

        gdb_port = _free_port()
        qmp_port = _free_port()
        while qmp_port == gdb_port:
            qmp_port = _free_port()

        qemu_path = _libafl_qemu_arm_path()
        cmd = [
            qemu_path,
            "-machine", f"configurable,config-filename={conf_path}",
            "-S",
            "-gdb", f"tcp::{gdb_port}",
            "-qmp", f"tcp:127.0.0.1:{qmp_port},server,nowait",
            "-nographic",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)

        b = LibAflQemuBackend(
            arch="cortex-m3", qemu_path=qemu_path,
            gdb_port=gdb_port, qmp_port=qmp_port,
        )
        b._process = proc

        last_err = None
        for _ in range(10):
            try:
                b.launch()
                last_err = None
                break
            except (ConnectionRefusedError, OSError) as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.3)
        if last_err is not None:
            proc.kill()
            raise last_err

        try:
            yield b
        finally:
            try:
                b.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                proc.kill()

    def test_memory_read_flash(self, backend):
        assert backend.read_memory(_FLASH_BASE, 2, 1) == 0x2042

    def test_memory_rw_ram(self, backend):
        backend.write_memory(_RAM_BASE + 0x100, 4, 0xCAFEBABE)
        assert backend.read_memory(_RAM_BASE + 0x100, 4, 1) == 0xCAFEBABE

    def test_register_rw(self, backend):
        backend.write_register("r5", 0xDEADBEEF)
        assert backend.read_register("r5") == 0xDEADBEEF

    def test_syx_snapshot_restore_round_trip(self, backend):
        """save_state() with no args uses the fast in-QEMU syx snapshot
        (libafl-syx-snapshot/restore QMP commands), not the slow generic
        reg+RAM-over-GDB path."""
        assert backend.snapshot_is_fast() is True
        backend.write_memory(_RAM_BASE + 0x40, 1, b"syx-me!!", 8, raw=True)
        backend.write_register("r0", 0x1234)

        snap = backend.save_state()          # -> libafl-syx-snapshot
        # Opaque handle: the state lives inside QEMU, not in the Python object.
        assert snap.data.get("syx") is True

        backend.write_memory(_RAM_BASE + 0x40, 1, b"CLOBBER!", 8, raw=True)
        backend.write_register("r0", 0)

        assert backend.restore_state(snap) is True   # -> libafl-syx-restore
        assert bytes(backend.read_memory(_RAM_BASE + 0x40, 1, 8,
                                         raw=True)) == b"syx-me!!"
        assert backend.read_register("r0") == 0x1234

    def test_syx_snapshot_superseded_is_refused(self, backend):
        """QEMU keeps exactly one syx snapshot; a handle to a superseded one
        must be refused rather than silently restoring the newer state."""
        snap_a = backend.save_state()
        backend.save_state()                 # supersedes A inside QEMU
        assert backend.restore_state(snap_a) is False

    def test_portable_snapshot_falls_back_to_generic(self, backend):
        """portable=True can't use the in-QEMU syx snapshot (not
        serializable); it falls back to the generic reg+RAM capture."""
        from backend_snapshot_helpers import _ensure_ram_region
        _ensure_ram_region(backend, _RAM_BASE, 0x1000)
        backend.write_memory(_RAM_BASE + 0x40, 1, b"portable", 8, raw=True)
        snap = backend.save_state(portable=True)
        assert "mem" in snap.data            # generic, picklable structure
        backend.write_memory(_RAM_BASE + 0x40, 1, b"XXXXXXXX", 8, raw=True)
        assert backend.restore_state(snap) is True
        assert bytes(backend.read_memory(_RAM_BASE + 0x40, 1, 8,
                                         raw=True)) == b"portable"

    def test_set_remove_breakpoint_does_not_crash(self, backend):
        bp = backend.set_breakpoint(_BP_ADDR)
        assert isinstance(bp, int)
        backend.remove_breakpoint(bp)
