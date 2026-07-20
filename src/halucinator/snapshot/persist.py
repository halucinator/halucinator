# Copyright 2026 Christopher Wright

"""Disk persistence for snapshots: survive process death.

The in-memory ``Snapshot`` / ``SystemSnapshot`` objects are checkpoint-fast but
die with the process. This module serializes them to a single compressed file
so a snapshot taken in one run can be restored in a *fresh* process (same
config, freshly-init'd backend) — the boot-once / iterate-forever workflow.

Only PORTABLE snapshots may be persisted: a raw unicorn context blob pickles
happily but embeds process-local pointers, and restoring one in another
process SIGBUSes (verified empirically — same unicorn build, same CPU model).
``save_snapshot_file`` therefore rejects a snapshot holding a native context
and tells the caller to capture with ``save_state(portable=True)`` /
``system_snapshot(..., portable=True)``.

Format: one gzip stream containing a pickled ``(header, payload)`` tuple.
The header is validated BEFORE the payload is handed to the caller, per the
snapshot contract (reject cleanly, never mutate on mismatch): ``magic`` /
``format_version`` gate files we don't understand; ``unicorn_version`` is
recorded for provenance and logged (warning) on mismatch — harmless for
portable payloads, but worth seeing in a debug log.

Writes are atomic: serialize to a temp file in the destination directory, then
``os.replace`` — a failed save never leaves a partial file behind.

The backend-compat check (``backend_type`` + ``SNAPSHOT_VERSION``) stays where
it already lives: ``HalBackend.restore_state`` validates before mutating.
"""
from __future__ import annotations

import gzip
import logging
import os
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from ..backends.hal_backend import Snapshot, SnapshotError
from .system_snapshot import SystemSnapshot

log = logging.getLogger(__name__)

MAGIC = "HALSNAP"
FORMAT_VERSION = 1

# gzip level 1: guest RAM is overwhelmingly zeros, so even the fastest level
# crushes it; higher levels just burn wall-clock on multi-MB images.
_GZIP_LEVEL = 1


def _unicorn_version() -> Optional[str]:
    try:
        import unicorn
        return str(unicorn.__version__)
    except ImportError:
        return None


def _backend_snap(payload: Union[Snapshot, SystemSnapshot]) -> Snapshot:
    return payload.backend if isinstance(payload, SystemSnapshot) else payload


def _reject_nonportable(payload: Union[Snapshot, SystemSnapshot]) -> None:
    """A native uc context blob is process-local (it pickles, but restoring
    it in another process crashes) — refuse it up front with the fix."""
    data = _backend_snap(payload).data
    if isinstance(data, dict) and "context" in data:
        raise SnapshotError(
            "save_snapshot_file: this snapshot holds a process-local native "
            "CPU context and cannot be persisted. Capture it with "
            "save_state(portable=True) / system_snapshot(..., portable=True) "
            "instead.")


def save_snapshot_file(payload: Union[Snapshot, SystemSnapshot],
                       path: Union[str, Path]) -> Path:
    """Serialize *payload* (a backend ``Snapshot`` or a whole
    ``SystemSnapshot``) to *path*. Atomic: on any failure the destination is
    untouched. Raises ``SnapshotError`` on unpicklable state.
    """
    path = Path(path)
    _reject_nonportable(payload)
    backend_snap = _backend_snap(payload)
    header = {
        "magic": MAGIC,
        "format_version": FORMAT_VERSION,
        "kind": "system" if isinstance(payload, SystemSnapshot) else "backend",
        "backend_type": backend_snap.backend_type,
        "snapshot_version": backend_snap.version,
        "unicorn_version": (_unicorn_version()
                            if backend_snap.backend_type == "UnicornBackend"
                            else None),
        "created": time.time(),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent,
                                    prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as raw, \
                gzip.GzipFile(fileobj=raw, mode="wb",
                              compresslevel=_GZIP_LEVEL) as gz:
            pickle.dump((header, payload), gz, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_name, path)
    except Exception as exc:  # noqa: BLE001
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise SnapshotError(
            f"save_snapshot_file({path}): serialization failed: {exc!r}"
        ) from exc
    log.info("snapshot saved: %s (%d bytes, kind=%s, backend=%s)",
             path, path.stat().st_size, header["kind"], header["backend_type"])
    return path


def load_snapshot_file(path: Union[str, Path],
                       ) -> Tuple[Union[Snapshot, SystemSnapshot], dict]:
    """Load a snapshot file. Returns ``(payload, header)`` where payload is the
    ``Snapshot`` / ``SystemSnapshot`` ready for ``restore_state`` /
    ``system_restore``.

    Raises ``SnapshotError`` if the file is not a HALucinator snapshot or is a
    format we don't understand. The header is validated before the payload is
    returned so a bad file can never reach a restore path.
    """
    path = Path(path)
    try:
        with gzip.open(path, "rb") as gz:
            header, payload = pickle.load(gz)
    except SnapshotError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SnapshotError(
            f"load_snapshot_file({path}): not a readable snapshot file: {exc!r}"
        ) from exc

    if not isinstance(header, dict) or header.get("magic") != MAGIC:
        raise SnapshotError(f"load_snapshot_file({path}): bad magic — "
                            "not a HALucinator snapshot file")
    if header.get("format_version") != FORMAT_VERSION:
        raise SnapshotError(
            f"load_snapshot_file({path}): format_version="
            f"{header.get('format_version')} not supported "
            f"(this build reads {FORMAT_VERSION})")

    saved_uc = header.get("unicorn_version")
    if saved_uc is not None:
        current_uc = _unicorn_version()
        if current_uc != saved_uc:
            log.warning("load_snapshot_file(%s): produced under unicorn %s, "
                        "loading under %s (portable payload — expected to be "
                        "fine, noted for provenance)",
                        path, saved_uc, current_uc or "none")

    expected = SystemSnapshot if header.get("kind") == "system" else Snapshot
    if not isinstance(payload, expected):
        raise SnapshotError(
            f"load_snapshot_file({path}): header kind={header.get('kind')!r} "
            f"but payload is {type(payload).__name__}")
    return payload, header
