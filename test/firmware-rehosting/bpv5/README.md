<!-- Copyright 2026 Christopher Wright -->

# Bus Pirate v5 (RP2040) Emulation Example

This example demonstrates how to re-host the Bus Pirate v5 (bpv5) firmware using HALucinator. The bpv5 is based on the Raspberry Pi RP2040 microcontroller.

## Status & companion docs

**Full device: all interfaces working.** The firmware boots to an interactive
colour `HiZ>` shell on unicorn (macOS), and every bus mode + always-on
peripheral is driven end-to-end against a **modeled target device** — 15/15
scripted tests pass on one merged config (tag `bpv5-full-device-20260614`).

| Working interface | Modeled target → result through the CLI |
|---|---|
| SPI (#6)      | W25Q128 NOR flash → `RX: 0xEF 0x40 0x18` (JEDEC) |
| I2C (#5)      | 24C02 EEPROM → `RX: 0x00 ACK 0x01 NACK` |
| 1-WIRE (#2)   | DS18B20 → `Temperature: 25.062` (CRC8 valid) |
| UART (#3)     | serial peer → `RX: 0x41`; NMEA GPS → `fix quality: 1` |
| 2WIRE (#7) / 3WIRE (#8) | smartcard/Microwire → `00 01 02 03` / `93 C4 6E 5A` |
| JTAG (#12)    | ARM scan chain → `Device 0: 0x4BA00477` |
| INFRARED (#11)| NEC remote → TX `0x0804` + RX decode `Addr 4 Cmd 8` |
| DIO (#9)      | per-pin GPIO → drive + read-back |
| LED (#10)     | WS2812 strip → pixel `0x80FF00` captured |
| LCD           | ST7789 → captured drawn text (`Vout`, `IO0..IO7`, …) |
| ADC/PSU/AMUX  | `v`/`W` → modeled voltages (Vout 3.3 V) |
| SPI-NAND      | FatFs → `ls`/`cat` a modeled filesystem |
| Scope (#14)   | `d 2` → modeled DC waveform |

BINLOOP (#13) is unreachable headless (core1-only service on a separate
USB-CDC interface) and is documented, not faked — see `INTERFACES.md`.

- **▶ [`DEMO.md`](DEMO.md) — start here:** the live web panel (terminal + LCD +
  device tiles in one browser tab), how to launch the interactive terminal, and
  a type-this-expect-this walkthrough for every interface.
- [`INTERFACES.md`](INTERFACES.md) — every bus mode + peripheral, its
  controller/MMIO, firmware entry points, and how each is modeled.

Each interface is high-level-emulated (HLE) at its software leaf helper
(SPI uses the real PL022 SSP; every PIO-bit-banged mode hooks the per-byte/
per-frame C helper and answers from a Python model).

## Provenance
The third-party binaries shipped here are redistributed under their upstream
licenses; see [`PROVENANCE.md`](PROVENANCE.md) for sources, copyright
holders, and rebuild instructions, and [`licenses/`](licenses/) for the
upstream `LICENSE.TXT` files.
*   **Binary:** `bus_pirate5_rev10.bin` — MIT, DangerousPrototypes/BusPirate5-firmware
*   **Bootrom:** `b2.bin` — BSD-3-Clause, raspberrypi/pico-bootrom-rp2040

## Emulation Challenges & Solutions

### 1. RP2040 Hardware Dependencies
Unlike simpler Cortex-M chips, the RP2040 boot sequence and the Bus Pirate firmware have strict hardware checks that would normally stall a generic emulator.

*   **Bootrom Mapping:** The firmware makes dynamic calls to library functions stored in the RP2040 bootrom (e.g., optimized `memcpy`, floating-point math). We mapped the official `b2.bin` at `0x0` to satisfy these lookups.
*   **SIO and Spinlocks:** The chip uses Single-cycle IO (SIO) at `0xd0000000` for core identification and hardware spinlocks. We mapped this region as **RAM** rather than a peripheral model. This provides "sticky" register behavior, allowing the firmware to successfully "claim" locks and proceed past the `_reset_handler`.
*   **Peripheral Spoofing:** To avoid implementing complex SPI, ADC, and LCD models, we mapped their MMIO ranges (`0x18...`, `0x40...`) as RAM and added `SkipFunc` intercepts for their initialization routines (`spi_init`, `lcd_configure`, etc.).

### 2. High-Level Emulation (HLE) Intercepts
We leveraged HALucinator's intercept system to "short-circuit" hardware-heavy loops:
*   **Time Compression:** `busy_wait_ms` and `sleep_ms` were skipped to make the boot sequence near-instant.
*   **Multicore Bypass:** RP2040 is dual-core. HALucinator currently targets single-threaded execution. We intercepted `multicore_launch_core1` to keep the firmware running exclusively on Core 0.
*   **USB Connection Spoofing:** The Bus Pirate waits for a USB CDC connection before entering the command loop. We intercepted `tud_cdc_n_connected` to always return `True` (1).

### 3. I/O Redirection
*   **Flexible Printf:** The Bus Pirate uses `SEGGER_RTT_printf`, which places the format string as the second argument. The core `halucinator.bp_handlers.generic.libc.Libc6` handler accepts a `fmt_idx` registration argument so any `printf`-family wrapper whose format string isn't at arg 0 — `SEGGER_RTT_printf`, `fprintf`/`dprintf`, `snprintf` (`fmt_idx: 2`), vendor logging macros, etc. — can be intercepted by the same class.
*   **Console:** `rx_fifo_try_get` / `tud_cdc_n_write` / `tud_cdc_n_write_flush` are bridged into a `UTTYModel` "BP5" interface by `BusPirateConsole`. Keystrokes are delivered over ZMQ by `bpv5_terminal.py` — the external terminal device that also handles the VT100 cursor-position probe (see "How to Run" below).

## Core Library Changes
To support this example (and external-device integration generally), the
following changes were made to the HALucinator core:
*   **`src/halucinator/bp_handlers/generic/libc.py`**: `Libc6.printf` and
    `Libc6.puts` publish formatted output via `Peripheral.UTTYModel.tx_buf`
    (registering a "STDIO" UTTY interface on init) so external devices can
    subscribe to firmware stdio over ZMQ, matching the `hal_dev_uart` /
    STM32 UART example pattern. Falls back to plain `print()` when
    UTTYModel isn't reachable. Per-intercept `registration_args.fmt_idx`
    lets the same handler cover `printf`-family variants whose format
    string isn't at arg 0 (e.g. `SEGGER_RTT_printf` is `fmt_idx: 1`).
*   **`src/halucinator/peripheral_models/interrupts.py`**: `clear_active_bp`
    is now a no-op when `irq_num` is None or when the backend has no IRQ
    controller, so peripheral models that call `clear_irq()` defensively
    (UTTYModel after `get_rx_char()` is one) don't crash configurations
    that don't define a `halucinator-irq` memory region.
*   **`src/halucinator/backends/renode_backend.py`**: r0–r12 writes now use
    Renode's `cpu SetRegister <N> <val>` instead of `cpu rN <val>` (which
    Renode rejects with "sysbus.cpu does not provide a field, method or
    property R0"). Without this fix, `ReturnConstant(ret_value=N)`
    intercepts silently delivered `0` to the firmware under the Renode
    backend — the bpv5 PCB-revision check (`mcu_detect_revision`) was the
    visible symptom.

## How to Run
Run halucinator and the external terminal device as two processes —
matching the two-window pattern of the STM32 UART example.

The default backend is **`unicorn`** everywhere — it's in-process, needs no
Docker container or QEMU build, and boots this firmware to `HiZ>` in a couple
of seconds. Override with `HAL_EMULATOR=unicorn|avatar2|qemu|ghidra|renode`
(`avatar2`/`qemu` need the Linux QEMU binaries; the CI smoke job uses `avatar2`).

> **Ordering note (unicorn):** the in-process unicorn backend boots to the
> banner in under a second, so start the **terminal device first**, give
> its ZMQ SUB socket a moment to connect, *then* launch halucinator.
> Otherwise the banner is published into the PUB/SUB slow-joiner window
> and lost — the device never sees a first `tx_buf`, so it never sends its
> `--prelude` boot keystroke. `run_tests.bash` does this ordering for you.
> (qemu/avatar2 boot slowly enough that either order works.)

In the first window, attach the terminal device:

```bash
PYTHONPATH=.:src python3 -m halucinator.external_devices.bpv5_terminal
```

In the second window (after the device prints `subscribed to ...`),
start halucinator:

```bash
bash test/firmware-rehosting/bpv5/run.sh        # uses unicorn by default
```

The output will show the Bus Pirate banner and initialization sequence,
followed by the firmware's VT100 cursor-position probe — the device
responds with your terminal's real size, the firmware switches to VT100
colour mode, and you reach the `HiZ>` command shell. Type `h<Enter>` for
the help text, Ctrl-D to disconnect.

For a non-interactive smoke test (used by CI) that runs both processes
together and asserts the firmware reaches the help output:

```bash
bash test/firmware-rehosting/bpv5/run_tests.bash               # unicorn on macOS
HAL_EMULATOR=avatar2 bash test/firmware-rehosting/bpv5/run_tests.bash   # Linux/Docker
```
