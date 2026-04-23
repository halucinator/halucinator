"""
Session-scoped conftest for halucinator test suite.

- Disables zmq.Context.__del__ to prevent C-level abort during GC
- Auto-marks tests that use real zmq/raw sockets so CI can skip them
- Runs test_debug_shell.py first (IPython/zmq import order matters)
- Forces clean exit to avoid interpreter shutdown hangs
"""
import os
import sys
import threading

import pytest

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
    "peripheral_models/test_host_ethernet.py",     # raw sockets + scapy
}

# Tests that must run first due to import-order or global-state sensitivity
_RUN_FIRST = {"test_debug_shell.py", "test_gpio_unit.py"}


def pytest_collection_modifyitems(config, items):
    """Auto-apply markers and reorder tests."""
    # Apply markers
    for item in items:
        nodeid = item.nodeid
        for fragment in _SLOW_ZMQ_PATHS:
            if fragment in nodeid:
                item.add_marker(pytest.mark.slow_zmq)
                break
        for fragment in _NEEDS_ROOT_PATHS:
            if fragment in nodeid:
                item.add_marker(pytest.mark.needs_root)
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


# ---------------------------------------------------------------------------
# Clean exit
# ---------------------------------------------------------------------------

_session_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    """Capture exit status before unconfigure (session isn't available there)."""
    global _session_exit_status
    _session_exit_status = exitstatus


def pytest_unconfigure(config):
    """Force-exit to avoid interpreter shutdown hangs from zmq threads."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_session_exit_status)
