<!-- Copyright 2026 Christopher Wright -->

# Bus Pirate v5 — Interface Map

This catalogues every Bus Pirate v5 (RP2040) interface the firmware exposes,
the on-chip controller peripheral each one drives, the MMIO it touches, and
the firmware entry points a modeling agent hooks to bring the interface up
under HALucinator. It is the fan-out worklist for the per-interface agents.

All addresses are from `test/firmware-rehosting/bpv5/bpv5_addrs.yaml` (the symbol map) and from
RE of `bus_pirate5_rev10.bin` (literal-pool MMIO bases, verified against the
RP2040 datasheet). Symbol→address: the addrs file lists decimal addresses;
flash is based at `0x10000000`.

---

## 1. Architectural reality: most modes run on PIO, not dedicated controllers

The single most important fact for the fan-out: **only SPI uses a dedicated
hardware serial controller (the PL022 SSP). Almost every other bus mode is
bit-banged through the RP2040 PIO state machines**, with software helpers in
flash. This changes what you model per interface:

| Interface | Backing hardware | What to model |
|-----------|------------------|---------------|
| SPI       | PL022 SSP @ `0x40040000` | SSP controller regs + target device |
| HW-UART   | PIO program (`hwuart_pio_*`) | PIO FIFO behaviour or HLE the read/write helpers |
| I2C       | PIO program (`pio_i2c_*`, `i2c_*_program_init`) | PIO or HLE the `pio_i2c_*_timeout` helpers |
| 2WIRE/3WIRE | PIO (`pio_hw2wire_*`, `pio_hw3wire_*`) | HLE the put16/get16 helpers |
| 1-WIRE    | PIO (`onewire_program_init`, `onewire_*`) | HLE `onewire_tx_byte`/`onewire_rx_byte` |
| LED       | PIO (WS2812/APA102 `*_program`) | usually no target needed (output-only) |
| INFRARED  | PIO (`irio_pio_*`, NEC/RC5) | HLE frame tx/rx |
| JTAG/SWD  | bit-banged GPIO (`jtag_*`, `swd*`) | model the scan-chain responses |
| DIO       | raw GPIO via SIO/`bio_*` | model pin levels |

For PIO-backed modes the cleanest HALucinator approach is **high-level
emulation (HLE)**: intercept the per-byte / per-frame software helper
(`onewire_tx_byte`, `pio_i2c_write_timeout`, `hwuart_pio_read`, …) and answer
from a Python target model, rather than emulating the PIO state machine. SPI
is the exception where modeling the SSP controller registers directly is both
feasible and the clean reference pattern.

PIO MMIO (currently inside the `logger` GenericPeripheral catch-all
`0x50000000`+`0x10000000`): **PIO0 = `0x50200000`, PIO1 = `0x50300000`**
(SDK standard; the firmware passes the base as an argument to the `pio_sm_*`
helpers, so it isn't a flash literal).

---

## 2. Bus modes (the `m` menu)

Canonical menu order from the firmware mode table (`mode/modes.c`), matching
the strings in the binary. Each mode is selected from the `HiZ>` shell with
`m` then the number/name. Mode handlers follow a fixed vtable:
`*_setup` (prompt the user) → `*_setup_exc` (apply settings) →
`*_start`/`*_stop` (transaction framing) → `*_write`/`*_read` (data) →
`*_macro` (`(n)` macros) → `*_cleanup` → `*_help` / `*_get_speed`.

| # | Menu | Controller / backing | MMIO base | Firmware entry points (setup → I/O) |
|---|------|----------------------|-----------|--------------------------------------|
| 1 | HiZ | none (safe/idle) | — | `hiz_setup` / `hiz_setup_exec` / `hiz_cleanup` |
| 2 | 1-WIRE | PIO | PIO0/1 | `hw1wire_setup` → `hw1wire_setup_exc`; `hw1wire_write`/`hw1wire_read`; low level `onewire_tx_byte`/`onewire_rx_byte`/`onewire_reset` |
| 3 | UART | PIO (`hwuart_pio_*`) | PIO0/1 | `hwuart_setup` → `hwuart_setup_exc`; `hwuart_write`/`hwuart_read`; `hwuart_pio_read`/`hwuart_pio_write` |
| 4 | HDUART | PIO (half-duplex UART) | PIO0/1 | `hwhduart_setup` → `hwhduart_setup_exc`; `hwhduart_write`/`hwhduart_read` |
| 5 | I2C | PIO (`pio_i2c_*`) | PIO0/1 | `hwi2c_setup` → `hwi2c_setup_exc`; `hwi2c_start`/`hwi2c_stop`/`hwi2c_write`/`hwi2c_read`; `pio_i2c_write_timeout`/`pio_i2c_read_timeout` |
| 6 | **SPI** | **PL022 SSP** | **`0x40040000`** | `spi_setup` → `spi_setup_exc`; `spi_start`/`spi_stop`/`spi_write`/`spi_read`; **`spi_write_read` @ `0x1001ac2c`** (the byte transfer — DR `+0x08`, SR `+0x0c`) |
| 7 | 2WIRE | PIO (`pio_hw2wire_*`) | PIO0/1 | `hw2wire_setup` → `hw2wire_setup_exc`; `hw2wire_write`/`hw2wire_read`; `pio_hw2wire_put16`/`pio_hw2wire_get16` |
| 8 | 3WIRE | PIO (`pio_hw3wire_*`) | PIO0/1 | `hw3wire_setup` → `hw3wire_setup_exc`; `hw3wire_write`/`hw3wire_read`; `pio_hw3wire_get16` |
| 9 | DIO | raw GPIO (`bio_*` / SIO) | SIO `0xd0000000`, IO_BANK0 `0x40014000` | `dio_setup` → `dio_setup_exc`; `dio_write`/`dio_read` |
| 10 | LED | PIO (WS2812/APA102) | PIO0/1 | `hwled_setup` → `hwled_setup_exc`; `hwled_write`; `ws2812_write`/`apa102_write` (output only — no target model needed) |
| 11 | INFRARED | PIO (`irio_pio_*`) | PIO0/1 | `infrared_setup` → `infrared_setup_exc`; `infrared_write`/`infrared_read`; NEC/RC5 `nec_write`/`nec_get_frame`, `rc5_send`/`rc5_receive` |
| 12 | JTAG | bit-banged GPIO | IO_BANK0 / SIO | `jtag_setup` → `jtag_setup_exc`; scan via `jtagScan`/`getDeviceIDs`; SWD via `swdScan`/`swdReadDPIDR` |
| 13 | BINLOOP | binary loopback | — | `binmode_setup`/`binmode_service` (SkipFunc'd; ⚠ intractable — core1-only + CDC iface 1, see worklist) |
| 14 | Scope | ADC + DMA + LCD | ADC `0x4004c000` | ✅ `scope_setup` → `scope_setup_exc`; entered via `d 2` (display-mode idx 1), modeled waveform — see worklist |

### Per-mode protocol-helper / target devices

Many modes ship demo/protocol drivers that a target model can satisfy
end-to-end. These are the highest-value targets for "the interface really
works" proof:

- **SPI**: `spiflash_probe`/`spiflash_dump` (SFUD JEDEC flash — JEDEC-ID `0x9F`,
  SFDP, reads); `spi_eeprom_handler` (25xx EEPROM); `nand_spi_*`/`spi_nand_*`
  (SPI NAND). **`spiflash_probe` is the SPI keystone target** (see playbook).
- **I2C**: `i2c_search_addr` (address scan), `demo_si7021`/`demo_sht4x`/
  `demo_ms5611`/`demo_tsl2561`/`demo_tcs34725` (sensor demos),
  `i2c_eeprom_handler`, `ddr4_*`/`ddr5_*` (SPD), `fusb302_handler`,
  `ap33772s_*`, `mpu6050_*`.
- **1-WIRE**: `onewire_test_ds18b20_conversion`, `ds1wire_id`, `OWSearch`,
  `ow_eeprom_read`.
- **UART**: `nmea_decode_handler`/`process_gps`, `uart_bridge_handler`.
- **JTAG/SWD**: `getDeviceIDs`, `bypassTest`, `swdReadDPIDR` (DPIDR response).
- **2WIRE**: `sle4442` (smartcard).

---

## 3. Always-on peripherals (run regardless of mode)

These initialise during boot (`main_system_initialization`) and several are
currently SkipFunc-spoofed in `bpv5_config.yaml`. A modeling agent replaces
the SkipFunc with a real model when that peripheral's data is needed.

| Peripheral | Hardware | MMIO base | Firmware entry | Current spoof |
|------------|----------|-----------|----------------|---------------|
| **ST7789 LCD** | SPI-attached TFT (320×240) | PL022 SSP `0x40040000` (shared) + GPIO | `lcd_init`, `lcd_configure`, `lcd_write_string`, `disp_default_lcd_update`, `ui_lcd_update` | `skip_lcd_*` SkipFunc |
| **ADC / voltage measure** | RP2040 ADC | `0x4004c000` | `adc_init`, `adc_measure`, `adc_measure_single`, `amux_read`, `amux_sweep` | `skip_adc_init`, `skip_amux` |
| **PSU (programmable supply)** | DAC + ADC fuse | via amux/ADC + GPIO | `psu_init`, `psu_enable`, `psu_measure`, `psucmd_enable`, `psucmd_init` | `skip_psucmd_*` |
| **Pull-up resistors** | GPIO control | IO_BANK0 `0x40014000` | `pullups_init`, `pullups_enable`, `pullup_enable` | `skip_pullups_init` |
| **Per-pin I/O (BIO)** | GPIO via SIO + 74-shift | SIO `0xd0000000`, shift reg | `bio_init`, `bio_put`/`bio_get`, `bio_output`/`bio_input`, `shift_*` | `skip_bio_init`, `skip_shift_*` |
| **AMUX (analog mux)** | analog mux on ADC | ADC `0x4004c000` | `amux_init`, `amux_select_bio`, `amux_read`, `amux_sweep` | `skip_amux` |
| **RGB LEDs (onboard)** | PIO WS2812 | PIO0/1 | `rgb_init`, `rgb_put`, `rgb_set_all` | `skip_rgb_init` |
| **Onboard SPI NAND storage** | SPI NAND + FatFs/dhara | SSP / GPIO | `storage_init`, `storage_mount`, `spi_nand_*`, `dhara_*`, `f_*` (FatFs) | ✅ MODELED — FatFs-ABI HLE (`NandStorage`); see §3-storage below |
| **Button** | GPIO | IO_BANK0 | `button_init`, `button_get`, `button_check_press` | (runs) |
| **Frequency / PWM** | PWM block + timers | PWM `0x40050000` | `pwm_configure_enable`, `freq_measure_period` | (per-pin, on demand) |

---

## 4. RP2040 chip infrastructure (already handled in the demo)

For reference — the boot-critical chip blocks the existing config already
satisfies (do not re-model unless extending):

| Block | MMIO | Handling |
|-------|------|----------|
| SIO (cores, spinlocks) | `0xd0000000` | mapped as RAM; `RP2040Init.init_spinlocks` pre-claims locks |
| Bootrom (ROM funcs) | `0x00000000` (`b2.bin`) | mapped r-x; ROM lookups satisfied |
| RESETS | `0x4000c000` | inside `io_ram` RAM region (sticky) |
| Clocks / PLL / XOSC | `0x40008000`/`0x40028000`/`0x40024000` | `runtime_init_clocks` SkipFunc'd |
| USB CDC | DPRAM `0x50100000` | `tud_cdc_n_*` intercepted → `BusPirateConsole` UTTY bridge |
| Multicore | SIO FIFO | `multicore_launch_core1` SkipFunc'd (Core 0 only) |

---

## 5. Memory-map gaps to widen for fan-out

The current `bpv5_memory.yaml` maps `io_ram` `0x40000000`–`0x40ffffff` and
`ssi_ram` `0x18000000`. Two things the fan-out will need:

1. **PIO at `0x50200000`/`0x50300000`** currently falls into the `logger`
   GenericPeripheral catch-all (`0x50000000`, size `0x10000000`). PIO-backed
   modes that aren't HLE'd at the helper level will read/write here. Prefer
   HLE at the software helper; only carve out a real PIO model if needed.
2. **SPI SSP at `0x40040000`** is inside `io_ram` (RAM-backed). The SPI
   keystone replaces that RAM behaviour with a controller model + target
   device.

---

## 6. Fan-out worklist (one agent per row)

Priority order — SPI first (keystone, done as the reference), then the
high-value protocol modes:

1. **SPI** — PL022 SSP model + SPI NOR flash target (JEDEC `0x9F`). *(keystone — see playbook)* ✅ done
2. **I2C** — PIO-HLE + sensor/EEPROM target (e.g. Si7021 or 24xx EEPROM via `i2c_search_addr`).
3. **1-WIRE** — PIO-HLE + DS18B20 / DS1-ID target. ✅ done — `Ds18b20Target` (HLE at `onewire_reset`/`onewire_tx_byte`/`onewire_rx_byte`); the firmware `ds18b20` demo reads back the modeled +25.0625 °C with a valid Maxim CRC8. See `bp_handlers/bpv5/onewire.py` + `run_onewire_test.bash`.
4. **UART** — PIO-HLE + loopback or NMEA/GPS source.
5. **2WIRE / 3WIRE** — PIO-HLE + SLE4442 smartcard / generic target. ✅ done — `TwoWireTarget` HLE's the 2WIRE leaf helpers `pio_hw2wire_put16`/`pio_hw2wire_get16` plus `pio_hw2wire_start`/`pio_hw2wire_stop` (the framing helpers busy-poll the PIO FIFO and would hang); a CLI `[0x30 0x00 r:4]` smartcard-memory read returns the ramp bytes the firmware renders as `RX: 0x00 0x01 0x02 0x03`. `ThreeWireTarget` HLE's the single full-duplex helper `pio_hw3wire_get16` (TX byte in `*buf`, RX written back) plus the `hw3wire_start`/`hw3wire_stop` CS framing; a CLI `[0x80 r:4]` read streams the signature the firmware renders as `RX: 0x93 0xC4 0x6E 0x5A`. Both skip the PIO bring-up (`pio_hw{2,3}wire_init`, `pio_hw2wire_reset`) like `skip_onewire_init`. Live menu #7 (2WIRE) / #8 (3WIRE). See `bp_handlers/bpv5/twowire.py` + `run_twowire_test.bash`.
6. **JTAG / SWD** — bit-bang GPIO + scan-chain (IDCODE / DPIDR) target. *(done — `JtagTarget` answers the blueTag `bluetag jtag -c N` IDCODE scan with ARM IDCODE `0x4BA00477`, rendered by the firmware's own `displayDeviceDetails`; HLE'd at the `bypassTest`/`detectDevices`/`getDeviceIDs` discovery seam since the bus is bit-banged with no per-bit helper. See `bp_handlers/bpv5/jtag.py` + `run_jtag_test.bash`.)*
7. **LED** — PIO-HLE, output-only (verify frame bytes emitted; no target).
   ✅ done — `LedStripSink` (HLE at `ws2812_write`/`apa102_write`/`hwled_write`;
   `hwled_start`/`hwled_stop` observed for frame delimiting). A CLI `[0x80FF00]`
   write in WS2812 mode (menu #10, type `1`) is captured as the exact emitted
   pixel word `0x80FF00` → WS2812 wire bytes G=0x80 R=0xFF B=0x00 (firmware does
   NO RGB→GRB reorder; user supplies GRB order, PIO shifts top 24 bits MSB-first).
   The firmware renders its own transaction echo (`RESET` start/stop frame labels
   + `TX:`). Skips: `hwled_setup_exc`, `hwled_wait_idle`, `ws2812_start/stop`,
   `apa102_start/stop` (PIO bring-up / drain that would spin on the bypassed PIO
   SM struct). See `bp_handlers/bpv5/led.py` + `run_led_test.bash`.
8. **INFRARED** — PIO-HLE + NEC/RC5 frame source/sink. *(done — `InfraredNecTarget` does BOTH directions for NEC. TX: the CLI `[0x0804]` transaction (byte0=addr 0x04, byte1=cmd 0x08) → `infrared_write` → `nec_write` (HLE captures the wire frame 0xF708FB04); the firmware renders `TX: 0x0804.16`. RX: the `infrared_periodic` loop calls `nec_get_frame`; the handler seeds a faked PIO RX FIFO (ctx @ 0x2003ac14 → scratch base 0x30001000) so the firmware's OWN decode+print renders `(0xf708fb04) Address: 4 (0x04) Command: 8 (0x08)`. PIO init/wait/drain leaves (`nec_*_init`/`nec_tx_wait_idle`/`nec_rx_drain_fifo`) are SkipFunc'd. Note: a bare value does NOT compile to a write in IR mode — it must be `[`/`]`-framed. See `bp_handlers/bpv5/infrared.py` + `run_infrared_test.bash`.)*
9. **DIO** — GPIO pin-level model (read/write/measure). ✅ done — `DioPinTarget` (HLE at the `bio_get`/`bio_put`/`bio_output`/`bio_input` leaf helpers; the live `A`/`a`/`@` DIO commands funnel through these). `@ 5` reads a modeled externally-driven HIGH input (`IO5 set to INPUT: 1`); `A 4`→`@ 4` then `a 4`→`@ 4` demonstrate write-then-read-back (`OUTPUT: 1`/`INPUT: 1` then `OUTPUT: 0`/`INPUT: 0`). See `bp_handlers/bpv5/dio.py` + `run_dio_test.bash`.
10. **Scope** — ADC waveform source + LCD render. ✅ done — Scope is display-mode
    index 1 (NOT a `m`-menu bus mode); the CLI enters it with **`d 2`**
    (`ui_display_enable_args` validates `N<=3`, uses `N-1` to index the
    display-mode table @0x20001990). On `d 2` the firmware runs `scope_setup`
    (allocs the 0xfb00 sample/framebuffer block) + `scope_setup_exc` and prints
    `Display: Scope`. `ScopeModel` (HLE `lcd_enable`→no-op,
    `scope_setup_exc`→observe, `scope_periodic`→inject) writes a modeled DC
    waveform (raw 0x800 → 3.30 V; mV=raw*6600>>12) into the firmware's REAL
    sample buffer (0x20038ad4 → 0x20014880) + publishes the trigger level
    @0x2003dc44. The free-running DMA/IRQ render pipeline doesn't fire headless
    (no live core1/DMA). See `bp_handlers/bpv5/scope.py` + `run_scope_test.bash`.
    **BINLOOP / binmode** — intractable for a console proof: runs on core1
    (never launched headless), I/O on USB CDC interface 1 (not the terminal's
    interface 0), and un-skipping `binmode_service` steals the single-owner big
    buffer from Scope. Documented in `bp_handlers/bpv5/scope.py` + config comments.

Always-on follow-ups: ~~**ST7789 LCD** (framebuffer capture)~~ ✅ done (see
below), **ADC/PSU/AMUX** (voltage source model). ~~**onboard SPI NAND storage**
(FatFs-backed image)~~ ✅ done (see below).

### §3-lcd — ST7789 LCD pixel framebuffer (controller-stream capture) ✅ done

The text model (`St7789Lcd`, `bp_handlers/bpv5/lcd.py`) only captured drawn
*strings*. `St7789Framebuffer` (`bp_handlers/bpv5/lcdfb.py`) now captures the
**actual pixels** the firmware's real glyph rasterizer streams to the ST7789,
into a **320×240 RGB565 framebuffer** dumped to a real PNG
(`bpv5_lcd_screen.png`) via a pure-stdlib encoder (`tools/fb_to_png.py`).

RE (capstone Thumb, base 0x10000000): every LCD byte goes through the single
SPI-FIFO veneer **`__spi_write_blocking_veneer` @0x10053b58** =
`spi_write_blocking(spi0=0x4003c000, src, datasize)`. `datasize==1` carries
the ST7789 command/coordinate bytes (a self-framing stream
`2A xs xs xe xe  2B ys ys ye ye  2C …` = CASET/RASET window + RAMWR);
`datasize==2` carries one RGB565 pixel where `src` is a **pointer** to the
colour in the table @0x100b11c4 (red 0xf800) / 0x100b11dc (grey panel 0x4529).
We decode the CASET/RASET window with an opcode-driven FSM (the D/C GPIO is
RAM-backed and not readable post-`str` under emulation), then paint each pixel
at the sequential RAMWR cursor — the rasterizer emits exactly window-area
pixels in GRAM order, so this is pixel-exact. Result: the idle screen renders
the real pin-label column **Vout / IO0…IO7 / GND** + per-pin voltages in their
firmware colours. A whole-flash xref confirms all 205 callers of the veneer
are LCD/display/scope-LCD code — NONE on the user SPI bus mode (`hwspi_*`,
`SpiFlashTarget`) — so the SPI keystone is unaffected (verified). Test:
`run_lcdfb_test.bash` (ports 5845/5846). MADCTL=0x20, COLMOD=0x55 (landscape,
RGB565).

### §3-storage — Onboard SPI-NAND storage (FatFs-ABI HLE) ✅ done

The full stack is FatFs → dhara (flash translation) → `spi_nand_*` → SSP. We HLE
at the **highest clean seam — the FatFs ABI** — and serve a small *modeled*
FatFs volume from Python (`NandStorage` in `bp_handlers/bpv5/nand.py`), letting the
firmware's own `storage_ls` / `disk_cat_handler` print loops render the result.

Hooks (RE'd from `bus_pirate5_rev10.bin`; flash base `0x10000000`):

- `f_mount` → `FR_OK` (0). `storage_mount` now RUNS for real (replaces
  `skip_storage_mount`) and records the volume mounted during boot. The handler
  seeds the FATFS BPB fields (`fs_type`@+0, `csize`@+0xa, clusters@+0x20) so the
  boot capacity line renders sanely.
- `f_opendir`/`f_readdir`/`f_closedir` — the CLI `ls`
  (`disk_ls_handler` → `storage_ls`). `f_readdir` fills the caller's FatFs
  `FILINFO` (no-LFN 8.3 layout: `fsize`@+0 DWORD, `fattrib`@+8 BYTE 0x10=DIR /
  0x20=file, `fname`@+9 NUL-term), and signals end-of-dir with `fname[0]=0`.
- `f_open`/`f_gets`/`f_read`/`f_close` — the CLI `cat <name>`
  (`disk_cat_handler`, uses `f_gets` per line) and `storage_load_config`
  (reads/parses `bpconfig.bp` via `f_read`). `f_open` returns `FR_NO_FILE` (4)
  for absent files.

Kept `skip_storage_init` (the SSP-NAND GPIO/pin bring-up; the FatFs HLE needs no
real NAND/dhara layer). Live proof: a CLI `ls` renders the modeled directory

```
   <DIR>   logs
       147 bpconfig.bp
        42 hello.scr
       132 readme.txt
1 dirs, 3 files
```

and `cat bpconfig.bp` renders the modeled JSON config (which boot also parses via
`storage_load_config`). See `bp_handlers/bpv5/nand.py` + `run_nand_test.bash`.
