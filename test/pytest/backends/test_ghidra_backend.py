"""
Unit tests for GhidraBackend. pyghidra + the JVM are expensive to boot
and not always available in CI, so every test here either mocks the
EmulatorHelper or skips unless pyghidra is actually importable.
"""
import pytest

try:
    import pyghidra  # noqa: F401
    _HAVE_PYGHIDRA = True
except ImportError:
    _HAVE_PYGHIDRA = False


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_is_hal_backend():
    from halucinator.backends.hal_backend import HalBackend
    from halucinator.backends.ghidra_backend import GhidraBackend
    assert issubclass(GhidraBackend, HalBackend)


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_language_map_covers_multiarch_configs():
    from halucinator.backends.ghidra_backend import _LANGUAGE_MAP
    for arch in ("cortex-m3", "arm", "arm64", "mips", "powerpc", "ppc64"):
        assert arch in _LANGUAGE_MAP


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_set_breakpoint_returns_int():
    """Construct a backend without init(); set_breakpoint uses the emulator,
    so stub it with a mock."""
    from unittest import mock
    from halucinator.backends.ghidra_backend import GhidraBackend
    b = GhidraBackend(arch="cortex-m3")
    b._emulator = mock.MagicMock()
    b._address_factory = mock.MagicMock()
    bp = b.set_breakpoint(0x1000)
    assert isinstance(bp, int)
    b._emulator.setBreakpoint.assert_called_once()


def test_missing_pyghidra_raises_importerror(monkeypatch):
    """If pyghidra is not available, constructing GhidraBackend must fail
    fast with a clear ImportError."""
    from halucinator.backends import ghidra_backend as mod
    monkeypatch.setattr(mod, "_HAVE_PYGHIDRA", False)
    with pytest.raises(ImportError, match="pyghidra is required"):
        mod.GhidraBackend(arch="cortex-m3")


# ---------------------------------------------------------------------------
# Pure-Python tests (no JVM required) — covers register-alias logic,
# exception-return constants, watchpoint bookkeeping, and inject_irq's
# pre-emulator guard paths.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_register_aliases_cover_all_multi_arch():
    """_REGISTER_ALIASES must give "sp" a mapping on every non-Cortex arch
    we support, otherwise main.py's `backend.regs.sp = entry_sp` would
    fail with 'Unknown register' for those targets."""
    from halucinator.backends.ghidra_backend import GhidraBackend
    aliases = GhidraBackend._REGISTER_ALIASES
    for arch in ("powerpc", "powerpc:MPC8XX", "ppc64"):
        assert "sp" in aliases[arch], f"{arch}: no sp alias"
        assert aliases[arch]["sp"] == "r1"


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_exc_return_magic_matches_arm_v7m():
    """ARM-v7M spec: EXC_RETURN values always have top nibble 0xF, and
    0xFFFFFFF9 is specifically thread-mode + MSP. inject_irq relies on
    these exact values so the emulator never needs to talk to a real
    NVIC model."""
    from halucinator.backends.ghidra_backend import GhidraBackend
    assert GhidraBackend._EXC_RETURN_THREAD_MSP == 0xFFFFFFF9
    assert GhidraBackend._EXC_RETURN_MASK == 0xFFFFFFF0
    assert GhidraBackend._EXC_RETURN_MAGIC == 0xFFFFFFF0
    # Sanity-check the mask-match logic
    assert (0xFFFFFFF9 & GhidraBackend._EXC_RETURN_MASK) == \
           GhidraBackend._EXC_RETURN_MAGIC
    assert (0xFFFFFFFD & GhidraBackend._EXC_RETURN_MASK) == \
           GhidraBackend._EXC_RETURN_MAGIC
    # A normal code address must NOT match
    assert (0x08001234 & GhidraBackend._EXC_RETURN_MASK) != \
           GhidraBackend._EXC_RETURN_MAGIC


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_set_and_remove_watchpoint_tracks_bookkeeping():
    """Watchpoint IDs are integers, tracked by id, and removed cleanly."""
    from unittest import mock
    from halucinator.backends.ghidra_backend import GhidraBackend
    b = GhidraBackend(arch="cortex-m3")
    b._emulator = mock.MagicMock()
    wp_id = b.set_watchpoint(0x20000000, write=True, size=4)
    assert isinstance(wp_id, int)
    assert wp_id in b._watchpoints
    addr, size, read, write = b._watchpoints[wp_id]
    assert (addr, size, read, write) == (0x20000000, 4, False, True)
    b.remove_watchpoint(wp_id)
    assert wp_id not in b._watchpoints


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_watchpoint_rejects_no_read_or_write():
    from halucinator.backends.ghidra_backend import GhidraBackend
    b = GhidraBackend(arch="cortex-m3")
    with pytest.raises(ValueError, match="read or write"):
        b.set_watchpoint(0x20000000, write=False, read=False)


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_set_vtor_stores_vector_base():
    from halucinator.backends.ghidra_backend import GhidraBackend
    b = GhidraBackend(arch="cortex-m3")
    b.set_vtor(0x08000000)
    assert b._vtor == 0x08000000


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
def test_inject_irq_on_non_cortex_m_is_warning(caplog):
    """IRQ injection is Cortex-M specific; other archs log and return."""
    import logging
    from halucinator.backends.ghidra_backend import GhidraBackend
    b = GhidraBackend(arch="mips")
    caplog.set_level(logging.WARNING)
    b.inject_irq(5)
    assert any("cortex-m3" in r.message for r in caplog.records)


@pytest.mark.skipif(not _HAVE_PYGHIDRA, reason="pyghidra not installed")
@pytest.mark.skipif(
    not __import__("os").environ.get("GHIDRA_INSTALL_DIR"),
    reason="GHIDRA_INSTALL_DIR not set",
)
def test_live_thumb_step(tmp_path):
    """End-to-end: load a tiny Thumb program, step it, check r0 and PC.
    Verifies the JVM/EmulatorHelper path, the TMode context-register
    wiring for PC writes, and memory region loading."""
    from halucinator.backends.ghidra_backend import GhidraBackend
    from halucinator.backends.hal_backend import MemoryRegion
    # MOVS r0,#0x42 ; MOVS r1,#0x10 ; B .
    code = bytes([0x42, 0x20, 0x10, 0x21, 0xfe, 0xe7])
    fw = tmp_path / "fw.bin"
    fw.write_bytes(code + b"\x00" * 1024)
    b = GhidraBackend(arch="cortex-m3")
    b.add_memory_region(MemoryRegion(
        name="flash", base_addr=0x08000000, size=0x1000, file=str(fw),
    ))
    b.init()
    try:
        b.write_register("pc", 0x08000000 | 1)
        assert b.read_register("pc") == 0x08000000
        b.step()
        assert b.read_register("r0") == 0x42
        assert b.read_register("pc") == 0x08000002
        b.step()
        assert b.read_register("r1") == 0x10
        assert b.read_register("pc") == 0x08000004
    finally:
        b.shutdown()
