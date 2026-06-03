# Bus Pirate v5 (RP2040) Emulation Example

This example demonstrates how to re-host the Bus Pirate v5 (bpv5) firmware using HALucinator. The bpv5 is based on the Raspberry Pi RP2040 microcontroller.

## Provenance
The binary and symbolized ELF were provided for testing.
*   **Binary:** `bus_pirate5_rev10.bin` (MIT License)[^1]
*   **ELF:** `bus_pirate5_rev10.elf`
*   **Bootrom:** `b2.bin` (Downloaded from Raspberry Pi's official repo) (Copyright 2020 (c) 2020 Raspberry Pi (Trading) Ltd.) [^2]

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
*   **Flexible Printf:** The Bus Pirate uses `SEGGER_RTT_printf`, which places the format string as the second argument. We enhanced the core `halucinator.bp_handlers.generic.libc.Libc6` handler to support a `fmt_idx` parameter, allowing us to specify the format string position in the configuration.
*   **Console Injection:** We implemented a custom handler for `rx_fifo_try_get`. This allowed us to inject simulated keystrokes (like `\r\n` and `y`) directly into the firmware's input buffer from Python.

## Core Library Changes
To support this example, the following changes were made to the HALucinator core:
*   **`src/halucinator/bp_handlers/generic/libc.py`**:
    *   Added `fmt_idx` support to `Libc6.printf`.
    *   Switched output to use `hal_log.getHalLogger()` for better real-time visibility in Docker environments.
    *   Added `flush=True` to print statements.
    *   Resolved missing typing imports (`cast`, `HandlerFunction`).

## How to Run
Ensure you are inside the HALucinator Docker container.

```bash
# Run the emulation
PYTHONPATH=.:src halucinator \
    -c test/bpv5/bpv5_memory.yaml \
    -c test/bpv5/bpv5_config.yaml \
    -c test/bpv5/bpv5_addrs.yaml \
    -n bpv5_run
```

The output will show the Bus Pirate banner and initialization sequence, ultimately reaching the VT100 color mode prompt.

----
[^1]: [MIT License](https://github.com/DangerousPrototypes/BusPirate5-firmware/blob/d821f1344fa561a015362b5499ef9606cc16df69/LICENSE.TXT)
[^2]: [Copyright 2020 (c) 2020 Raspberry Pi (Trading) Ltd.](https://github.com/raspberrypi/pico-bootrom-rp2040/blob/ef22cd8ede5bc007f81d7f2416b48db90f313434/LICENSE.TXT)
