# Copyright 2026 Christopher Wright

"""Minimal stdlib Modbus/TCP probe: connect, send one request, print the reply.
Default = FC=03 Read Holding Registers (addr 0, qty 1). Also supports a raw
UMAS-style FC via --fc/--data hex. Proves the socket-layer bridge round-trips a
request through the firmware's real Port502Server.
"""
import argparse
import socket
import struct
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--fc", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--data", default="00000001",
                    help="hex PDU body after the FC byte (default = addr0 qty1)")
    ap.add_argument("--wait", type=float, default=8.0)
    args = ap.parse_args()

    pdu = bytes([args.fc]) + bytes.fromhex(args.data)
    txid = 0x0001
    frame = struct.pack(">HHHB", txid, 0, len(pdu) + 1, args.unit) + pdu

    try:
        s = socket.create_connection((args.host, args.port), timeout=args.wait)
    except Exception as e:  # noqa: BLE001
        print(f"PROBE: connect FAILED: {e}")
        return 2
    print(f"PROBE: connected to {args.host}:{args.port}")
    s.settimeout(args.wait)
    s.sendall(frame)
    print(f"PROBE: sent  {frame.hex()}  (FC=0x{args.fc:02x})")
    t0 = time.time()
    buf = b""
    try:
        while time.time() - t0 < args.wait:
            chunk = s.recv(512)
            if not chunk:
                print("PROBE: peer closed")
                break
            buf += chunk
            if len(buf) >= 7:
                _, _, length, _ = struct.unpack(">HHHB", buf[:7])
                if len(buf) >= 6 + length:
                    break
    except socket.timeout:
        pass
    s.close()
    if not buf:
        print("PROBE: NO RESPONSE (timeout)")
        return 1
    print(f"PROBE: recv  {buf.hex()}  ({len(buf)} bytes)")
    if len(buf) >= 8:
        txid_r, protid, length, unit = struct.unpack(">HHHB", buf[:7])
        rpdu = buf[7:7 + length - 1]
        fc = rpdu[0] if rpdu else None
        print(f"PROBE: MBAP txid={txid_r} prot={protid} len={length} unit={unit} "
              f"respFC=0x{fc:02x}" + (" (EXCEPTION)" if fc and fc & 0x80 else " (OK)"))
    print("PROBE: RESPONSE RECEIVED -- bridge round-trip works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
