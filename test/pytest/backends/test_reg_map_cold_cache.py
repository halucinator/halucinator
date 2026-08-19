# Copyright 2026 Christopher Wright
"""Every per-arch register map must return its map on the COLD path.

`_get_riscv_reg_map` populated `_REG_MAPS_CACHE` and then fell off the end,
returning None. The second call read the cache and returned a dict, so the bug
only shows on the very first call for that architecture -- which in a real run
is the first `write_register` at boot, where it surfaces as
`'NoneType' object has no attribute 'get'` and the device simply never boots.

The existing suite could not catch it. Constructing a backend never dereferences
the register map, and any earlier test that touched the arch warms the cache for
everything after it. A test has to evict the cache first, which is what this one
does -- for every architecture, so the next map added is covered by default.
"""
import pytest

unicorn = pytest.importorskip("unicorn")

from halucinator.backends import unicorn_backend as U  # noqa: E402

# (cache key, getter) for every per-arch map that caches.
GETTERS = [(k, getattr(U, "_get_%s_reg_map" % n))
           for k, n in [("arm", "arm"), ("arm64", "arm64"), ("mips", "mips"),
                        ("x86", "x86"), ("riscv", "riscv"), ("sparc", "sparc"),
                        ("tricore", "tricore"), ("m68k", "m68k")]
           if hasattr(U, "_get_%s_reg_map" % n)]


@pytest.mark.parametrize("key,getter", GETTERS, ids=[k for k, _ in GETTERS])
def test_cold_call_returns_the_map(key, getter):
    """First call after a cache eviction must return the map, not None."""
    U._REG_MAPS_CACHE.pop(key, None)
    m = getter()
    assert m is not None, (
        "%s returned None on the cold path -- it populates the cache and falls "
        "off the end, so only the FIRST call for this arch is broken" % getter.__name__)
    assert isinstance(m, dict) and m, "%s returned an empty/!dict map" % getter.__name__


@pytest.mark.parametrize("key,getter", GETTERS, ids=[k for k, _ in GETTERS])
def test_cold_and_warm_agree(key, getter):
    U._REG_MAPS_CACHE.pop(key, None)
    cold = getter()
    warm = getter()
    assert cold == warm, "%s: cold and warm calls disagree" % getter.__name__


@pytest.mark.parametrize("arch", ["cortex-m3", "arm", "arm64", "mips", "mipsel",
                                  "x86", "riscv32", "sparc", "tricore", "m68k",
                                  "powerpc", "ppc64"])
def test_reg_map_for_arch_cold(arch):
    """The public path, cold. This is what a booting device actually hits."""
    for k in list(U._REG_MAPS_CACHE):
        U._REG_MAPS_CACHE.pop(k, None)
    m = U._reg_map_for_arch(arch)
    assert m is not None, "_reg_map_for_arch(%r) is None on a cold cache" % arch
    assert "pc" in m, "_reg_map_for_arch(%r) has no 'pc'" % arch
