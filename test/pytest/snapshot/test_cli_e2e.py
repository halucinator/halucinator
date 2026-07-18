# Copyright 2026 Christopher Wright

"""End-to-end CLI snapshot/restore: real firmware, real `halucinator.main`
processes, real process boundary.

  1. ground truth: run the arm32 UART firmware start-to-TX, record what it
     transmits.
  2. run A: --snapshot-at main --snapshot-out boot.halsnap
     (boots from the reset path, snapshots at main, exits).
  3. run B (FRESH process): --restore boot.halsnap
     (resumes at main — skipping the boot path entirely — and must transmit
     exactly what the ground-truth run did).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ARM32_DIR = REPO_ROOT / "test" / "multi_arch" / "arm32"
MAIN_ADDR = "0x08000051"  # `main` in test_uart_addrs.yaml

pytestmark = pytest.mark.skipif(
    not (ARM32_DIR / "firmware" / "test_uart.bin").exists(),
    reason="arm32 test firmware not built",
)

_TX_RE = re.compile(rb"UART TX:(b['\"].*?['\"])")


def _cmd(*extra: str, ports: int) -> list:
    return [
        sys.executable, "-m", "halucinator.main",
        "-c", "test_uart_config.yaml",
        "-c", "test_uart_addrs.yaml",
        "-c", "test_uart_memory.yaml",
        "--emulator", "unicorn",
        "-r", str(ports), "-t", str(ports + 1),
        *extra,
    ]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return env


def _run_until_tx_or_exit(cmd, timeout=90.0) -> bytes:
    """Run the CLI, return combined output once a UART TX line appears (the
    firmware then blocks in uart_read forever — kill it) or the process
    exits on its own. select()-driven so a silent child can't wedge the
    test past its deadline."""
    import select
    proc = subprocess.Popen(cmd, cwd=ARM32_DIR, env=_env(),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    out = b""
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                out += chunk
                if _TX_RE.search(out):
                    return out
                if not chunk and proc.poll() is not None:
                    return out
            elif proc.poll() is not None:
                out += proc.stdout.read() or b""
                return out
        raise AssertionError(
            f"no UART TX and no exit within {timeout}s:\n"
            f"{out.decode(errors='replace')}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.slow_zmq
class TestCliSnapshotRestore:
    def test_snapshot_then_restore_transmits_identically(self, tmp_path):
        snap = tmp_path / "boot.halsnap"

        # 1. Ground truth: what does an uninterrupted boot transmit?
        truth_out = _run_until_tx_or_exit(_cmd(ports=6710))
        truth_tx = _TX_RE.search(truth_out)
        assert truth_tx, truth_out.decode(errors="replace")

        # 2. Boot again, snapshot at main, exit. Must exit on its own,
        #    BEFORE any UART traffic (main hasn't run yet).
        snap_run = subprocess.run(
            _cmd("--snapshot-at", MAIN_ADDR, "--snapshot-out", str(snap),
                 ports=6720),
            cwd=ARM32_DIR, env=_env(), capture_output=True, timeout=90)
        combined = snap_run.stdout + snap_run.stderr
        assert snap_run.returncode == 0, combined.decode(errors="replace")
        assert b"Snapshot written" in combined
        assert not _TX_RE.search(combined), "snapshot ran past main"
        assert snap.exists() and snap.stat().st_size > 0

        # 3. Fresh process, restore, resume at main: identical TX.
        restore_out = _run_until_tx_or_exit(
            _cmd("--restore", str(snap), ports=6730))
        assert b"Restored snapshot" in restore_out, \
            restore_out.decode(errors="replace")
        restored_tx = _TX_RE.search(restore_out)
        assert restored_tx, restore_out.decode(errors="replace")
        assert restored_tx.group(1) == truth_tx.group(1), (
            "restored run transmitted differently:\n"
            f"  truth:    {truth_tx.group(1)!r}\n"
            f"  restored: {restored_tx.group(1)!r}")

    def test_restore_missing_file_fails_cleanly(self, tmp_path):
        r = subprocess.run(
            _cmd("--restore", str(tmp_path / "nope.halsnap"), ports=6740),
            cwd=ARM32_DIR, env=_env(), capture_output=True, timeout=90)
        assert r.returncode != 0
