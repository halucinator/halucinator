# Copyright 2026 Christopher Wright

"""ModbusTcpBridge — TCP server on host port 502, delivers Modbus PDUs
to a configurable firmware handler.

Implements the "rehost the application layer" approach for an
ARM/VxWorks PLC (and similar VxWorks Modbus/TCP devices):

  the PLC configurator
         |
         |  Modbus/TCP (port 502)
         v
  +-----------------------------+
  |   halucinator host TCP-502  |
  |        server               |
  +-----------------------------+
         |
         |  strip MBAP header (txid, protid, len, unit)
         |  build ModbusDiagnostics::CommandIn in halucinator scratch
         v
  +-----------------------------+
  |  ModbusTcpBridge.dispatch   |
  |  - writes the CommandIn     |
  |    into firmware memory     |
  |  - sets r0 = &CommandIn      |
  |  - sets PC = configured      |
  |    handler entry             |
  |  - sets LR = trigger PC      |
  |    (so handler returns to    |
  |    a known safe PC)          |
  +-----------------------------+
         |
         v
  Firmware: handler reads CommandIn, builds response, returns

When the trigger PC fires again (handler returned to it), the bridge
reads the response from the configured response-region and sends it
back over TCP wrapped in MBAP.

YAML configuration usage:
  - class: halucinator.bp_handlers.ModbusTcpBridge
    function: modbus_tcp_bridge
    addr: 0x23ff7300                 # trigger park — when PC reaches
                                     # here, the bridge takes over
    registration_args:
      tcp_port:      502             # host listen port
      cmd_in_addr:   0x04700000      # scratch where CommandIn is built
      response_addr: 0x04700200      # scratch where firmware writes response
      cmd_in_size:   0x100           # bytes to wipe before each request
      handler_pc:    0x2016f088      # firmware function to call with r0=&CommandIn
      return_pc:     0x23ff7300      # handler returns here; bridge resumes
      buffer_field_offset: 0x40      # where in CommandIn the PDU buffer sits
                                     # (depends on the firmware's struct layout;
                                     #  TODO once decompiled — for now, MBAP-stripped
                                     #  PDU is written here)
      length_field_offset: 0x44      # where the PDU length is stored
      response_field_offset: 0x60    # where the firmware writes the response
      response_length_offset: 0x64   # where firmware writes response length
      silent: false                  # log each request/response

Notes:
- The exact ModbusDiagnostics::CommandIn struct layout is firmware-
  specific and not yet fully recovered for the target (Ghidra labels for
  EthChannelServer::getWebMessaging were misaligned). The above field
  offsets are placeholders — adjust per the target firmware.
- This handler is "single-in-flight" — one client at a time. For
  the PLC configurator that's fine; the configurator opens one TCP connection.
- The handler runs IN A BACKGROUND THREAD (host TCP server) and
  injects state into the emulator at the BP trigger. The trigger PC
  must be reached periodically (e.g., via the IRQ rotation park
  mechanism) for new requests to be processed.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, cast

from halucinator.bp_handlers.bp_handler import BPHandler, HandlerFunction, HandlerReturn, bp_handler
from halucinator import hal_log

if TYPE_CHECKING:
    from halucinator.backends.hal_backend import HalBackend

log = logging.getLogger(__name__)
hlog = hal_log.getHalLogger()


# MBAP header is 7 bytes: txid(2), protid(2), len(2), unit(1)
MBAP_LEN = 7


class _PendingRequest:
    """Captures a Modbus request waiting for the next BP firing."""
    __slots__ = ("client_sock", "mbap_txid", "mbap_protid", "mbap_unit", "pdu")

    def __init__(self, client_sock: socket.socket, mbap_txid: int,
                 mbap_protid: int, mbap_unit: int, pdu: bytes) -> None:
        self.client_sock = client_sock
        self.mbap_txid = mbap_txid
        self.mbap_protid = mbap_protid
        self.mbap_unit = mbap_unit
        self.pdu = pdu


class ModbusTcpBridge(BPHandler):
    """TCP-502 listener that bridges Modbus/TCP frames to a firmware
    handler. See module docstring for the recipe."""

    def __init__(self) -> None:
        self.cfg: Dict[int, Dict[str, Any]] = {}
        self.func_names: Dict[int, str] = {}
        # Per-trigger pending request queue + lock
        self._pending: Dict[int, Optional[_PendingRequest]] = {}
        self._lock = threading.Lock()
        # State machine per trigger: "idle" → "request-pending" → "response-pending"
        self._state: Dict[int, str] = {}

    def register_handler(
        self, qemu: "HalBackend", addr: int, func_name: str,
        tcp_port: int = 502,
        cmd_in_addr: int = 0x04700000,
        response_addr: int = 0x04700200,
        cmd_in_size: int = 0x100,
        handler_pc: int = 0,
        return_pc: int = 0,
        buffer_field_offset: int = 0x40,
        length_field_offset: int = 0x44,
        response_field_offset: int = 0x60,
        response_length_offset: int = 0x64,
        silent: bool = False,
        mode: str = "dispatch",
        regs: int = 256,
        coils: int = 256,
        device_id_vendor: str = "Vendor",
        device_id_product: str = "PLC",
        device_id_revision: str = "rehost",
        uart_pty_path: str = "/tmp/vxworks_uart_pty",
        uart_unit_id: int = 1,
        uart_response_timeout: float = 3.0,
        uart_idle_gap: float = 0.05,
    ) -> HandlerFunction:  # pylint: disable=too-many-arguments
        self.cfg[addr] = {
            "tcp_port": tcp_port,
            "cmd_in_addr": cmd_in_addr,
            "response_addr": response_addr,
            "cmd_in_size": cmd_in_size,
            "handler_pc": handler_pc,
            "return_pc": return_pc or addr,   # default: handler returns to trigger PC
            "buffer_field_offset": buffer_field_offset,
            "length_field_offset": length_field_offset,
            "response_field_offset": response_field_offset,
            "response_length_offset": response_length_offset,
            "silent": silent,
            "mode": mode,
            "holding_regs": [0] * regs,
            "input_regs":   [0] * regs,
            "coils":        [False] * coils,
            "discrete":     [False] * coils,
            "device_id_vendor":   device_id_vendor,
            "device_id_product":  device_id_product,
            "device_id_revision": device_id_revision,
            # uart_rtu mode: route MBAP TCP traffic through the firmware's
            # Modbus RTU UART path (DBGU pty link from phase 54).
            "uart_pty_path":         uart_pty_path,
            "uart_unit_id":          uart_unit_id,
            "uart_response_timeout": uart_response_timeout,
            "uart_idle_gap":         uart_idle_gap,
            "uart_fd":               None,    # opened lazily on first request
        }
        self.func_names[addr] = func_name
        self._pending[addr] = None
        self._state[addr] = "idle"

        # Spawn TCP listener thread for this trigger
        t = threading.Thread(target=self._tcp_listener, args=(addr,),
                             daemon=True,
                             name=f"{func_name}-tcp-{tcp_port}")
        t.start()
        hlog.info("ModbusTcpBridge(%s): listening on tcp/%d, trigger PC=0x%08x, "
                  "handler=0x%08x", func_name, tcp_port, addr, handler_pc)
        return cast(HandlerFunction, ModbusTcpBridge.on_trigger)

    # --------------------- TCP listener (host side) ---------------------

    def _tcp_listener(self, trigger_addr: int) -> None:
        """Accept clients, parse Modbus/TCP frames, enqueue requests."""
        cfg = self.cfg[trigger_addr]
        port = cfg["tcp_port"]
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(1)
        except Exception as e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge: listen(%d) failed: %s", port, e)
            return

        while True:
            try:
                client, peer = srv.accept()
                hlog.info("ModbusTcpBridge: client connected from %s:%d",
                          peer[0], peer[1])
                self._serve_client(trigger_addr, client)
                try:
                    client.close()
                except Exception:
                    pass
                hlog.info("ModbusTcpBridge: client %s disconnected", peer[0])
            except Exception as e:  # noqa: BLE001
                hlog.error("ModbusTcpBridge: accept failed: %s", e)
                break

    def _serve_client(self, trigger_addr: int, client: socket.socket) -> None:
        """Read MBAP-framed Modbus requests; for each: enqueue and wait
        for the bridge to deliver a response."""
        cfg = self.cfg[trigger_addr]
        while True:
            mbap = self._recv_n(client, MBAP_LEN)
            if mbap is None:
                return
            txid, protid, length, unit = struct.unpack(">HHHB", mbap)
            pdu_len = length - 1  # length includes unit byte
            if pdu_len < 1 or pdu_len > 253:
                hlog.error("ModbusTcpBridge: bad PDU length %d", pdu_len)
                return
            pdu = self._recv_n(client, pdu_len)
            if pdu is None:
                return

            hlog.info("ModbusTcpBridge: req txid=%d unit=%d pdu=%s",
                      txid, unit, pdu.hex())

            mode = cfg.get("mode", "dispatch")
            pending = _PendingRequest(client, txid, protid, unit, pdu)
            if mode == "responder":
                # Generate the response locally; firmware doesn't see it.
                resp_pdu = self._responder_handle(cfg, pdu)
                self._send_response(pending, resp_pdu)
                continue
            if mode == "uart_rtu":
                # Route through the firmware's Modbus RTU UART path:
                # wrap as RTU, write to pty (firmware UART RX), read
                # response off pty (firmware UART TX), unwrap.
                resp_pdu = self._uart_rtu_exchange(cfg, pdu)
                self._send_response(pending, resp_pdu)
                continue

            # Dispatch mode: enqueue and wait for the BP trigger to fire +
            # the firmware to produce a response.
            with self._lock:
                self._pending[trigger_addr] = _PendingRequest(
                    client, txid, protid, unit, pdu)
                self._state[trigger_addr] = "request-pending"
            self._wait_until_idle(trigger_addr, client)

    def _wait_until_idle(self, trigger_addr: int, client: socket.socket) -> None:
        """Block until the BP handler clears the pending request."""
        import time
        while True:
            with self._lock:
                state = self._state[trigger_addr]
            if state == "idle":
                return
            # Make sure the client is still alive
            try:
                client.settimeout(0.05)
                _ = client.recv(0, socket.MSG_PEEK)
            except (BlockingIOError, socket.timeout):
                pass
            except Exception:
                with self._lock:
                    self._pending[trigger_addr] = None
                    self._state[trigger_addr] = "idle"
                return
            time.sleep(0.02)

    @staticmethod
    def _recv_n(sock: socket.socket, n: int) -> Optional[bytes]:
        """Receive exactly n bytes; return None on EOF/error."""
        buf = bytearray()
        sock.settimeout(None)
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except Exception:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    # --------------------- BP handler (emulator side) ---------------------

    @bp_handler
    def on_trigger(self, qemu: "HalBackend", addr: int) -> HandlerReturn:
        """Called when PC reaches the trigger park. Drives the request/
        response state machine for one TCP client."""
        cfg = self.cfg[addr]
        with self._lock:
            state = self._state[addr]
            pending = self._pending[addr]

        if state == "idle":
            # Nothing to do; let execution continue past the trigger
            return False, None

        if state == "request-pending" and pending is not None:
            # Build CommandIn in scratch and dispatch into handler
            try:
                self._dispatch_request(qemu, addr, cfg, pending)
            except Exception as e:  # noqa: BLE001
                hlog.error("ModbusTcpBridge: dispatch failed: %s", e)
                with self._lock:
                    self._state[addr] = "idle"
                    self._pending[addr] = None
                return False, None
            # The trigger PC is now set to handler_pc; the handler will
            # eventually return back to addr, at which point we'll be
            # in "response-pending" state.
            with self._lock:
                self._state[addr] = "response-pending"
            # Don't SkipFunc — the emulator now needs to RUN the handler.
            # We've already pointed PC at handler_pc + set LR = addr.
            return False, None

        if state == "response-pending" and pending is not None:
            # Handler returned. Read response from scratch and send it back.
            try:
                resp_pdu = self._read_response(qemu, cfg)
                self._send_response(pending, resp_pdu)
            except Exception as e:  # noqa: BLE001
                hlog.error("ModbusTcpBridge: read/send failed: %s", e)
            with self._lock:
                self._state[addr] = "idle"
                self._pending[addr] = None
            return False, None

        return False, None

    def _dispatch_request(self, qemu: "HalBackend", trigger_addr: int,
                          cfg: Dict[str, Any], pending: _PendingRequest) -> None:
        """Build CommandIn in scratch memory and redirect PC to handler."""
        cmd_in_addr = cfg["cmd_in_addr"]
        cmd_in_size = cfg["cmd_in_size"]
        buf_off  = cfg["buffer_field_offset"]
        len_off  = cfg["length_field_offset"]

        # Wipe the CommandIn region
        zero = bytes(cmd_in_size)
        try:
            qemu.write_memory(cmd_in_addr, 1, 0, num_words=cmd_in_size, raw=False)
        except Exception:
            # Fall back to multi-byte write
            try:
                qemu.write_memory_bytes(cmd_in_addr, zero)  # type: ignore[attr-defined]
            except Exception:
                pass

        # Write PDU into the buffer field
        pdu = pending.pdu
        try:
            for i, b in enumerate(pdu):
                qemu.write_memory(cmd_in_addr + buf_off + i, 1, b)
        except Exception as _e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge: write PDU bytes failed: %s", _e)

        # Write length
        try:
            qemu.write_memory(cmd_in_addr + len_off, 4, len(pdu))
        except Exception as _e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge: write PDU length failed: %s", _e)

        # Set r0 = &CommandIn, LR = return_pc, PC = handler_pc
        try:
            qemu.write_register("r0", cmd_in_addr & 0xffffffff)
            qemu.write_register("lr", cfg["return_pc"] & 0xffffffff)
            qemu.write_register("pc", cfg["handler_pc"] & 0xffffffff)
        except Exception as _e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge: register set failed: %s", _e)
            return

        if not cfg["silent"]:
            hlog.info("ModbusTcpBridge: dispatched -- CommandIn @ 0x%08x  "
                      "PDU len=%d  handler PC=0x%08x  return_pc=0x%08x",
                      cmd_in_addr, len(pdu), cfg["handler_pc"], cfg["return_pc"])

    def _read_response(self, qemu: "HalBackend", cfg: Dict[str, Any]) -> bytes:
        """Read the firmware-written response from the configured
        response region."""
        resp_addr   = cfg["response_addr"]
        resp_off    = cfg["response_field_offset"]
        resp_len_off = cfg["response_length_offset"]
        # Read length first
        try:
            length_bytes = qemu.read_memory(cfg["cmd_in_addr"] + resp_len_off, 4, 1)
            if isinstance(length_bytes, (bytes, bytearray)):
                length = struct.unpack("<I", bytes(length_bytes[:4]))[0]
            else:
                length = int(length_bytes) & 0xffffffff
        except Exception:
            length = 0
        length = max(0, min(length, 253))

        # Read response bytes
        resp = bytearray()
        for i in range(length):
            try:
                b = qemu.read_memory(cfg["cmd_in_addr"] + resp_off + i, 1, 1)
                if isinstance(b, (bytes, bytearray)):
                    resp.append(b[0])
                else:
                    resp.append(int(b) & 0xff)
            except Exception:
                break
        return bytes(resp)

    # --------------------- UART RTU passthrough ---------------------
    #
    # `mode: uart_rtu` MBAP-to-RTU bridge. The frame written to the pty
    # link is consumed by the firmware's serial RX path (At91Dbgu's
    # feed_rx, then iosLib_tyRead -> M_UART_Manual_Handler @ 0x2013a554
    # for the target). The firmware's Modbus RTU state machine processes
    # the frame and writes the response to the same UART TX; the pty
    # echoes those bytes back to the host, where we drain them, strip
    # the RTU envelope and forward the PDU as an MBAP response.

    _CRC16_TABLE: Optional[Tuple[int, ...]] = None

    @classmethod
    def _crc16_table(cls) -> Tuple[int, ...]:
        if cls._CRC16_TABLE is None:
            tbl = []
            for b in range(256):
                v = b
                for _ in range(8):
                    v = (v >> 1) ^ 0xA001 if (v & 1) else v >> 1
                tbl.append(v)
            cls._CRC16_TABLE = tuple(tbl)
        return cls._CRC16_TABLE

    @classmethod
    def _crc16(cls, data: bytes) -> int:
        tbl = cls._crc16_table()
        crc = 0xFFFF
        for b in data:
            crc = (crc >> 8) ^ tbl[(crc ^ b) & 0xff]
        return crc & 0xffff

    def _uart_open(self, cfg: Dict[str, Any]) -> Optional[int]:
        """Lazy-open the pty link to the firmware's UART."""
        if cfg.get("uart_fd") is not None:
            return cfg["uart_fd"]
        import os
        path = cfg["uart_pty_path"]
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except FileNotFoundError:
            hlog.error("ModbusTcpBridge[uart_rtu]: pty %s not present "
                       "(is At91Dbgu's bridge_mode=pty? did firmware open it?)",
                       path)
            return None
        except Exception as e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge[uart_rtu]: open(%s) failed: %s",
                       path, e)
            return None
        cfg["uart_fd"] = fd
        return fd

    def _uart_rtu_exchange(self, cfg: Dict[str, Any], pdu: bytes) -> bytes:
        """Encode PDU as RTU, write to firmware UART RX, read response,
        strip RTU. Returns the response PDU (with FC error byte set on
        any failure)."""
        import os, select, time
        fd = self._uart_open(cfg)
        if fd is None:
            return self._modbus_exception(pdu[0] if pdu else 0, 0x0B)  # GW_TARGET
        unit = cfg["uart_unit_id"] & 0xff
        rtu_frame = bytes([unit]) + pdu
        crc = self._crc16(rtu_frame)
        rtu_frame += bytes([crc & 0xff, (crc >> 8) & 0xff])

        # Drain any stale RX bytes
        deadline_drain = time.time() + 0.05
        try:
            while time.time() < deadline_drain:
                r, _, _ = select.select([fd], [], [], 0)
                if not r:
                    break
                try:
                    os.read(fd, 4096)
                except (BlockingIOError, OSError):
                    break
        except Exception:
            pass

        # Write RTU frame
        try:
            written = 0
            while written < len(rtu_frame):
                _, w, _ = select.select([], [fd], [], 1.0)
                if not w:
                    return self._modbus_exception(pdu[0], 0x0B)
                n = os.write(fd, rtu_frame[written:])
                if n <= 0:
                    return self._modbus_exception(pdu[0], 0x0B)
                written += n
        except Exception as e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge[uart_rtu]: write failed: %s", e)
            return self._modbus_exception(pdu[0], 0x0B)

        # Read response with 3.5-char silent-gap heuristic
        timeout = cfg["uart_response_timeout"]
        idle = cfg["uart_idle_gap"]
        deadline = time.time() + timeout
        rx = bytearray()
        last_byte_at: Optional[float] = None
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    chunk = os.read(fd, 4096)
                except (BlockingIOError, OSError):
                    chunk = b""
                if chunk:
                    rx.extend(chunk)
                    last_byte_at = time.time()
                    continue
            # No new data: if we got at least an RTU response (>=4 bytes:
            # unit + FC + min_payload + 2*CRC) and have been idle for the
            # configured gap, we're done.
            if last_byte_at is not None and len(rx) >= 4 \
                    and time.time() - last_byte_at >= idle:
                break

        if len(rx) < 4:
            return self._modbus_exception(pdu[0], 0x0B)  # gateway path-target

        rx_bytes = bytes(rx)
        # Strip RTU envelope: unit(1) + PDU(n) + CRC(2)
        body = rx_bytes[:-2]
        crc_rx = rx_bytes[-2] | (rx_bytes[-1] << 8)
        if self._crc16(body) != crc_rx:
            hlog.warning("ModbusTcpBridge[uart_rtu]: bad CRC on RTU response "
                         "(got 0x%04x); returning frame anyway", crc_rx)
        if body[0] != unit:
            hlog.warning("ModbusTcpBridge[uart_rtu]: unit-id mismatch "
                         "(expected %d, got %d)", unit, body[0])
        return body[1:]  # strip unit; return PDU

    # --------------------- Local Modbus responder ---------------------
    #
    # `mode: responder` answers Modbus FC=01/02/03/04/05/06/0F/10/2B locally
    # from the configured simulated register table. The rehosted PLC
    # firmware keeps running underneath (the unicorn loop ticks, the
    # scheduler context-switches), so the PLC configurator sees a live PLC on
    # port 502 even before the firmware-side Modbus dispatch chain is
    # fully reverse-engineered. Switching `mode: dispatch` once the
    # processModbusMessage entry is known re-routes traffic through the
    # firmware.

    @staticmethod
    def _modbus_exception(fc: int, ec: int) -> bytes:
        return bytes([fc | 0x80, ec & 0xff])

    def _responder_handle(self, cfg: Dict[str, Any], pdu: bytes) -> bytes:
        """Generate a Modbus PDU response for the given request PDU."""
        if not pdu:
            return self._modbus_exception(0, 0x03)  # ILLEGAL_DATA
        fc = pdu[0]
        try:
            if fc == 0x01:  # Read Coils
                addr, qty = struct.unpack(">HH", pdu[1:5])
                if qty < 1 or qty > 2000:
                    return self._modbus_exception(fc, 0x03)
                bits = cfg["coils"]
                if addr + qty > len(bits):
                    return self._modbus_exception(fc, 0x02)
                n = (qty + 7) // 8
                out = bytearray(n)
                for i in range(qty):
                    if bits[addr + i]:
                        out[i >> 3] |= 1 << (i & 7)
                return bytes([fc, n]) + bytes(out)
            if fc == 0x02:  # Read Discrete Inputs
                addr, qty = struct.unpack(">HH", pdu[1:5])
                if qty < 1 or qty > 2000:
                    return self._modbus_exception(fc, 0x03)
                bits = cfg["discrete"]
                if addr + qty > len(bits):
                    return self._modbus_exception(fc, 0x02)
                n = (qty + 7) // 8
                out = bytearray(n)
                for i in range(qty):
                    if bits[addr + i]:
                        out[i >> 3] |= 1 << (i & 7)
                return bytes([fc, n]) + bytes(out)
            if fc in (0x03, 0x04):  # Read Holding / Input Registers
                addr, qty = struct.unpack(">HH", pdu[1:5])
                if qty < 1 or qty > 125:
                    return self._modbus_exception(fc, 0x03)
                regs = cfg["holding_regs"] if fc == 0x03 else cfg["input_regs"]
                if addr + qty > len(regs):
                    return self._modbus_exception(fc, 0x02)
                payload = bytearray()
                for v in regs[addr:addr + qty]:
                    payload += struct.pack(">H", v & 0xffff)
                return bytes([fc, len(payload)]) + bytes(payload)
            if fc == 0x05:  # Write Single Coil
                addr, val = struct.unpack(">HH", pdu[1:5])
                if val not in (0x0000, 0xff00):
                    return self._modbus_exception(fc, 0x03)
                if addr >= len(cfg["coils"]):
                    return self._modbus_exception(fc, 0x02)
                cfg["coils"][addr] = (val == 0xff00)
                return pdu[:5]  # echo
            if fc == 0x06:  # Write Single Register
                addr, val = struct.unpack(">HH", pdu[1:5])
                if addr >= len(cfg["holding_regs"]):
                    return self._modbus_exception(fc, 0x02)
                cfg["holding_regs"][addr] = val & 0xffff
                return pdu[:5]  # echo
            if fc == 0x0F:  # Write Multiple Coils
                addr, qty, bc = struct.unpack(">HHB", pdu[1:6])
                if qty < 1 or qty > 1968 or bc != (qty + 7) // 8:
                    return self._modbus_exception(fc, 0x03)
                if addr + qty > len(cfg["coils"]):
                    return self._modbus_exception(fc, 0x02)
                data = pdu[6:6 + bc]
                for i in range(qty):
                    cfg["coils"][addr + i] = bool(data[i >> 3] & (1 << (i & 7)))
                return bytes([fc]) + struct.pack(">HH", addr, qty)
            if fc == 0x10:  # Write Multiple Registers
                addr, qty, bc = struct.unpack(">HHB", pdu[1:6])
                if qty < 1 or qty > 123 or bc != qty * 2:
                    return self._modbus_exception(fc, 0x03)
                if addr + qty > len(cfg["holding_regs"]):
                    return self._modbus_exception(fc, 0x02)
                for i in range(qty):
                    cfg["holding_regs"][addr + i] = struct.unpack(
                        ">H", pdu[6 + i * 2:8 + i * 2])[0]
                return bytes([fc]) + struct.pack(">HH", addr, qty)
            if fc == 0x17:  # Read/Write Multiple Registers
                raddr, rqty, waddr, wqty, bc = struct.unpack(
                    ">HHHHB", pdu[1:10])
                if (rqty < 1 or rqty > 125 or wqty < 1 or wqty > 121
                        or bc != wqty * 2):
                    return self._modbus_exception(fc, 0x03)
                regs = cfg["holding_regs"]
                if raddr + rqty > len(regs) or waddr + wqty > len(regs):
                    return self._modbus_exception(fc, 0x02)
                for i in range(wqty):
                    regs[waddr + i] = struct.unpack(
                        ">H", pdu[10 + i * 2:12 + i * 2])[0]
                payload = bytearray()
                for v in regs[raddr:raddr + rqty]:
                    payload += struct.pack(">H", v & 0xffff)
                return bytes([fc, len(payload)]) + bytes(payload)
            if fc == 0x2B and len(pdu) >= 3 and pdu[1] == 0x0E:
                # Read Device Identification (MEI type 0x0E)
                rd_code = pdu[2]
                obj_id = pdu[3] if len(pdu) >= 4 else 0
                # Build a Basic device-ID response (3 objects: vendor, product, revision)
                objs = [
                    (0x00, cfg["device_id_vendor"].encode("ascii", "replace")),
                    (0x01, cfg["device_id_product"].encode("ascii", "replace")),
                    (0x02, cfg["device_id_revision"].encode("ascii", "replace")),
                ]
                more = 0
                conformity = 0x01  # Basic, stream-only
                payload = bytearray([fc, 0x0E, rd_code, conformity, more, 0,
                                     len(objs)])
                for oid, val in objs:
                    payload += bytes([oid, len(val)]) + val
                return bytes(payload)
            # Unknown / unsupported function code
            return self._modbus_exception(fc, 0x01)  # ILLEGAL_FUNCTION
        except struct.error:
            return self._modbus_exception(fc, 0x03)  # ILLEGAL_DATA_VALUE

    def _send_response(self, pending: _PendingRequest, resp_pdu: bytes) -> None:
        """Wrap the PDU in MBAP and send to the original client."""
        if not resp_pdu:
            # Build a Modbus error response: function | 0x80, exception code 0x04
            resp_pdu = bytes([(pending.pdu[0] if pending.pdu else 0) | 0x80, 0x04])
        mbap = struct.pack(">HHHB",
                           pending.mbap_txid, pending.mbap_protid,
                           len(resp_pdu) + 1, pending.mbap_unit)
        frame = mbap + resp_pdu
        try:
            pending.client_sock.sendall(frame)
        except Exception as _e:  # noqa: BLE001
            hlog.error("ModbusTcpBridge: send failed: %s", _e)
