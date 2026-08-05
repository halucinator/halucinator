# Copyright 2026 Christopher Wright

"""Tests for the two opt-in UnicornBackend knobs:

  HAL_CORTEXM_CPU_MODEL — pin a richer M-profile core than the Cortex-M3 default
  HAL_FAST_BP           — per-address breakpoint hooks instead of the global
                          per-instruction code hook

Both are default-OFF; the tests assert that the unset behaviour is unchanged,
that a bad value degrades loudly rather than silently, and that the fast-BP
eligibility gate refuses to disable a per-instruction feature behind the
user's back.
"""
import logging

import pytest

unicorn = pytest.importorskip("unicorn")

from halucinator.backends.unicorn_backend import UnicornBackend  # noqa: E402


@pytest.fixture
def clean_env(monkeypatch):
    """Both knobs unset, whatever the ambient environment says."""
    monkeypatch.delenv("HAL_CORTEXM_CPU_MODEL", raising=False)
    monkeypatch.delenv("HAL_FAST_BP", raising=False)
    monkeypatch.delenv("HAL_BREAK_RAM_SPINS", raising=False)
    return monkeypatch



@pytest.fixture
def hal_records():
    """Capture records from the HAL_LOG logger.

    hal_log's logger is configured `propagate=0`, so pytest's caplog (which
    handles at the root) never sees it -- attach our own handler instead."""
    from halucinator import hal_log
    logger = hal_log.getHalLogger()
    records = []

    class _Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _Grab()
    logger.addHandler(h)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)


def _cortex_m(**kw):
    b = UnicornBackend(arch="cortex-m3", **kw)
    b.init()
    return b


# --- HAL_CORTEXM_CPU_MODEL -------------------------------------------------

def test_cpu_model_defaults_to_m3(clean_env):
    """Unset -> plain Cortex-M3, and the backend comes up."""
    b = _cortex_m()
    assert b.arch_name == "cortex-m3"
    b.shutdown()


def test_cpu_model_accepts_m4(clean_env):
    """A valid richer core is accepted (M4 adds the DSP extension)."""
    clean_env.setenv("HAL_CORTEXM_CPU_MODEL", "UC_CPU_ARM_CORTEX_M4")
    b = _cortex_m()
    b.shutdown()


def test_cpu_model_bad_name_warns_and_falls_back(clean_env, hal_records):
    """A typo must not fall back silently -- that costs an afternoon of
    'why is my M4 DSP instruction still undefined'."""
    clean_env.setenv("HAL_CORTEXM_CPU_MODEL", "UC_CPU_ARM_CORTEX_M44")
    b = _cortex_m()
    assert any("HAL_CORTEXM_CPU_MODEL" in r.getMessage() for r in hal_records)
    b.shutdown()


def test_cpu_model_rejects_non_cpu_constant(clean_env, hal_records):
    """A real unicorn constant that isn't a CPU model must not be passed
    through to ctl_set_cpu_model."""
    clean_env.setenv("HAL_CORTEXM_CPU_MODEL", "UC_ARM_REG_R0")
    b = _cortex_m()
    assert any("HAL_CORTEXM_CPU_MODEL" in r.getMessage() for r in hal_records)
    b.shutdown()


# --- HAL_FAST_BP -----------------------------------------------------------

def test_fast_bp_off_by_default(clean_env):
    """Unset -> the global code hook path, exactly as before."""
    b = _cortex_m()
    assert b._fast_bp is False
    assert b._fast_bp_active is False
    b.shutdown()


def test_fast_bp_enables_per_address_hooks(clean_env):
    clean_env.setenv("HAL_FAST_BP", "1")
    b = _cortex_m()
    assert b._fast_bp_active is True
    b.shutdown()


def test_fast_bp_breakpoint_still_fires(clean_env):
    """The point of the knob: breakpoints must still work. Two movs then a
    self-branch; break on the second instruction."""
    clean_env.setenv("HAL_FAST_BP", "1")
    b = _cortex_m()
    base = 0x8000000
    b._uc.mem_map(base, 0x1000)
    b._uc.mem_write(base, bytes.fromhex("0122" "0123" "fee7"))
    b.set_breakpoint(base + 2)
    b.write_register("pc", base)
    b.cont()
    assert b._bp_hit_addr == base + 2
    b.shutdown()


def test_fast_bp_declined_when_spin_breaker_active(clean_env, hal_records):
    """HAL_BREAK_RAM_SPINS lives inside the global code hook, so fast-BP must
    stand down rather than silently disable it."""
    clean_env.setenv("HAL_FAST_BP", "1")
    clean_env.setenv("HAL_BREAK_RAM_SPINS", "1")
    b = _cortex_m()
    assert b._fast_bp_active is False
    assert any("HAL_FAST_BP ignored" in r.getMessage() for r in hal_records)
    b.shutdown()


def test_fast_bp_warns_if_loop_recover_enabled_after_init(clean_env, hal_records):
    """auto_recover_loops is a public attribute; flipping it after init()
    would leave the loop breaker dead. Say so instead of failing quietly."""
    clean_env.setenv("HAL_FAST_BP", "1")
    b = _cortex_m()
    assert b._fast_bp_active is True
    base = 0x8000000
    b._uc.mem_map(base, 0x1000)
    b._uc.mem_write(base, bytes.fromhex("0122" "0123" "fee7"))
    b.set_breakpoint(base + 2)
    b.write_register("pc", base)
    b.auto_recover_loops = True
    b.cont()
    assert any("auto_recover_loops" in r.getMessage() for r in hal_records)
    b.shutdown()
