"""
Session-scoped conftest for halucinator test suite.

- Disables zmq.Context.__del__ to prevent C-level abort during GC
- Auto-marks tests that use real zmq/raw sockets so CI can skip them
- Runs test_debug_shell.py first (IPython/zmq import order matters)
- Neutralizes the DAP-disconnect handler's os._exit so any test that
  exercises that path doesn't take down the pytest process
- Forces halucinator's long-running worker Thread subclasses to daemon=True
  so that interpreter shutdown can't hang on join()
- Patches ``ctypes.util.find_library`` so scapy's BPF-core import doesn't
  explode on ARM Linux images where gcc's ``-print-file-name`` path-search
  is broken
- Skips collection of root-only scapy-using tests when running non-root
"""
import ctypes.util as _ctypes_util
import os
import threading

import pytest

# ---------------------------------------------------------------------------
# Work around a ctypes.util.find_library(...) crash on some Linux ARM images.
#
# scapy 2.4.4's ``scapy/arch/bpf/core.py`` does, at module scope:
#
#     LIBC = cdll.LoadLibrary(find_library("libc"))
#
# On Ubuntu 22.04 arm64 (what our local docker image is built on), cpython's
# ``ctypes.util.find_library`` internally runs ``gcc -Wl,-t ... -lc`` and
# tries to parse the output; on this gcc version the parse finds a bogus
# ``liblibc.a`` path that doesn't exist on disk, and open() raises
# FileNotFoundError *during module import*. Collection of any test that
# imports scapy (even transitively, e.g. via halucinator.external_devices.
# host_ethernet_server) then fails with a collection error that masks every
# other test.
#
# On x86_64 (GitHub Actions CI) the same lookup resolves cleanly and this
# is a no-op, so the patch is safe in all environments.
# ---------------------------------------------------------------------------
_original_find_library = _ctypes_util.find_library


def _find_library_safe(name):
    try:
        return _original_find_library(name)
    except FileNotFoundError:
        # Fall back to well-known Linux shared library paths before giving up.
        if name == "libc":
            for candidate in (
                "libc.so.6",
                "/lib/aarch64-linux-gnu/libc.so.6",
                "/lib/x86_64-linux-gnu/libc.so.6",
                "/usr/lib/aarch64-linux-gnu/libc.so.6",
                "/usr/lib/x86_64-linux-gnu/libc.so.6",
            ):
                if os.path.exists(candidate) or candidate == "libc.so.6":
                    return candidate
        return None


_ctypes_util.find_library = _find_library_safe


# ---------------------------------------------------------------------------
# Prevent interpreter shutdown from hanging on non-daemon worker threads.
#
# Several halucinator Thread subclasses (notably
# external_devices.host_ethernet_server.IOServer, external_devices.ioserver.IOServer,
# peripheral_models.timer_model.TimerIRQ, peripheral_models.tcp_stack.TCPModel,
# and external_devices.vn8200xp.VN8200XP) call Thread.__init__ but never set
# daemon=True, and at least one of them (host_ethernet_server.IOServer) blocks
# in a zmq recv without a timeout. When a test exercises such a thread and
# its teardown doesn't fully join it (or the fixture's shutdown logic fails),
# threading._shutdown() joins it forever at interpreter exit. Externally the
# pytest process looks like it truncates its output at ~97% with exit code 0:
# pytest wrote the summary, but the process then sits in _shutdown().
#
# Patching threading.Thread.start() globally to force daemon=True on every
# non-main thread is the smallest correct fix for the test session. Production
# code is unaffected.
# ---------------------------------------------------------------------------
_original_thread_start = threading.Thread.start


def _daemonizing_start(self, *args, **kwargs):
    if not self.daemon:
        try:
            self.daemon = True
        except RuntimeError:
            # Already started — leave it alone and let start() raise its own.
            pass
    return _original_thread_start(self, *args, **kwargs)


threading.Thread.start = _daemonizing_start


# ---------------------------------------------------------------------------
# Keep the DAP-disconnect shutdown handler from killing the test process.
#
# The DAP "disconnect" request handler calls halucinator.debug_adapter
# ._shutdown_handler(0) — in production that's os._exit(0), which is
# exactly the right thing when a debug client closes but is catastrophic
# under pytest: the session exits silently with code 0, hiding every
# earlier failure. Swap in a no-op for the whole test session.
#
# The import is guarded because the module pulls in avatar2/IPython; some
# very-minimal test invocations may run before those are available.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _stub_debug_adapter_shutdown(monkeypatch):
    try:
        from halucinator.debug_adapter import debug_adapter
    except Exception:   # noqa: BLE001
        yield
        return
    monkeypatch.setattr(
        debug_adapter, "_shutdown_handler", lambda _code: None, raising=False,
    )
    yield

# ---------------------------------------------------------------------------
# Prevent zmq.Context.__del__ from aborting the process.
# When Python's GC collects a zmq Context while IOServer threads are still
# polling, zmq's signaler.cpp hits a fatal assertion. Making __del__ a no-op
# prevents the abort. The contexts leak but that's fine for a test process.
# ---------------------------------------------------------------------------
try:
    import zmq
    zmq.Context.__del__ = lambda self: None
except (ImportError, AttributeError):
    pass


# ---------------------------------------------------------------------------
# Tests that hang or need special privileges in CI, keyed by path fragment.
# ---------------------------------------------------------------------------
_SLOW_ZMQ_PATHS = {
    # Hangs even when run alone (blocking zmq subprocess loop)
    "peripheral_models/test_gpio.py",              # NOT test_gpio_unit.py
    # Uses real zmq subprocess; fails due to global zmq state pollution
    "peripheral_models/test_adc.py",
}

_NEEDS_ROOT_PATHS = {
    "peripheral_models/test_host_ethernet.py",          # raw sockets + scapy
    "peripheral_models/test_host_ethernet_server.py",   # raw sockets + scapy
    "peripheral_models/test_ethernet_virtual_hub.py",   # raw sockets + scapy
}

# Tests that must run first due to import-order or global-state sensitivity
_RUN_FIRST = {"test_debug_shell.py", "test_gpio_unit.py"}


def _scapy_importable():
    """Return True iff ``import scapy.all`` succeeds in this interpreter."""
    global _SCAPY_IMPORTABLE
    try:
        return _SCAPY_IMPORTABLE
    except NameError:
        pass
    try:
        import scapy.all  # noqa: F401
        _SCAPY_IMPORTABLE = True
    except Exception:  # noqa: BLE001 -- scapy can raise all manner of things
        _SCAPY_IMPORTABLE = False
    return _SCAPY_IMPORTABLE


def pytest_ignore_collect(collection_path, config):
    """
    Skip collection for tests that need raw sockets + scapy when either
    (a) we're not root (scapy raw-socket tests can't do anything useful),
    or (b) scapy itself still fails to import (beyond what the
    find_library patch above can recover from). Either way, there's no
    point letting pytest try to import the test module — it'll just
    produce a collection error that masks other failures.

    Note: pytest bypasses ``pytest_ignore_collect`` for test files passed
    explicitly on the command line, so this hook only protects against
    scapy-triggered collection errors during directory-level collection.
    The find_library patch above is what fixes the explicit-argument case.
    """
    path_str = str(collection_path)
    matches = any(fragment in path_str for fragment in _NEEDS_ROOT_PATHS)
    if not matches:
        return False
    if os.geteuid() != 0:
        return True
    return not _scapy_importable()


def _can_open_raw_socket():
    """
    Return True iff the current process can open a raw AF_PACKET socket.
    Running as root is necessary but not sufficient — e.g. inside Docker
    without --cap-add=NET_RAW, socket(AF_PACKET, SOCK_RAW) raises
    PermissionError even though geteuid() == 0. This check reflects what
    the scapy-using tests actually need.
    """
    global _CAN_RAW_SOCKET
    try:
        return _CAN_RAW_SOCKET
    except NameError:
        pass
    import socket
    try:
        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
        s.close()
        _CAN_RAW_SOCKET = True
    except (PermissionError, OSError, AttributeError):
        # AttributeError: AF_PACKET doesn't exist on non-Linux
        _CAN_RAW_SOCKET = False
    return _CAN_RAW_SOCKET


def pytest_collection_modifyitems(config, items):
    """Auto-apply markers and reorder tests."""
    # Apply markers
    needs_root_skip = pytest.mark.skip(
        reason="test requires raw-socket capability (e.g. CAP_NET_RAW)"
    )
    can_raw = _can_open_raw_socket()
    for item in items:
        nodeid = item.nodeid
        for fragment in _SLOW_ZMQ_PATHS:
            if fragment in nodeid:
                item.add_marker(pytest.mark.slow_zmq)
                break
        for fragment in _NEEDS_ROOT_PATHS:
            if fragment in nodeid:
                item.add_marker(pytest.mark.needs_root)
                # The file-level `pytestmark = skipif(geteuid() != 0, ...)`
                # already skips when not root, but root-in-Docker without
                # NET_RAW still can't open raw sockets. Skip explicitly
                # here so these tests never silently "run and fail" — they
                # should either execute with raw-socket capability, or be
                # skipped cleanly.
                if not can_raw:
                    item.add_marker(needs_root_skip)
                break

    # Move priority tests to the front
    first = []
    rest = []
    for item in items:
        if any(name in item.nodeid for name in _RUN_FIRST):
            first.append(item)
        else:
            rest.append(item)
    items[:] = first + rest


