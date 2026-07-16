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
                 timeout: float = 5.0, arch: str = "arm",
                 unix_path: Optional[str] = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.arch = arch  # used for fallback register layout
        self.unix_path = unix_path  # AF_UNIX path; None -> TCP (host/port)
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._ack_mode: bool = True  # conservative default; turned off if
                                     # QStartNoAckMode is supported
        # Persistent receive buffer shared by every read site (_recv_pkt,
        # _drain_socket). Reading the RSP stream one byte per recv() syscall
        # made each packet cost O(bytes) syscalls — catastrophic for large
        # register/memory dumps. We now recv() in 4 KiB chunks and frame
        # packets out of this buffer.
        self._rxbuf: bytes = b""
        # Per-stop register-file cache. read_register() serves every register
        # from one cached 'g' packet instead of a fresh GDB round-trip per
        # register — a bp handler reading get_arg(0..3) was costing 4 full
        # register dumps. Invalidated whenever the CPU may have changed regs
        # (cont/step/stop/write_register). Closes most of the speed gap vs
        # avatar2, which caches its register file the same way.
        self._g_cache: Optional[bytes] = None
        # Seconds wait_for_stop spends draining trailing/duplicate stop
        # replies after a stop. Renode emits the stop reply twice on a bp
        # hit, so RenodeBackend bumps this. QEMU sends exactly one stop reply
        # per stop, so the default is 0.0 — draining for a fixed window on
        # *every* breakpoint hit otherwise dominates wall-clock (a 0.25s
        # drain × hundreds of intercepts was the bulk of the qemu-vs-avatar2
        # gap). When 0.0, only already-buffered packets are swept (no block).
        self.stop_drain_timeout: float = 0.0

    def connect(self) -> None:
        if self.unix_path:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect(self.unix_path)
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.host, self.port))
            # Tiny request/response packets — disable Nagle so they aren't
            # delayed waiting to coalesce.
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._rxbuf = b""
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
        """Read and discard any bytes already pending — both the buffered
        bytes and whatever is still in the kernel socket buffer."""
        self._rxbuf = b""   # discard anything already framed-but-unread
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

    def _fill_buf(self) -> None:
        """Pull one chunk into the receive buffer. Propagates socket.timeout
        (callers set a deadline and rely on it) and raises on clean close."""
        chunk = self._sock.recv(4096)
        if not chunk:
            raise ConnectionError("GDB stub closed the connection")
        self._rxbuf += chunk

    def _recv_pkt(self) -> bytes:
        """Read one GDB RSP packet ($<payload>#cc), return its payload.

        Framed out of the persistent buffer rather than one byte per
        syscall. Parsing is NON-destructive until a complete packet is in
        hand: ``self._rxbuf`` is only advanced once payload + '#' + the two
        checksum chars are all present, so a mid-packet socket.timeout (the
        wait_for_stop drain relies on this) leaves the partial packet intact
        for the next call instead of corrupting the stream.

        Leading '+'/'-' ACKs and any inter-packet junk are skipped, matching
        the previous behaviour. If ACK mode is on, '+' is sent back so the
        stub doesn't retransmit."""
        with self._lock:
            while True:
                start = self._rxbuf.find(b"$")
                if start == -1:
                    # No packet start yet — buffer holds only ACKs/junk.
                    self._rxbuf = b""
                else:
                    hash_i = self._rxbuf.find(b"#", start + 1)
                    if hash_i != -1 and len(self._rxbuf) >= hash_i + 3:
                        payload = self._rxbuf[start + 1:hash_i]
                        # drop payload + '#' + 2 checksum chars; keep the rest
                        self._rxbuf = self._rxbuf[hash_i + 3:]
                        if self._ack_mode:
                            try:
                                self._sock.sendall(b"+")
                            except OSError:
                                pass
                        return payload
                self._fill_buf()

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
        if self._g_cache is not None:
            return self._g_cache
        resp = self._cmd(b"g")
        try:
            self._g_cache = bytes.fromhex(resp.decode())
            return self._g_cache
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

    # x86's GDB i386/amd64 register set has no 'pc'/'sp' — halucinator uses
    # those generic names (e.g. to set the entry point and initial stack).
    # Alias them to the arch's real register when the canonical name isn't
    # present in the discovered layout.
    _REG_ALIASES = {"pc": ("eip", "rip"), "sp": ("esp", "rsp")}

    def _alias_reg(self, key: str) -> str:
        if self._reg_layout and key not in self._reg_layout:
            for cand in self._REG_ALIASES.get(key, ()):
                if cand in self._reg_layout:
                    return cand
        return key

    def read_register(self, name: str) -> int:
        key = self._alias_reg(name.lower())
        if self._reg_layout and key in self._reg_layout:
            off, size = self._reg_layout[key]
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

    def _reg_off_size_payload(self, name: str,
                              value: int) -> Tuple[str, int, int, bytes, int]:
        """Resolve a register write to (key, byte_offset, byte_size, payload,
        regnum). Shared by write_register and write_registers."""
        key = self._alias_reg(name.lower())
        if self._reg_layout and key in self._reg_layout:
            off, size = self._reg_layout[key]
            order = "big" if self._big_endian_arch() else "little"
            payload = value.to_bytes(size, order, signed=False)
            regnum = self._regnum_of(key)
        else:
            idx = self._ARM_REG_INDEX.get(key)
            if idx is None:
                raise ValueError(f"Unknown register: {name!r}")
            off = idx * 4
            size = 4
            payload = value.to_bytes(4, "little")
            regnum = idx
        return key, off, size, payload, regnum

    def _patch_g_cache(self, off: int, size: int, payload: bytes) -> None:
        """Keep the per-stop register cache coherent after a successful write,
        instead of invalidating it. A subsequent read of any register at this
        stop then stays a cache hit (no extra full-'g' round-trip)."""
        if self._g_cache is not None and off + size <= len(self._g_cache):
            buf = bytearray(self._g_cache)
            buf[off:off + size] = payload
            self._g_cache = bytes(buf)
        else:
            # Can't patch (cold cache or write past the cached image) — drop it.
            self._g_cache = None

    def write_register(self, name: str, value: int) -> None:
        """Write a single register. Tries 'P' first; falls back to read-modify-
        write via 'g'/'G' for stubs (like QEMU's) that don't implement 'P'."""
        key, off, size, payload, regnum = self._reg_off_size_payload(name, value)
        hex_val = payload.hex()
        resp = self._cmd(f"P{regnum:x}={hex_val}".encode())
        if resp == b"OK":
            # P succeeded — patch the cache in place rather than invalidate.
            self._patch_g_cache(off, size, payload)
            return
        if resp and not resp.startswith(b"E") and resp != b"":
            log.warning("write_register %s: unexpected response %r", name, resp)
            self._g_cache = None
            return
        # Empty reply -> 'P' unsupported; fall back to 'G'
        # read-modify-write.
        all_bytes = bytearray(self._read_g_packet())
        if off + size > len(all_bytes):
            # Some stubs send a truncated g packet. Pad with zeros.
            all_bytes.extend(b"\x00" * (off + size - len(all_bytes)))
        all_bytes[off:off + size] = payload
        resp = self._cmd(b"G" + all_bytes.hex().encode())
        if resp != b"OK":
            log.warning("write_register(G) %s: unexpected response %r",
                        name, resp)
            self._g_cache = None
        else:
            self._g_cache = bytes(all_bytes)

    def write_registers(self, regs: Dict[str, int]) -> None:
        """Write several registers, collapsing to a single 'G' round-trip when
        a warm register cache is available to patch. This is the fast path for
        execute_return (set return value + pc in one go).

        Cold cache (or no discovered layout): fall back to individual 'P'
        writes — each keeps the cache coherent and costs the same as fetching
        the full 'g' just to do one 'G'."""
        if not regs:
            return
        if self._g_cache is None or not self._reg_layout:
            for name, value in regs.items():
                self.write_register(name, value)
            return
        base = bytearray(self._g_cache)
        try:
            for name, value in regs.items():
                _key, off, size, payload, _regnum = \
                    self._reg_off_size_payload(name, value)
                if off + size > len(base):
                    base.extend(b"\x00" * (off + size - len(base)))
                base[off:off + size] = payload
        except ValueError:
            # Unknown register name — bail to per-register writes for clarity.
            for name, value in regs.items():
                self.write_register(name, value)
            return
        resp = self._cmd(b"G" + base.hex().encode())
        if resp == b"OK":
            self._g_cache = bytes(base)
        else:
            # 'G' rejected — invalidate and retry one at a time via 'P'/'G'.
            self._g_cache = None
            for name, value in regs.items():
                self.write_register(name, value)

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
        self._g_cache = None          # regs change once the CPU runs
        self._send_pkt(b"c")

    def step(self) -> None:
        self._g_cache = None
        self._send_pkt(b"s")

    def stop(self) -> None:
        """Send Ctrl-C (interrupt)."""
        self._g_cache = None
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
        # GDB stubs — Renode in particular — emit the stop reply twice on a
        # single bp hit and again after subsequent commands). The drain
        # window is per-backend (self.stop_drain_timeout): Renode sets it
        # positive; QEMU leaves it 0.0 because it sends exactly one stop
        # reply, and blocking for a fixed window on every single breakpoint
        # hit was the dominant cost vs avatar2. With 0.0 we still sweep any
        # packet already framed/pending (non-blocking) so a same-instant
        # duplicate can't desync the stream, but we never wait.
        drain_to = self.stop_drain_timeout
        self._sock.settimeout(drain_to if drain_to > 0 else 0.0)
        try:
            while True:
                try:
                    extra = self._recv_pkt()
                except (socket.timeout, BlockingIOError):
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
    """Minimal QEMU Machine Protocol (QMP) client.

    Speaks QMP over either a Unix domain socket (``unix_path``) or a TCP
    socket (``host``/``port``). A Unix socket is strongly preferred for the
    halucinator inject_irq hot path: every interrupt is a QMP request →
    reply round-trip, and a Unix socket avoids the loopback TCP/IP stack
    (no Nagle, no checksums, no port). When TCP is used we at least disable
    Nagle so the tiny request/response messages aren't delayed.
    """

    def __init__(self, host: str = "localhost", port: int = 4444,
                 unix_path: Optional[str] = None):
        self.host = host
        self.port = port
        self.unix_path = unix_path
        self._sock: Optional[socket.socket] = None
        self._buf: bytes = b""
        self._lock = threading.Lock()

    def connect(self) -> None:
        if self.unix_path:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(self.unix_path)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.host, self.port))
            # Tiny QMP request/response messages — don't let Nagle coalesce
            # and delay them.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._buf = b""
        # Read the greeting
        self._recv_line()
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
        self._buf = b""

    def _send(self, obj: Dict) -> None:
        data = (json.dumps(obj) + "\n").encode()
        with self._lock:
            self._sock.sendall(data)

    def _recv_line(self) -> Dict:
        # Buffered line read: pull in chunks and split on newlines, instead
        # of one recv() syscall per byte (which dominated the old TCP path).
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            self._buf += chunk
        line, _sep, self._buf = self._buf.partition(b"\n")
        return json.loads(line) if line else {}

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
        gdb_unix_socket: Optional[str] = None,
        qmp_host: str = "localhost",
        qmp_port: int = 4444,
        qmp_unix_socket: Optional[str] = None,
        **kwargs: Any,
    ):
        self.config = config
        self.arch = arch
        self.qemu_path = qemu_path
        self.qemu_args = qemu_args or []
        self.gdb_unix_socket = gdb_unix_socket
        self.qmp_unix_socket = qmp_unix_socket
        # This is the INTERNAL control channel (halucinator -> QEMU's
        # gdbstub). A Unix socket is faster than loopback TCP. It is
        # independent of the EXTERNAL gdb server (avatar spawn_gdb_server on
        # gdb_server_port) that IDEs/VSCode attach to — that stays TCP.
        self._gdb = _GDBClient(gdb_host, gdb_port, arch=arch,
                               unix_path=gdb_unix_socket)
        # Prefer a Unix domain socket for QMP (much faster inject_irq
        # round-trips); fall back to TCP when no socket path is given.
        self._qmp = _QMPClient(qmp_host, qmp_port, unix_path=qmp_unix_socket)
        self._process: Optional[subprocess.Popen] = None
        self._bp_map: Dict[int, int] = {}   # bp_id → addr
        self._next_bp_id = 1
        self._regions: List[MemoryRegion] = []

        # Override the class-level ARM32 ABI helpers with the arch-specific
        # mixin's methods. ARM32 remains the default via inheritance so the
        # class is usable without __init__ (e.g. in unit tests).
        self._bind_abi(arch)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def launch(self) -> None:
        """Start QEMU and connect GDB + QMP.

        When we spawn QEMU ourselves the internal gdb + qmp control channels
        default to Unix domain sockets (faster than loopback TCP, no
        delayed-ACK tail latency, and we own both ends). Set
        ``HALUCINATOR_QEMU_TCP=1`` to force TCP instead. Explicitly passing
        ``gdb_unix_socket`` / ``qmp_unix_socket`` always wins."""
        # Only spawn QEMU if we were given a path AND the caller hasn't
        # already started one (some callers — e.g. the direct main path and
        # the libafl live test — pre-spawn QEMU themselves and set
        # ``_process`` before calling launch(); we must connect to that one,
        # not spawn a redundant second QEMU on a different endpoint).
        if self.qemu_path and self._process is None:
            force_tcp = bool(os.environ.get("HALUCINATOR_QEMU_TCP"))
            if not force_tcp:
                if self.gdb_unix_socket is None:
                    self.gdb_unix_socket = f"/tmp/hal-gdb-{self._gdb.port}.sock"
                    self._gdb.unix_path = self.gdb_unix_socket
                if self.qmp_unix_socket is None:
                    self.qmp_unix_socket = f"/tmp/hal-qmp-{self._qmp.port}.sock"
                    self._qmp.unix_path = self.qmp_unix_socket
            # Remove stale socket files so QEMU's bind (server,nowait) wins.
            for sp in (self.gdb_unix_socket, self.qmp_unix_socket):
                if sp:
                    try:
                        os.unlink(sp)
                    except OSError:
                        pass
            if self.qmp_unix_socket:
                qmp_arg = f"-qmp unix:{self.qmp_unix_socket},server,nowait"
            else:
                qmp_arg = (f"-qmp tcp:{self._qmp.host}:{self._qmp.port},"
                           f"server,nowait")
            if self.gdb_unix_socket:
                gdb_arg = f"-gdb unix:{self.gdb_unix_socket},server,nowait"
            else:
                gdb_arg = f"-gdb tcp::{self._gdb.port}"
            # Opt-in QEMU debug logging (CPU exceptions / resets) to a file,
            # for diagnosing guest faults that close the GDB connection.
            _dbg = os.environ.get("HAL_QEMU_LOG")
            dbg_args = (["-d", "int,cpu_reset,guest_errors,unimp", "-D", _dbg]
                        if _dbg else [])
            cmd = [self.qemu_path] + self.qemu_args + dbg_args + [
                "-S",  # start stopped
                gdb_arg,
                qmp_arg,
            ]
            log.info("Launching QEMU: %s", " ".join(cmd))
            self._process = subprocess.Popen(
                " ".join(cmd), shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            time.sleep(0.5)  # let QEMU initialize

        # Retry while QEMU's gdb endpoint comes up. TCP not-listening raises
        # ConnectionRefusedError; a Unix socket QEMU hasn't created yet raises
        # FileNotFoundError. Both mean "not ready" — newer QEMU (libafl 10.x)
        # can take >0.5 s to bind the socket, especially on a loaded CI host.
        retries = 10
        for i in range(retries):
            try:
                self._gdb.connect()
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if i == retries - 1:
                    raise
                time.sleep(0.5)

        # QMP is best-effort, but retry the same not-ready errors so a slow
        # QEMU start doesn't silently disable IRQ injection.
        for i in range(retries):
            try:
                self._qmp.connect()
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if i == retries - 1:
                    log.warning(
                        "QMP connection failed — IRQ injection will not work")
                else:
                    time.sleep(0.5)
            except OSError:
                log.warning(
                    "QMP connection failed — IRQ injection will not work")
                break

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

    def write_registers(self, regs: Dict[str, int]) -> None:
        """Batched register write — collapses execute_return's return-value +
        pc updates into a single GDB round-trip (see _GDBClient)."""
        self._gdb.write_registers(regs)

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

    # Canonical halucinator-native IRQ-injection QMP commands, each mapped
    # to the deprecated avatar-qemu name kept as a fallback. halucinator
    # prefers the hal-* name (the goal: QEMU forks that don't require
    # avatar); against an older build that only has avatar-*, we fall back
    # and remember. The cortex-m command (avatar-armv7m-inject-irq) is
    # called directly and isn't aliased here — it's unchanged across builds.
    _IRQ_CMD_ALIASES = {
        "hal-shadow-irq": "avatar-shadow-irq",
        "hal-arm-inject-irq": "avatar-arm-inject-irq",
        "hal-mips-inject-irq": "avatar-mips-inject-irq",
        "hal-ppc-inject-irq": "avatar-ppc-inject-irq",
        # x86/i386 is hal-only (no deprecated avatar-* predecessor).
        "hal-x86-inject-irq": None,
    }

    @staticmethod
    def _is_cmd_not_found(resp: Any) -> bool:
        return (isinstance(resp, dict)
                and isinstance(resp.get("error"), dict)
                and resp["error"].get("class") == "CommandNotFound")

    def _qmp_inject(self, command: str, arguments: Dict) -> Optional[Dict]:
        """Run an IRQ-injection QMP command, preferring the hal-* name and
        falling back to the deprecated avatar-* alias on older QEMU builds.

        QMP returns ``{"error": {"class": "CommandNotFound"}}`` for an
        unknown command (it doesn't raise). We try the canonical name; if
        it's absent and an avatar-* alias exists, we use that and cache the
        choice. If neither exists (a stock QEMU with no IRQ patches at all),
        we log one clear warning and short-circuit further attempts instead
        of silently delivering nothing."""
        if getattr(self, "_irq_qmp_unavailable", False):
            return None
        resolved = getattr(self, "_irq_cmd_resolved", None)
        cmd = resolved.get(command, command) if resolved else command
        resp = self._qmp.execute(cmd, arguments)
        if self._is_cmd_not_found(resp):
            alias = self._IRQ_CMD_ALIASES.get(command)
            if alias and alias != cmd:
                resp_alias = self._qmp.execute(alias, arguments)
                if not self._is_cmd_not_found(resp_alias):
                    if resolved is None:
                        self._irq_cmd_resolved = resolved = {}
                    resolved[command] = alias  # remember the working name
                    return resp_alias
            # Neither hal-* nor avatar-* exists — no IRQ patches at all.
            self._irq_qmp_unavailable = True
            log.warning(
                "QEMU build lacks the IRQ-injection QMP command %r (and its "
                "avatar-* alias) — IRQs will NOT be delivered to the target. "
                "Build QEMU with the halucinator IRQ overlay/patches, or use "
                "a backend that injects via the IrqController.", command)
        elif isinstance(resp, dict) and "error" in resp:
            log.warning("inject_irq: QMP %s failed: %r", cmd, resp["error"])
        return resp

    def inject_irq(self, irq_num: int) -> None:
        arch = getattr(self, "arch", None)
        # Cortex-M3 fast-path: avatar-qemu's NVIC-aware QMP command
        # integrates with watchman semantics. Add the 16-system-
        # exception offset since the QMP command takes a full
        # Cortex-M exception number.
        if arch == "cortex-m3":
            self._qmp_inject(
                "avatar-armv7m-inject-irq",
                {"num-irq": int(irq_num) + 16, "num-cpu": 0},
            )
            return
        # ARM/AArch64 fast-path: pulse the GIC's SPI line directly
        # via avatar-arm-inject-irq. Bypasses the GDB-write_memory
        # GIC_ISPENDR path which can race with the live target.
        if arch in ("arm", "arm64"):
            self._qmp_inject(
                "hal-arm-inject-irq",
                {"num-irq": int(irq_num), "num-cpu": 0},
            )
            return
        # x86/i386: deliver `irq_num` as a fixed interrupt vector to the
        # CPU's local APIC via hal-x86-inject-irq. The firmware enables
        # its LAPIC and has an IDT entry for the vector.
        if arch == "x86":
            self._qmp_inject(
                "hal-x86-inject-irq",
                {"num-irq": int(irq_num), "num-cpu": 0},
            )
            return
        # MIPS: prefer avatar-shadow-irq when the YAML provides
        # physical shadow-state addresses; falls back to
        # avatar-mips-inject-irq (Cause.IP pulse) otherwise.
        if arch == "mips":
            ctrl = getattr(self, "_irq_controller", None)
            irq_fired_phys = getattr(ctrl, "irq_fired_phys_addr", None)
            irq_number_phys = getattr(ctrl, "irq_number_phys_addr", None)
            if irq_fired_phys is not None and irq_number_phys is not None:
                self._qmp_inject(
                    "hal-shadow-irq",
                    {"number-addr": int(irq_number_phys),
                     "fired-addr":  int(irq_fired_phys),
                     "irq-num":     int(irq_num)},
                )
                return
            self._qmp_inject(
                "hal-mips-inject-irq",
                {"num-irq": int(irq_num), "num-cpu": 0},
            )
            return
        # PPC fast-path: avatar-qemu pulses env->irq_inputs[N] via
        # avatar-ppc-inject-irq. PowerPC's irq_inputs[] is sparse
        # (5-7 entries, name-indexed); halucinator's single-IRQ
        # API always pulses the canonical external INT slot:
        # 4 for e500v2 (PPCE500_INPUT_INT), 0 for Book3S PPC64.
        if arch in ("powerpc", "powerpc:MPC8XX", "ppc64"):
            # Shadow-write delivery via avatar-qemu's QMP avatar-shadow-irq
            # command: writes irq_number / irq_fired straight into the
            # firmware's RAM globals from the iothread, under BQL,
            # without going through the GDB stub. Sidesteps two
            # problems with the M-packet path: (a) QEMU's GDB stub
            # rejects writes while the CPU is running, and (b) the
            # halucinator dispatch loop's wait_for_stop on the same
            # GDB socket would race against the inject thread's stop
            # reply. The firmware's polling loop sees the flag flip on
            # its next iteration.
            ctrl = getattr(self, "_irq_controller", None)
            irq_fired_addr = getattr(ctrl, "irq_fired_addr", None)
            irq_number_addr = getattr(ctrl, "irq_number_addr", None)
            if irq_fired_addr is not None and irq_number_addr is not None:
                self._qmp_inject(
                    "hal-shadow-irq",
                    {"number-addr": int(irq_number_addr),
                     "fired-addr":  int(irq_fired_addr),
                     "irq-num":     int(irq_num)},
                )
                return
            # No shadow-state — fall back to QMP pulse (works only
            # on e500 where the racy pulse happens to land).
            self._qmp_inject(
                "hal-ppc-inject-irq",
                {"num-irq": 4 if arch != "ppc64" else 5, "num-cpu": 0},
            )
            return
        # Other arches use the IrqController via super().inject_irq
        # which writes MMIO through self.write_memory. GDB rejects
        # memory writes while the target is running, so stop+resume
        # around the IrqController call.
        try:
            self._gdb.stop()
            self._gdb.wait_for_stop(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            super().inject_irq(irq_num)
        finally:
            try:
                self._gdb.cont()
            except Exception:  # noqa: BLE001
                pass
