"""
QEMUBackend — direct QEMU control via GDB stub + QMP socket, no avatar2.

This backend speaks the GDB Remote Serial Protocol (RSP) for register/memory
access, stepping, and breakpoints, and the QEMU Machine Protocol (QMP) for
device-level operations like IRQ injection.

Status: functional for ARM softmmu; IRQ injection requires the avatar-qemu
fork's QMP commands.
"""
from __future__ import annotations

import json
import logging
import os
import re
import select
import socket
import struct
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from .hal_backend import (
    ABI_MIXINS, ARM32HalMixin, ARMHalMixin, HalBackend, MemoryRegion,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback register layouts per arch — used when QEMU's GDB stub doesn't
# implement qXfer:features:read (MIPS, PPC on QEMU 6.2).
#
# Each layout is a dict {name: (byte_offset_in_g_packet, byte_size)}, matching
# the 'g' packet order QEMU emits for that target.
# ---------------------------------------------------------------------------

def _arm_layout() -> Dict[str, Tuple[int, int]]:
    m = {f"r{i}": (i * 4, 4) for i in range(13)}
    m.update({"sp": (13 * 4, 4), "lr": (14 * 4, 4), "pc": (15 * 4, 4),
              "cpsr": (25 * 4, 4)})
    return m


def _mips_layout() -> Dict[str, Tuple[int, int]]:
    # QEMU MIPS32 gdbstub order:
    #   r0-r31 (32 × 4 bytes) → offsets 0..124
    #   status (32) → 128
    #   lo (33) → 132
    #   hi (34) → 136
    #   badvaddr (35) → 140
    #   cause (36) → 144
    #   pc (37) → 148
    m: Dict[str, Tuple[int, int]] = {}
    for i in range(32):
        m[f"r{i}"] = (i * 4, 4)
    # MIPS ABI register aliases (on r0-r31):
    aliases = {
        "zero": 0, "at": 1, "v0": 2, "v1": 3,
        "a0": 4, "a1": 5, "a2": 6, "a3": 7,
        "t0": 8, "t1": 9, "t2": 10, "t3": 11, "t4": 12, "t5": 13,
        "t6": 14, "t7": 15, "s0": 16, "s1": 17, "s2": 18, "s3": 19,
        "s4": 20, "s5": 21, "s6": 22, "s7": 23, "t8": 24, "t9": 25,
        "k0": 26, "k1": 27, "gp": 28, "sp": 29, "fp": 30, "ra": 31,
    }
    for name, idx in aliases.items():
        m[name] = (idx * 4, 4)
    m["status"] = (128, 4)
    m["lo"] = (132, 4)
    m["hi"] = (136, 4)
    m["badvaddr"] = (140, 4)
    m["cause"] = (144, 4)
    m["pc"] = (148, 4)
    return m


def _ppc_layout(word: int = 4) -> Dict[str, Tuple[int, int]]:
    # QEMU 6.2 PPC32/PPC64 gdbstub g packet (verified empirically by probing
    # the live stub, not trusting target.xml which lies): 412 bytes total on
    # PPC32. Layout:
    #     r0-r31 (32 × word)  -> offsets 0..32*word-1
    #     f0-f31 (32 × 8)     -> FPRs always 8 bytes each, regardless of word
    #     pc, msr, cr, lr, ctr, xer, fpscr (each 4 bytes on PPC32;
    #     pc/msr/lr/ctr widen to 8 on PPC64)
    m: Dict[str, Tuple[int, int]] = {}
    offset = 0
    for i in range(32):
        m[f"r{i}"] = (offset, word)
        offset += word
    for i in range(32):
        m[f"f{i}"] = (offset, 8)
        offset += 8
    m["pc"]    = (offset, word); offset += word
    m["msr"]   = (offset, word); offset += word
    m["cr"]    = (offset, 4);    offset += 4
    m["lr"]    = (offset, word); offset += word
    m["ctr"]   = (offset, word); offset += word
    m["xer"]   = (offset, 4);    offset += 4
    m["fpscr"] = (offset, 4);    offset += 4
    # r1 is the PPC stack pointer
    m["sp"] = m["r1"]
    return m


def _arm64_layout() -> Dict[str, Tuple[int, int]]:
    # x0-x30 (31 × 8 bytes), sp (8), pc (8), pstate (4).
    m: Dict[str, Tuple[int, int]] = {}
    for i in range(31):
        m[f"x{i}"] = (i * 8, 8)
    m["sp"] = (31 * 8, 8)
    m["pc"] = (32 * 8, 8)
    m["pstate"] = (33 * 8, 4)
    return m


_FALLBACK_LAYOUTS: Dict[str, Dict[str, Tuple[int, int]]] = {
    "arm":      _arm_layout(),
    "cortex-m3": _arm_layout(),
    "arm64":    _arm64_layout(),
    "mips":     _mips_layout(),
    "powerpc":  _ppc_layout(4),
    "powerpc:MPC8XX": _ppc_layout(4),
    "ppc64":    _ppc_layout(8),
}


# ---------------------------------------------------------------------------
# Minimal GDB RSP client
# ---------------------------------------------------------------------------

class _GDBClient:
    """
    Minimal GDB Remote Serial Protocol client.
    Supports: read/write registers and memory, set/remove breakpoints, c/s.
    """

    def __init__(self, host: str = "localhost", port: int = 1234,
                 timeout: float = 5.0, arch: str = "arm"):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.arch = arch  # used for fallback register layout
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._ack_mode: bool = True  # conservative default; turned off if
                                     # QStartNoAckMode is supported

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # Initial '+' to ACK any startup banner; then negotiate no-ack mode.
        # If the stub doesn't support it (e.g. avatar-qemu's ppc64 stub
        # returns empty), we stay in ACK mode and +-back every packet.
        self._send_raw(b"+")
        # Some stubs (Renode) push an initial stop reply at attach; drain
        # whatever's buffered so it doesn't get mistaken for the response
        # to our first command.
        self._drain_socket(max_wait=0.3)
        self._send_pkt(b"QStartNoAckMode")
        resp = self._recv_pkt()
        if resp == b"OK":
            self._ack_mode = False
        # If QStartNoAckMode was unsupported, the stub may still have queued
        # extra bytes behind the empty reply — drain again.
        self._drain_socket(max_wait=0.1)
        # Discover register layout. For known archs this just picks the
        # hardcoded fallback; for unknown archs it probes via qXfer.
        self._discover_register_map()

    def _drain_socket(self, max_wait: float) -> None:
        """Read and discard any bytes already in the socket buffer."""
        prev_timeout = self._sock.gettimeout()
        self._sock.settimeout(max_wait)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return
        except (socket.timeout, BlockingIOError):
            pass
        finally:
            self._sock.settimeout(prev_timeout)

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    # Internal framing
    # ------------------------------------------------------------------

    @staticmethod
    def _checksum(data: bytes) -> int:
        return sum(data) & 0xFF

    def _send_raw(self, data: bytes) -> None:
        self._sock.sendall(data)

    def _send_pkt(self, payload: bytes) -> None:
        cs = self._checksum(payload)
        frame = b"$" + payload + b"#" + f"{cs:02x}".encode()
        with self._lock:
            self._send_raw(frame)

    def _recv_pkt(self) -> bytes:
        """Read one GDB RSP packet, return its payload. If ACK mode is on,
        send '+' back to the stub so it doesn't re-send. The read loop
        naturally skips any leading '+' ACKs from the stub."""
        buf = b""
        with self._lock:
            while True:
                ch = self._sock.recv(1)
                if ch == b"$":
                    break
                # Ignore '+' acks and any other bytes between packets.
            while True:
                ch = self._sock.recv(1)
                if ch == b"#":
                    # consume 2-char checksum
                    self._sock.recv(2)
                    break
                buf += ch
            if self._ack_mode:
                try:
                    self._sock.sendall(b"+")
                except OSError:
                    pass
        return buf

    def _cmd(self, payload: bytes) -> bytes:
        self._send_pkt(payload)
        return self._recv_pkt()

    # ------------------------------------------------------------------
    # Register layout discovery
    # ------------------------------------------------------------------
    # Filled by _discover_register_map during connect(). Maps register name
    # (lowercase) -> (byte_offset_in_g_packet, byte_size).
    _reg_layout: Dict[str, Tuple[int, int]] = None  # type: ignore[assignment]
    _g_packet_size: int = 0
    _ack_mode: bool = False  # class default — unit tests that skip __init__
                             # must still see this attr

    # Fallback ARM map for the mock-based unit tests that skip discovery.
    _ARM_REG_INDEX: Dict[str, int] = {
        **{f"r{i}": i for i in range(13)},
        "sp": 13, "lr": 14, "pc": 15,
        "cpsr": 25,
    }

    def _qxfer_read(self, object_name: str, annex: str = "") -> bytes:
        """Fetch a 'qXfer:<object>:read:<annex>:offset,length' blob, all chunks."""
        buf = b""
        offset = 0
        chunk = 4096
        while True:
            pkt = f"qXfer:{object_name}:read:{annex}:{offset:x},{chunk:x}"
            resp = self._cmd(pkt.encode())
            if not resp or resp.startswith(b"E"):
                return buf
            flag, data = resp[:1], resp[1:]
            buf += data
            offset += len(data)
            if flag == b"l":  # 'l' = last chunk
                return buf
            if flag != b"m":  # unknown
                return buf

    def _parse_target_xml(self, root_xml: bytes) -> Dict[str, Tuple[int, int]]:
        """Walk target.xml (and <xi:include> descendants) to build name -> (offset, size)."""
        import re
        # Gather all XML parts: root + any included files
        parts = [root_xml]
        for m in re.finditer(rb'<xi:include\s+href="([^"]+)"', root_xml):
            inner = self._qxfer_read("features", m.group(1).decode())
            if inner:
                parts.append(inner)
        joined = b"\n".join(parts)
        layout: Dict[str, Tuple[int, int]] = {}
        offset = 0
        # Match <reg ... /> in document order; use bitsize to compute byte size.
        for m in re.finditer(
            rb'<reg\b([^/>]*?)/>', joined, re.DOTALL):
            attrs = m.group(1)
            name_m = re.search(rb'name="([^"]+)"', attrs)
            bits_m = re.search(rb'bitsize="(\d+)"', attrs)
            regnum_m = re.search(rb'regnum="(\d+)"', attrs)
            if not name_m or not bits_m:
                continue
            name = name_m.group(1).decode().lower()
            size = int(bits_m.group(1)) // 8
            if regnum_m:
                # regnum resets offset; recompute from scratch.
                # For simplicity assume regs before this are contiguous (usually true).
                pass
            layout[name] = (offset, size)
            offset += size
        return layout

    def _discover_register_map(self) -> None:
        """Populate self._reg_layout.

        Prefer the hardcoded fallback layouts (verified against actual QEMU
        g-packet bytes) for known archs. QEMU's target.xml is often a lie —
        e.g. PPC 6.2 advertises 119 registers when the g packet only carries
        38. Only fall back to target.xml for unknown arches.
        """
        # Hardcoded layout by arch is authoritative for supported archs.
        if self.arch in _FALLBACK_LAYOUTS:
            self._reg_layout = _FALLBACK_LAYOUTS[self.arch].copy()
            self._g_packet_size = max(
                off + sz for off, sz in self._reg_layout.values()
            )
            return

        xml = self._qxfer_read("features", "target.xml")
        if xml:
            layout = self._parse_target_xml(xml)
            if layout:
                self._reg_layout = layout
                self._g_packet_size = sum(s for _, s in layout.values())
                # PPC stubs sometimes emit 'r0'.. but no 'sp'/'lr'/'pc'
                # aliases. Add common aliases where helpful.
                aliases = {
                    # ARM aliases when stub names are different
                    "sp": "r13", "lr": "r14", "pc": "r15",
                    # cortex-m reports xpsr, callers ask for cpsr
                    "cpsr": "xpsr",
                    # MIPS register aliases
                    "a0": "r4", "a1": "r5", "a2": "r6", "a3": "r7",
                    "v0": "r2", "v1": "r3", "ra": "r31", "gp": "r28",
                }
                for alias, src in aliases.items():
                    if alias not in self._reg_layout and src in self._reg_layout:
                        self._reg_layout[alias] = self._reg_layout[src]
                # PPC: stub uses lr/ctr/pc names already; ensure 'sp' alias
                # points at r1 (the PPC stack register)
                if "sp" not in self._reg_layout and "r1" in self._reg_layout:
                    self._reg_layout["sp"] = self._reg_layout["r1"]
                return
        # Stub didn't support qXfer:features:read (common for MIPS/PPC in
        # QEMU 6.2). Use an arch-specific hardcoded layout matching QEMU's
        # gdbstub default register dump order.
        self._reg_layout = _FALLBACK_LAYOUTS.get(
            self.arch, _FALLBACK_LAYOUTS["arm"]
        ).copy()
        self._g_packet_size = max(
            off + sz for off, sz in self._reg_layout.values()
        )

    def read_registers(self) -> List[int]:
        """Read all general-purpose registers; returns list of uint32.

        Deprecated in favor of read_register(name); kept for unit tests that
        still assume ARM-style layout.
        """
        resp = self._cmd(b"g")
        vals: List[int] = []
        for i in range(0, len(resp), 8):
            word_hex = resp[i:i + 8]
            if len(word_hex) < 8:
                break
            vals.append(int.from_bytes(bytes.fromhex(word_hex.decode()), "little"))
        return vals

    def _read_g_packet(self) -> bytes:
        resp = self._cmd(b"g")
        try:
            return bytes.fromhex(resp.decode())
        except ValueError:
            log.error("GDB g packet returned non-hex reply: %r", resp[:80])
            raise

    def _big_endian_arch(self) -> bool:
        """PPC/MIPS stubs send big-endian register bytes in the g packet."""
        # Heuristic: ARM/ARM64/x86/MIPSEL use little; PPC/MIPS BE use big.
        # The target.xml may tell us. For now use register name presence:
        # PowerPC stubs have 'msr', MIPS BE has 'cause'.
        if self._reg_layout is None:
            return False
        return any(r in self._reg_layout for r in ("msr", "cause"))

    def read_register(self, name: str) -> int:
        key = name.lower()
        if self._reg_layout and key in self._reg_layout:
            off, size = self._reg_layout[key]
            # avatar-qemu's ppc64 gdbstub asserts in handle_read_all_regs
            # ("len == gdbserver_state.mem_buf->len") whenever the 'g'
            # packet is sent — the assertion crashes the QEMU process
            # before we can read any register. Fall straight through to
            # the single-register 'p' protocol for ppc64.
            if self.arch == "ppc64":
                hex_resp = self._cmd(f"p{self._regnum_of(key):x}".encode())
                if hex_resp and not hex_resp.startswith(b"E"):
                    data = bytes.fromhex(hex_resp.decode())[:size]
                else:
                    data = b"\x00" * size
            else:
                data = self._read_g_packet()[off:off + size]
                if len(data) < size:
                    # Fall back to 'p' single-register read for archs that
                    # only send a prefix of the g packet by default (PPC
                    # large vector regs etc).
                    hex_resp = self._cmd(
                        f"p{self._regnum_of(key):x}".encode())
                    if hex_resp and not hex_resp.startswith(b"E"):
                        data = bytes.fromhex(hex_resp.decode())
            order = "big" if self._big_endian_arch() else "little"
            return int.from_bytes(data, order)
        if self._reg_layout:
            # Discovery ran but this name isn't known — don't fall back to the
            # ARM map (its indices will be wrong for other archs).
            raise ValueError(f"Unknown register: {name!r}")
        # Back-compat: ARM map, used only when discovery didn't populate layout.
        idx = self._ARM_REG_INDEX.get(key)
        if idx is None:
            raise ValueError(f"Unknown register: {name!r}")
        regs = self.read_registers()
        return regs[idx]

    def _regnum_of(self, name: str) -> int:
        """Byte offset -> regnum approximation using fixed-size layout."""
        if not self._reg_layout:
            return self._ARM_REG_INDEX.get(name, 0)
        # Regs in document order; count how many come before *name*.
        for i, reg_name in enumerate(self._reg_layout.keys()):
            if reg_name == name:
                return i
        return 0

    def write_register(self, name: str, value: int) -> None:
        """Write a single register. Tries 'P' first; falls back to read-modify-
        write via 'g'/'G' for stubs (like QEMU's) that don't implement 'P'."""
        key = name.lower()
        if self._reg_layout and key in self._reg_layout:
            off, size = self._reg_layout[key]
            order = "big" if self._big_endian_arch() else "little"
            payload = value.to_bytes(size, order, signed=False)
        else:
            idx = self._ARM_REG_INDEX.get(key)
            if idx is None:
                raise ValueError(f"Unknown register: {name!r}")
            off = idx * 4
            size = 4
            payload = value.to_bytes(4, "little")

        regnum = self._regnum_of(key) if self._reg_layout else self._ARM_REG_INDEX[key]
        hex_val = payload.hex()
        resp = self._cmd(f"P{regnum:x}={hex_val}".encode())
        if resp == b"OK":
            return
        if resp and not resp.startswith(b"E") and resp != b"":
            log.warning("write_register %s: unexpected response %r", name, resp)
            return
        # Empty reply -> 'P' unsupported. ppc64's avatar-qemu stub
        # can't service 'g' (and therefore 'G') without crashing; skip
        # the read-modify-write fallback for it and accept that the
        # register write didn't take.
        if self.arch == "ppc64":
            log.debug("write_register %s: 'P' unsupported on ppc64, skipping",
                      name)
            return
        all_bytes = bytearray(self._read_g_packet())
        if off + size > len(all_bytes):
            # Some stubs send a truncated g packet. Pad with zeros.
            all_bytes.extend(b"\x00" * (off + size - len(all_bytes)))
        all_bytes[off:off + size] = payload
        resp = self._cmd(b"G" + all_bytes.hex().encode())
        if resp != b"OK":
            log.warning("write_register(G) %s: unexpected response %r",
                        name, resp)

    # ------------------------------------------------------------------
    # Memory access
    # ------------------------------------------------------------------

    def read_memory(self, addr: int, length: int) -> bytes:
        resp = self._cmd(f"m{addr:x},{length:x}".encode())
        if resp.startswith(b"E"):
            raise OSError(f"GDB read_memory error: {resp!r}")
        return bytes.fromhex(resp.decode())

    def write_memory(self, addr: int, data: bytes) -> None:
        hex_data = data.hex()
        resp = self._cmd(f"M{addr:x},{len(data):x}:{hex_data}".encode())
        if resp != b"OK":
            raise OSError(f"GDB write_memory error: {resp!r}")

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def _bp_kind(self) -> int:
        """GDB RSP 'kind' field for Z0/z0 packets. On ARM this is the
        instruction size the stub should use to match the breakpoint
        address — 2 for Thumb, 4 for ARM. halucinator's cortex-m targets
        are Thumb-only; plain 'arm' is ARMv7A which we boot in ARM mode.
        On other archs the kind is effectively ignored by the stub, but
        we pick a sensible default."""
        if self.arch == "cortex-m3":
            return 2
        if self.arch == "arm":
            return 4
        if self.arch == "arm64":
            return 4
        # MIPS, PPC, PPC64 all have 4-byte fixed-width instructions.
        return 4

    def set_breakpoint(self, addr: int) -> None:
        kind = self._bp_kind()
        resp = self._cmd(f"Z0,{addr:x},{kind}".encode())
        if resp != b"OK":
            log.warning("set_breakpoint 0x%x: %r", addr, resp)

    def remove_breakpoint(self, addr: int) -> None:
        kind = self._bp_kind()
        resp = self._cmd(f"z0,{addr:x},{kind}".encode())
        if resp != b"OK":
            log.warning("remove_breakpoint 0x%x: %r", addr, resp)

    # Watchpoint packets:
    #   Z2 / z2 -> write watchpoint  (trap on writes)
    #   Z3 / z3 -> read watchpoint   (trap on reads)
    #   Z4 / z4 -> access watchpoint (trap on either)
    # kind here is the watch length in bytes (not the instruction size).
    _WATCH_TYPE = {(False, True): 2,  # write-only
                   (True, False): 3,  # read-only
                   (True, True):  4}  # access (read+write)

    def set_watchpoint(self, addr: int, size: int = 4,
                       read: bool = False, write: bool = True) -> None:
        ty = self._WATCH_TYPE.get((read, write))
        if ty is None:
            raise ValueError("watchpoint must have read or write enabled")
        resp = self._cmd(f"Z{ty},{addr:x},{size}".encode())
        if resp != b"OK":
            log.warning("set_watchpoint 0x%x (%s): %r",
                        addr, "rw"[read:] + "w"[:1 if write else 0], resp)

    def remove_watchpoint(self, addr: int, size: int = 4,
                          read: bool = False, write: bool = True) -> None:
        ty = self._WATCH_TYPE.get((read, write))
        if ty is None:
            return
        resp = self._cmd(f"z{ty},{addr:x},{size}".encode())
        if resp != b"OK":
            log.warning("remove_watchpoint 0x%x: %r", addr, resp)

    def cont(self) -> None:
        self._send_pkt(b"c")

    def step(self) -> None:
        self._send_pkt(b"s")

    def stop(self) -> None:
        """Send Ctrl-C (interrupt)."""
        self._send_raw(b"\x03")

    def wait_for_stop(self, timeout: float = 30.0) -> Optional[str]:
        """Block until a stop reply arrives; returns the stop reason string.
        Loops past any non-stop packets that show up in the queue first,
        then drains any duplicate stop replies (Renode emits these on bp
        hits) so they don't desync the next command/response pair."""
        prev_to = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        got: Optional[str] = None
        try:
            while got is None:
                try:
                    pkt = self._recv_pkt()
                except socket.timeout:
                    return None
                decoded = pkt.decode(errors="replace")
                if decoded and decoded[0] in ("S", "T", "W", "X"):
                    got = decoded
                else:
                    log.debug("wait_for_stop: skipping non-stop packet %r",
                              decoded[:40])
        finally:
            self._sock.settimeout(prev_to)

        # Drain any trailing packets that arrived alongside the stop (some
        # GDB stubs — Renode in particular — emit the stop reply twice
        # on a single bp hit and again after subsequent commands). 250ms
        # is comfortably longer than any real packet latency but short
        # enough that the caller's next command isn't delayed noticeably.
        drain_to = 0.25
        self._sock.settimeout(drain_to)
        try:
            while True:
                try:
                    extra = self._recv_pkt()
                except socket.timeout:
                    break
                log.debug("wait_for_stop: drained trailing packet %r",
                          extra[:40])
        finally:
            self._sock.settimeout(prev_to)
        return got


# ---------------------------------------------------------------------------
# Minimal QMP client
# ---------------------------------------------------------------------------

class _QMPClient:
    """Minimal QEMU Machine Protocol (QMP) client over a TCP socket."""

    def __init__(self, host: str = "localhost", port: int = 4444):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)
        self._sock.connect((self.host, self.port))
        # Read the greeting
        greeting = self._recv_line()
        # Negotiate capabilities
        self._send({"execute": "qmp_capabilities"})
        self._recv_line()  # OK response

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send(self, obj: Dict) -> None:
        data = (json.dumps(obj) + "\n").encode()
        with self._lock:
            self._sock.sendall(data)

    def _recv_line(self) -> Dict:
        buf = b""
        while True:
            ch = self._sock.recv(1)
            if not ch or ch == b"\n":
                break
            buf += ch
        return json.loads(buf) if buf else {}

    def execute(self, command: str, arguments: Optional[Dict] = None) -> Dict:
        msg: Dict = {"execute": command}
        if arguments:
            msg["arguments"] = arguments
        self._send(msg)
        return self._recv_line()


# ---------------------------------------------------------------------------
# QEMUBackend
# ---------------------------------------------------------------------------

class QEMUBackend(ARM32HalMixin, HalBackend):
    """
    HalBackend that talks to QEMU directly via GDB RSP + QMP.
    No avatar2 dependency.

    The QEMU process can either be launched by this backend (pass qemu_path
    and qemu_args) or you can attach to an already-running instance (pass
    gdb_host/gdb_port and qmp_host/qmp_port).

    Calling-convention helpers (get_arg, execute_return, read_string) come
    from the arch-specific ABI mixin selected at __init__ time — so an
    instance built for arch='arm64' exposes ARM64 calling conventions,
    arch='mips' exposes MIPS, etc. The mixin is bound onto the instance
    so bp_handlers can call backend.get_arg(i) uniformly.
    """

    def __init__(
        self,
        config: Any = None,
        arch: str = "cortex-m3",
        qemu_path: Optional[str] = None,
        qemu_args: Optional[List[str]] = None,
        gdb_host: str = "localhost",
        gdb_port: int = 1234,
        qmp_host: str = "localhost",
        qmp_port: int = 4444,
        **kwargs: Any,
    ):
        self.config = config
        self.arch = arch
        self.qemu_path = qemu_path
        self.qemu_args = qemu_args or []
        self._gdb = _GDBClient(gdb_host, gdb_port, arch=arch)
        self._qmp = _QMPClient(qmp_host, qmp_port)
        self._process: Optional[subprocess.Popen] = None
        self._bp_map: Dict[int, int] = {}   # bp_id → addr
        self._next_bp_id = 1
        self._regions: List[MemoryRegion] = []

        # Override the class-level ARM32 ABI helpers with the arch-specific
        # mixin's methods. ARM32 remains the default via inheritance so the
        # class is usable without __init__ (e.g. in unit tests).
        abi_cls = ABI_MIXINS.get(arch, ARM32HalMixin)
        self._abi = abi_cls
        if abi_cls is not ARM32HalMixin:
            for method_name in ("get_arg", "set_args", "get_ret_addr",
                                "set_ret_addr", "execute_return",
                                "read_string"):
                method = getattr(abi_cls, method_name, None)
                if method is not None:
                    setattr(self, method_name,
                            method.__get__(self, type(self)))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def launch(self) -> None:
        """Start QEMU and connect GDB + QMP."""
        if self.qemu_path:
            cmd = [self.qemu_path] + self.qemu_args + [
                "-S",  # start stopped
                f"-gdb tcp::{self._gdb.port}",
                f"-qmp tcp:{self._qmp.host}:{self._qmp.port},server,nowait",
            ]
            log.info("Launching QEMU: %s", " ".join(cmd))
            self._process = subprocess.Popen(
                " ".join(cmd), shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            time.sleep(0.5)  # let QEMU initialize

        retries = 5
        for i in range(retries):
            try:
                self._gdb.connect()
                break
            except ConnectionRefusedError:
                if i == retries - 1:
                    raise
                time.sleep(0.5)

        try:
            self._qmp.connect()
        except (ConnectionRefusedError, OSError):
            log.warning("QMP connection failed — IRQ injection will not work")

    def shutdown(self) -> None:
        self._gdb.disconnect()
        self._qmp.disconnect()
        if self._process:
            self._process.terminate()
            self._process = None

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def read_memory(self, addr: int, size: int, num_words: int = 1,
                    raw: bool = False) -> Union[int, bytes]:
        total = size * num_words
        data = self._gdb.read_memory(addr, total)
        if raw or num_words > 1:
            return bytes(data)
        if size == 1:
            return data[0]
        if size == 2:
            return struct.unpack_from("<H", data)[0]
        return struct.unpack_from("<I", data)[0]

    def write_memory(self, addr: int, size: int,
                     value: Union[int, bytes, bytearray],
                     num_words: int = 1, raw: bool = False) -> bool:
        if isinstance(value, (bytes, bytearray)):
            data = bytes(value)
        else:
            data = value.to_bytes(size * num_words, "little")
        try:
            self._gdb.write_memory(addr, data)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Registers
    # ------------------------------------------------------------------

    def read_register(self, register: str) -> int:
        return self._gdb.read_register(register)

    def write_register(self, register: str, value: int) -> None:
        self._gdb.write_register(register, value)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    def set_breakpoint(self, addr: int, hardware: bool = False,
                       temporary: bool = False) -> int:
        self._gdb.set_breakpoint(addr)
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._bp_map[bp_id] = addr
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        addr = self._bp_map.pop(bp_id, None)
        if addr is not None:
            self._gdb.remove_breakpoint(addr)

    def set_watchpoint(self, addr: int, write: bool = True,
                       read: bool = False, size: int = 4) -> int:
        self._gdb.set_watchpoint(addr, size=size, read=read, write=write)
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        # Reuse _bp_map — watchpoints share the id space with breakpoints.
        self._bp_map[bp_id] = (addr, size, read, write)
        return bp_id

    def remove_watchpoint(self, bp_id: int) -> None:
        entry = self._bp_map.pop(bp_id, None)
        if isinstance(entry, tuple) and len(entry) == 4:
            addr, size, read, write = entry
            self._gdb.remove_watchpoint(addr, size=size, read=read, write=write)

    def cont(self, blocking: bool = False) -> None:
        """Resume QEMU. Default non-blocking: the caller's dispatch loop
        waits for the next stop itself (see _qemu_backend_dispatch_loop)."""
        self._gdb.cont()
        if blocking:
            self._gdb.wait_for_stop()

    def stop(self) -> None:
        self._gdb.stop()

    def step(self) -> None:
        self._gdb.step()
        self._gdb.wait_for_stop(timeout=2.0)

    # ------------------------------------------------------------------
    # Memory regions
    # ------------------------------------------------------------------

    def add_memory_region(self, region: MemoryRegion) -> None:
        """Store region for reference; actual memory layout set via QEMU args."""
        self._regions.append(region)

    # ------------------------------------------------------------------
    # Optional: IRQ injection via QMP avatar commands
    # ------------------------------------------------------------------

    def inject_irq(self, irq_num: int) -> None:
        self._qmp.execute(
            "avatar-armv7m-inject-irq",
            {"num_irq": irq_num, "num_cpu": 0},
        )
