"""
Unit tests for LibAflQemuBackend.

LibAflQemuBackend is a thin :class:`QEMUBackend` subclass that picks a
different QEMU binary (the halucinator/libafl-qemu-bridge build) but
shares all of QEMUBackend's GDB+QMP control plane. So this test
focuses on the binary-resolution contract:

* env var ``HALUCINATOR_QEMU_LIBAFL_<ARCH>`` is honoured per-arch
* explicit ``qemu_path=`` wins
* missing binary leaves ``qemu_path = None`` and logs a warning
"""
from __future__ import annotations

import logging
import os

import pytest

from halucinator.backends.libafl_qemu_backend import (
    LibAflQemuBackend, _resolve_libafl_qemu_path,
)


class TestLibAflEnvResolution:
    def test_env_var_wins_per_arch(self, tmp_path, monkeypatch):
        fake = tmp_path / "qemu-system-arm"
        fake.write_bytes(b"")
        fake.chmod(0o755)
        monkeypatch.setenv("HALUCINATOR_QEMU_LIBAFL_ARM", str(fake))
        assert _resolve_libafl_qemu_path("cortex-m3") == str(fake)
        assert _resolve_libafl_qemu_path("arm") == str(fake)

    def test_per_arch_var_does_not_leak_across(self, tmp_path, monkeypatch):
        arm_bin = tmp_path / "qemu-system-arm"
        arm_bin.write_bytes(b"")
        monkeypatch.setenv("HALUCINATOR_QEMU_LIBAFL_ARM", str(arm_bin))
        # ppc64 lookup should not pick up the ARM binary
        monkeypatch.delenv("HALUCINATOR_QEMU_LIBAFL_PPC64", raising=False)
        result = _resolve_libafl_qemu_path("ppc64")
        assert result is None or result.endswith("qemu-system-ppc64")

    def test_unknown_arch_returns_none(self, monkeypatch):
        for var in ("HALUCINATOR_QEMU_LIBAFL_ARM",
                    "HALUCINATOR_QEMU_LIBAFL_ARM64",
                    "HALUCINATOR_QEMU_LIBAFL_MIPS",
                    "HALUCINATOR_QEMU_LIBAFL_PPC",
                    "HALUCINATOR_QEMU_LIBAFL_PPC64"):
            monkeypatch.delenv(var, raising=False)
        assert _resolve_libafl_qemu_path("riscv64") is None

    def test_missing_env_falls_through_to_default_or_none(self, monkeypatch):
        # Without env vars or the default build tree, must return None
        # rather than throwing — the backend logs a warning and the
        # caller decides what to do.
        for var in ("HALUCINATOR_QEMU_LIBAFL_ARM",
                    "HALUCINATOR_QEMU_LIBAFL_ARM64"):
            monkeypatch.delenv(var, raising=False)
        # The default build path probably doesn't exist on a fresh
        # checkout, so either None or a real file is acceptable.
        result = _resolve_libafl_qemu_path("arm")
        assert result is None or os.path.isfile(result)


class TestLibAflBackendInstantiation:
    def test_explicit_qemu_path_overrides_env(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit-qemu"
        explicit.write_bytes(b"")
        env_path = tmp_path / "env-qemu"
        env_path.write_bytes(b"")
        monkeypatch.setenv("HALUCINATOR_QEMU_LIBAFL_ARM", str(env_path))
        b = LibAflQemuBackend(arch="cortex-m3", qemu_path=str(explicit))
        assert b.qemu_path == str(explicit)

    def test_env_picked_up_when_no_explicit(self, tmp_path, monkeypatch):
        env_path = tmp_path / "env-qemu"
        env_path.write_bytes(b"")
        monkeypatch.setenv("HALUCINATOR_QEMU_LIBAFL_ARM", str(env_path))
        b = LibAflQemuBackend(arch="cortex-m3")
        assert b.qemu_path == str(env_path)

    def test_missing_binary_warns_but_does_not_raise(
        self, monkeypatch, caplog,
    ):
        for var in ("HALUCINATOR_QEMU_LIBAFL_ARM",
                    "HALUCINATOR_QEMU_LIBAFL_ARM64",
                    "HALUCINATOR_QEMU_LIBAFL_MIPS",
                    "HALUCINATOR_QEMU_LIBAFL_PPC",
                    "HALUCINATOR_QEMU_LIBAFL_PPC64"):
            monkeypatch.delenv(var, raising=False)
        caplog.set_level(logging.WARNING)
        # Use a probably-unbuilt arch in case the default tree exists
        # for arm/arm64 on the developer's box.
        b = LibAflQemuBackend(arch="ppc64")
        # qemu_path may be None or a stale path; the contract is
        # "doesn't raise, surfaces a warning when no binary found".
        if b.qemu_path is None:
            assert any("libafl-qemu-bridge" in r.message
                       for r in caplog.records)

    def test_inherits_qemu_backend_arch_dispatch(self, tmp_path, monkeypatch):
        env_path = tmp_path / "env-qemu"
        env_path.write_bytes(b"")
        monkeypatch.setenv("HALUCINATOR_QEMU_LIBAFL_ARM64", str(env_path))
        b = LibAflQemuBackend(arch="arm64")
        # The base class wires per-arch ABI mixin onto self at __init__;
        # confirm the inheritance chain didn't get severed.
        from halucinator.backends.qemu_backend import QEMUBackend
        assert isinstance(b, QEMUBackend)
        assert b.arch == "arm64"
