# Bus Pirate v5 (RP2040) Emulation Example

This example demonstrates how to re-host the Bus Pirate v5 (bpv5) firmware using HALucinator. The bpv5 is based on the Raspberry Pi RP2040 microcontroller.

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
Ensure you are inside the HALucinator Docker container. Run halucinator
and the external terminal device as two processes — matching the
two-window pattern of the STM32 UART example.

In the first window, start halucinator (default backend is `avatar2`;
override with `HAL_EMULATOR=unicorn|qemu|ghidra|renode`):

```bash
bash test/bpv5/run.sh
```

In the second window, attach the terminal device:

```bash
PYTHONPATH=.:src python3 -m test.bpv5.bpv5_terminal
```

The output will show the Bus Pirate banner and initialization sequence,
followed by the firmware's VT100 cursor-position probe — the device
responds with your terminal's real size, the firmware switches to VT100
colour mode, and you reach the `HiZ>` command shell. Type `h<Enter>` for
the help text, Ctrl-D to disconnect.

For a non-interactive smoke test (used by CI) that runs both processes
together and asserts the firmware reaches the help output:

```bash
HAL_EMULATOR=avatar2 bash test/bpv5/run_tests.bash
```
