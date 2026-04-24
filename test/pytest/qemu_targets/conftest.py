"""
Per-directory conftest for qemu_targets tests.

Tests in this directory that actually spawn an avatar2 QemuTarget
(e.g. test_arm_qemu.py::test_irq_*_raises_without_irq_controller) need a
real qemu-system-* binary on disk. The tests currently pass
``executable=os.getenv("HALUCINATOR_QEMU_ARM")`` to ``avatar.add_target``;
if that env var is unset, avatar2 falls back to resolving via
``AVATAR2_QEMU_EXECUTABLE`` and then raises a generic Exception if it
can't find the binary.

Two things happen here:

1. If ``HALUCINATOR_QEMU_ARM`` points at a real file, mirror it into
   ``AVATAR2_QEMU_EXECUTABLE`` so avatar2 picks it up too. This keeps the
   CI workflow working even if one of the two vars was unset, and lets
   the avatar2 fallback path resolve correctly.

2. If no ARM QEMU binary is available, skip the real-avatar2 tests
   cleanly rather than letting them error out. Purely-mocked tests in
   this directory (arm64/mips/ppc/ppc64) are unaffected.
"""
import os
from pathlib import Path

import pytest


def _find_arm_qemu():
    """Return a path to a usable qemu-system-arm, or None."""
    candidates = [
        os.getenv("HALUCINATOR_QEMU_ARM"),
        os.getenv("AVATAR2_QEMU_EXECUTABLE"),
    ]
    # Fall back to the in-tree build, if present.
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(
        str(repo_root / "deps" / "build-qemu" / "arm-softmmu" / "qemu-system-arm")
    )
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


_ARM_QEMU = _find_arm_qemu()
if _ARM_QEMU is not None:
    # avatar2's ``Architecture.resolve`` reads ``AVATAR2_QEMU_EXECUTABLE``
    # directly when the ``executable=`` kwarg is None. Make sure it's set
    # so tests that pass ``executable=os.getenv("HALUCINATOR_QEMU_ARM")``
    # still work if that env var happened to be unset.
    os.environ.setdefault("AVATAR2_QEMU_EXECUTABLE", _ARM_QEMU)
    os.environ.setdefault("HALUCINATOR_QEMU_ARM", _ARM_QEMU)


def pytest_collection_modifyitems(config, items):
    """Skip real-avatar2 ARM QEMU tests if no qemu-system-arm is available."""
    if _ARM_QEMU is not None:
        return
    skip = pytest.mark.skip(
        reason="qemu-system-arm not found; set HALUCINATOR_QEMU_ARM or "
        "AVATAR2_QEMU_EXECUTABLE to a qemu-system-arm binary"
    )
    for item in items:
        # Only the tests that use the avatar_qemu / avatar_qemu_v7m fixtures
        # need a real binary; those all live in test_arm_qemu.py.
        if "qemu_targets/test_arm_qemu.py" in item.nodeid:
            fixturenames = getattr(item, "fixturenames", ())
            if "avatar_qemu" in fixturenames or "avatar_qemu_v7m" in fixturenames:
                item.add_marker(skip)
