#!/usr/bin/env python3
# Copyright 2026 Christopher Wright

"""bpv5_terminal — interactive terminal device for the Bus Pirate v5 demo.

Two-process model, following the same pattern as ``hal_dev_uart`` and the
STM32 UART example:

    # window 1
    halucinator -c test/firmware-rehosting/bpv5/bpv5_memory.yaml \\
                -c test/firmware-rehosting/bpv5/bpv5_config.yaml \\
                -c test/firmware-rehosting/bpv5/bpv5_addrs.yaml \\
                -n bpv5

    # window 2 (this script)
    python3 -m halucinator.external_devices.bpv5_terminal

This device:

* Subscribes to ``Peripheral.UTTYModel.tx_buf`` for firmware stdio (routed
  through libc.py's STDIO interface) and the BP5 interface (USB CDC).
  Mirrors both to the local terminal, preserving ANSI escapes so the
  Bus Pirate VT100 colour mode renders.
* Auto-answers the boot-time ``VT100 compatible color mode? (Y/n)>`` prompt
  with ``y\\r\\n``.
* Responds to the firmware's ESC[6n cursor-position query with the user's
  *actual* terminal size, so the firmware accepts VT100 mode rather than
  falling back to ASCII.
* Forwards keystrokes from local stdin (raw mode) to the firmware over
  ``Peripheral.UTTYModel.rx_char_or_buf``.
* Supports ``--script`` for scripted / CI mode, with ``--exit-on`` for
  success-detection and ``--max-runtime`` as a wall-clock bound.
"""
from __future__ import annotations

import argparse
import os
import re
import select
import sys
import threading
import time

try:
    import termios
    import tty
    HAS_TTY = True
except ImportError:
    HAS_TTY = False

from halucinator.external_devices.ioserver import IOServer


# ESC[6n — DSR (Device Status Report) request for cursor position. The
# firmware sends this to detect whether the terminal speaks VT100.
VT100_PROBE_RE = re.compile(rb"\x1b\[6n")

# Boot-time "VT100 compatible color mode? (Y/n)>" prompt.
VT100_YN_PROMPT_RE = re.compile(rb"VT100 compatible color mode\?")


def get_term_size() -> tuple[int, int]:
    """Return ``(rows, cols)``. Defaults to 24x80 when stdout isn't a TTY."""
    try:
        sz = os.get_terminal_size()
        return sz.lines, sz.columns
    except OSError:
        return 24, 80


def decode_escapes(s: str) -> bytes:
    """Resolve ``\\r``/``\\n``/``\\xHH`` shorthand in CLI strings."""
    return s.encode("utf-8").decode("unicode_escape").encode("latin-1")


def main() -> int:  # pylint: disable=too-many-statements,too-many-branches
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-r", "--rx-port", type=int, default=5556,
                   help="ZMQ port to SUB on (firmware tx flows here)")
    p.add_argument("-t", "--tx-port", type=int, default=5555,
                   help="ZMQ port to PUB on (firmware rx is read from here)")
    p.add_argument("--interface", default="BP5",
                   help="UTTY interface_id the firmware reads from")
    p.add_argument("--stdio-interface", default="STDIO",
                   help="UTTY interface_id that firmware printf publishes on")
    p.add_argument("--no-vt100", action="store_true",
                   help="don't respond to ESC[6n; firmware falls back to ASCII")
    p.add_argument("--no-auto-yn", action="store_true",
                   help="don't auto-answer the (Y/n) prompt with y")
    p.add_argument("--no-local-echo", action="store_true",
                   help="don't locally echo typed keys (the firmware doesn't "
                        "echo injected input, so local echo is on by default)")
    p.add_argument("--script", default="",
                   help=r"non-interactive: bytes to send after the ESC[6n probe "
                        r"(use \r \n \xHH for control bytes)")
    p.add_argument("--script-delay", type=float, default=1.0,
                   help="seconds after VT100 response before sending --script")
    p.add_argument("--exit-on", default="",
                   help="exit when this substring appears in firmware output")
    p.add_argument("--max-runtime", type=float, default=300.0,
                   help="hard cap on wall-clock runtime in scripted mode")
    p.add_argument("--prelude", default="y\\r\\n",
                   help=r"bytes to send once at startup, before any firmware "
                        r"output (default 'y\r\n' — the boot keystroke the "
                        r"bpv5 firmware reads to detect a connected terminal). "
                        r"Set --prelude '' to disable.")
    p.add_argument("--prelude-delay", type=float, default=0.2,
                   help="seconds to wait after first firmware output before "
                        "sending --prelude")
    args = p.parse_args()

    io = IOServer(rx_port=args.rx_port, tx_port=args.tx_port)

    state = {
        "done": False,
        "yn_answered": False,
        "vt100_responded": False,
        "script_sent": False,
        "prelude_sent": False,
        "tail": b"",
    }

    def send_bytes_(chars: bytes) -> None:
        io.send_msg(
            "Peripheral.UTTYModel.rx_char_or_buf",
            {"interface_id": args.interface, "char": list(chars)},
        )

    def display(raw: bytes) -> None:
        """Mirror firmware output to the user's terminal."""
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()

    def watch_for_prompts(raw: bytes) -> None:
        """Pattern-match accumulating output for auto-response triggers."""
        state["tail"] += raw
        if len(state["tail"]) > 4096:
            state["tail"] = state["tail"][-2048:]

        # 1) Auto-answer Y/n prompt
        if (not state["yn_answered"] and not args.no_auto_yn
                and VT100_YN_PROMPT_RE.search(state["tail"])):
            send_bytes_(b"y\r\n")
            state["yn_answered"] = True
            sys.stderr.write(
                "\n[bpv5_terminal] auto-answered VT100 Y/n prompt with y\n"
            )
            sys.stderr.flush()

        # 2) Respond to ESC[6n cursor-position query
        if (not state["vt100_responded"] and not args.no_vt100
                and VT100_PROBE_RE.search(state["tail"])):
            rows, cols = get_term_size()
            send_bytes_(f"\x1b[{rows};{cols}R".encode())
            state["vt100_responded"] = True
            sys.stderr.write(
                f"\n[bpv5_terminal] responded to ESC[6n with size {rows}x{cols}\n"
            )
            sys.stderr.flush()

            # Scripted mode: send the script after a settling delay.
            if args.script and not state["script_sent"]:
                state["script_sent"] = True

                def deferred_send() -> None:
                    time.sleep(args.script_delay)
                    chars = decode_escapes(args.script)
                    send_bytes_(chars)
                    sys.stderr.write(
                        f"[bpv5_terminal] sent script: {chars!r}\n"
                    )
                    sys.stderr.flush()

                threading.Thread(target=deferred_send, daemon=True).start()

        # 3) Exit marker
        if args.exit_on and args.exit_on.encode() in state["tail"]:
            sys.stderr.write(
                f"\n[bpv5_terminal] exit marker {args.exit_on!r} seen\n"
            )
            state["done"] = True

    def on_tx_buf(server: IOServer, msg: dict) -> None:  # pylint: disable=unused-argument
        # Accept output from any UTTY interface (STDIO + BP5).
        raw = msg.get("chars", b"")
        if isinstance(raw, list):
            raw = bytes(raw)
        elif not isinstance(raw, (bytes, bytearray)):
            return
        display(raw)
        watch_for_prompts(raw)

        # Send the prelude on first received message — that proves halucinator
        # has bound its SUB socket and won't drop the prelude in the
        # ZMQ slow-joiner window.
        if args.prelude and not state["prelude_sent"]:
            state["prelude_sent"] = True

            def deferred_prelude() -> None:
                time.sleep(args.prelude_delay)
                chars = decode_escapes(args.prelude)
                send_bytes_(chars)
                sys.stderr.write(
                    f"\n[bpv5_terminal] sent prelude {chars!r}\n"
                )
                sys.stderr.flush()

            threading.Thread(target=deferred_prelude, daemon=True).start()

    io.register_topic("Peripheral.UTTYModel.tx_buf", on_tx_buf)
    io.start()
    if not args.script:
        sys.stderr.write("[bpv5_terminal] interactive — press Ctrl-C or Ctrl-D "
                         "to quit\n")
    sys.stderr.write(
        f"[bpv5_terminal] subscribed to Peripheral.UTTYModel.tx_buf\n"
        f"[bpv5_terminal] launch halucinator with rx_port={args.tx_port}, "
        f"tx_port={args.rx_port}\n"
    )
    sys.stderr.flush()

    # Prelude is sent on first received tx_buf (see on_tx_buf above) so it
    # arrives after halucinator's SUB is bound — avoiding the ZMQ
    # slow-joiner window that would drop messages sent at startup.

    if args.script:
        deadline = time.time() + args.max_runtime
        while not state["done"] and time.time() < deadline:
            time.sleep(0.25)
        if not state["done"]:
            sys.stderr.write(
                f"\n[bpv5_terminal] timed out after {args.max_runtime}s\n"
            )
    else:
        # Interactive: raw-mode stdin → ZMQ.
        #
        # IMPORTANT: the firmware periodically emits ESC[6n (cursor-position
        # query). A real terminal auto-answers with ESC[<row>;<col>R — that
        # reply lands on OUR stdin and, if forwarded verbatim, gets injected
        # into the firmware's command line and corrupts the user's keystrokes
        # (typing `m` does nothing). We already answer ESC[6n ourselves in
        # watch_for_prompts(), so strip the terminal's own CPR replies here.
        # Arrow keys (ESC[A..D) and other real keypresses are NOT matched and
        # pass through untouched.
        CPR_RE = re.compile(rb"\x1b\[[0-9]*(?:;[0-9]*)?R")
        DANGLING_RE = re.compile(rb"\x1b(?:\[[0-9;]*)?$")  # partial seq at end
        pending = b""
        old_settings = None
        if HAS_TTY and sys.stdin.isatty():
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setraw(sys.stdin)
            # tty.setraw() clears OPOST (all output post-processing), which
            # disables ONLCR — so the firmware's bare '\n' no longer maps to
            # CR-LF and each line "stair-steps" rightward. We only need RAW
            # *input* (char-at-a-time, no echo, '\r' preserved for Enter);
            # re-enable output processing so multi-line firmware output (the
            # boot log, help text, etc.) renders with proper line returns.
            attrs = termios.tcgetattr(sys.stdin)
            attrs[1] |= (termios.OPOST | termios.ONLCR)   # oflag
            termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
        try:
            while not state["done"]:
                if not sys.stdin.isatty():
                    time.sleep(0.5)
                    continue
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    chunk = os.read(sys.stdin.fileno(), 256)
                    if not chunk:
                        break
                    pending += chunk
                    pending = CPR_RE.sub(b"", pending)       # drop CPR replies
                    # hold a possibly-incomplete escape seq for the next read
                    # so we never split a CPR across two reads
                    m = DANGLING_RE.search(pending)
                    out, pending = (pending[:m.start()], pending[m.start():]) \
                        if m else (pending, b"")
                    if b"\x03" in out or b"\x04" in out:  # Ctrl-C / Ctrl-D quit
                        sys.stderr.write("\n[bpv5_terminal] quit\n")
                        break
                    if out:
                        send_bytes_(out)
                        # Local echo: the firmware reads injected input via the
                        # rx_fifo bridge and does NOT echo it back (a real unit
                        # echoes from its USB-CDC read loop, which we bypass), so
                        # in raw mode nothing would appear as you type. Echo
                        # printable keys + CR/backspace ourselves. Control and
                        # escape bytes (arrow keys, etc.) are not echoed.
                        if not args.no_local_echo:
                            echo = bytearray()
                            for b in out:
                                if b in (0x0d, 0x0a):
                                    echo += b"\r\n"
                                elif b in (0x08, 0x7f):
                                    echo += b"\b \b"
                                elif 0x20 <= b < 0x7f:
                                    echo.append(b)
                            if echo:
                                sys.stdout.buffer.write(bytes(echo))
                                sys.stdout.buffer.flush()
        finally:
            if old_settings is not None:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    state["done"] = True
    io.shutdown()
    sys.stderr.write("\n[bpv5_terminal] done\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
