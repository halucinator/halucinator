# Copyright 2020 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
# Under the terms of Contract DE-NA0003525 with NTESS, the U.S. Government retains
# certain rights in this software.
"""Robust QEMU GDB-socket connection for avatar2-backed targets.

avatar2's ``QemuTarget.init()`` spawns QEMU with
``-gdb unix:<path>,server,nowait`` and then *immediately* opens the GDB
connection, with no wait for QEMU to come up. On a loaded machine (seen
intermittently on the ubuntu-24.04 CI runner) QEMU has not yet created and
listened on the unix socket by the time avatar2 connects, so the connect
raises ``GDBProtocol was unable to connect`` and the whole emulation run dies
before the guest ever executes.

QEMU creates the socket during start-up init, *before* running the guest, so
the socket file appearing is a sound readiness signal. This mixin waits
(bounded) for that file to exist and retries the connect a few times, turning
a fatal start-up race into a short, silent wait.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

# Max seconds to wait for QEMU to create its GDB unix socket. Override via
# HALUCINATOR_QEMU_CONNECT_TIMEOUT for unusually slow/loaded environments.
_CONNECT_TIMEOUT = float(os.environ.get("HALUCINATOR_QEMU_CONNECT_TIMEOUT", "30"))
# Attempts at the avatar2 GDB/QMP handshake once the socket exists. The wait
# above closes the common race; the retries cover the nanosecond window
# between QEMU's bind() (socket file appears) and listen().
_CONNECT_RETRIES = 3


class RobustQemuConnectMixin:
    """Harden avatar2 ``QemuTarget._connect_protocols`` against the QEMU
    start-up socket race.

    Insert as the *first* base of a ``QemuTarget`` subclass so this override
    wins in the MRO and ``super()`` still reaches avatar2's implementation::

        class ARMQemuTarget(RobustQemuConnectMixin, QemuTarget):
            ...

    Only the avatar2 path (which calls ``init()`` -> ``_connect_protocols()``)
    is affected; the direct ``QEMUBackend`` never invokes ``_connect_protocols``
    and so is untouched.
    """

    def _connect_protocols(self):  # type: ignore[override]
        sock = getattr(self, "gdb_unix_socket_path", None)
        if sock:
            self._wait_for_gdb_socket(sock)

        last_exc: Optional[BaseException] = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                return super()._connect_protocols()  # type: ignore[misc]
            except Exception as exc:  # avatar2 raises a bare Exception on failure
                last_exc = exc
                proc = getattr(self, "_process", None)
                if proc is not None and proc.poll() is not None:
                    # QEMU has exited — retrying can't help.
                    break
                if attempt < _CONNECT_RETRIES:
                    log.warning(
                        "GDB connect attempt %d/%d failed (%s); retrying",
                        attempt, _CONNECT_RETRIES, exc,
                    )
                    time.sleep(0.5)
        assert last_exc is not None
        raise last_exc

    def _wait_for_gdb_socket(self, sock_path: str) -> None:
        deadline = time.monotonic() + _CONNECT_TIMEOUT
        while not os.path.exists(sock_path):
            proc = getattr(self, "_process", None)
            if proc is not None and proc.poll() is not None:
                # QEMU died during start-up; let the connect surface the real
                # error rather than blocking for the full timeout.
                return
            if time.monotonic() >= deadline:
                log.warning(
                    "QEMU GDB socket %s did not appear within %.0fs; "
                    "attempting connect anyway", sock_path, _CONNECT_TIMEOUT,
                )
                return
            time.sleep(0.02)
