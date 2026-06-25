<!-- Copyright 2026 Christopher Wright -->

# Bus Pirate 5 — Demo Walkthrough

This is the "drive it yourself and see what it does" guide for the emulated
Bus Pirate 5 (RP2040). The firmware runs under HALucinator with **every
interface backed by a modeled target device**, so you get a real, interactive,
colour `HiZ>` shell — type a command, watch the firmware talk to a (modeled)
chip, and see the bytes come back.

- **▶ Easiest:** the [live web panel](#0-the-live-web-panel-recommended) — terminal +
  LCD + devices in one browser tab.
- Want the **automated proof** instead? Jump to [§3 One-shot tests](#3-one-shot-tests).
- Want to know **how it's built**? See [`INTERFACES.md`](INTERFACES.md) — the map
  of every bus mode + peripheral and how each is modeled.

---

## 0. The live web panel (recommended)

`bpv5_panel.py` is an external device that shows **everything in one browser
tab** — an interactive terminal, a **synthesized live device display**
(VOUT/VREF + IO0–7 voltages + active mode; **drag the IO sliders to set the
modeled pin voltages live**, no restart), a **live LED strip** (the WS2812/
APA102 pixels; a **per-LED colour picker** row + fill presets set the LEDs by
injecting the CLI commands for you), the **pixel-faithful ST7789 boot capture**, an active-device
readout, and a feed of the modeled-target I/O — all driven by what you type.
No native GUI (it renders in your browser, so there's no Tk to set up).

> Note: the **LCD image is the firmware's real render captured at boot**. The
> firmware only re-streams LCD pixels from a hardware-timer refresh that the
> rehost stubs for boot stability, so it doesn't update live — the **synthesized
> device display** above it reflects voltage/mode changes (e.g. `W 5.0`) live.

Run it as **two windows** (default backend is `unicorn` everywhere; avatar2/qemu need the Linux QEMU binaries).
In each window, from the repo root:

```bash
export PATH="<repo>/virtualenvs/halucinator/bin:$PATH"   # put halucinator on PATH
export PYTHONPATH="src:."
```

```bash
# Window 1 — the panel (start FIRST; it opens http://127.0.0.1:8765):
python3 -m halucinator.external_devices.bpv5_panel

# Window 2 — halucinator with the LCD framebuffer overlay:
bash test/firmware-rehosting/bpv5/run_lcd.sh
```

Order matters (panel first) for the ZMQ slow-joiner, same as the terminal.
The browser tab fills in a few seconds after Window 2 boots: **left** the
terminal (click it, then type), **top-right** the live LCD, **right** the
device tiles + feed. Needs internet on first load (xterm.js is from a CDN).

### Drive each interface — type in the terminal pane

`↵` = Return. It boots into `1WIRE>`. Switch interfaces any time by typing `m`
again. Watch the **device feed** and the **LCD**.

| Interface | Type this | You should see |
|-----------|-----------|----------------|
| **SPI** flash | `m`↵ `6`↵ `↵`×5 `[0x9f r:3]`↵ | feed `MOSI=0x9F → MISO=0xEF 0x40 0x18`; term `RX: 0xEF 0x40 0x18` |
| **I²C** EEPROM | `m`↵ `5`↵ `↵`×2 `[0xA0 0x00 [0xA1 r:2]`↵ | `[I2cEepromTarget]` ACK/read |
| **1-Wire** DS18B20 | `m`↵ `2`↵ `ds18b20`↵ | term `Temperature: 25.062` |
| **UART** loopback | `m`↵ `3`↵ `↵`×6 `[0x41 r:1]`↵ | term `RX: 0x41` |
| **2-Wire** | `m`↵ `7`↵ `↵`×2 `[0x30 0x00 r:4]`↵ | term `00 01 02 03` |
| **3-Wire** | `m`↵ `8`↵ … | term `93 C4 6E 5A` |
| **JTAG** | `m`↵ `12`↵ `bluetag jtag -c 6`↵ | term `Device 0: 0x4BA00477` |
| **Infrared** NEC | `m`↵ `11`↵ `2`↵ `↵` `[0x0804]`↵ | `[InfraredNecTarget]` TX frame |
| **DIO** pins | `m`↵ `9`↵ `@ 5`↵ `A 4`↵ `@ 4`↵ | `[DioPinTarget] bio_output/bio_put/bio_get` |
| **LED** WS2812 | `m`↵ `10`↵ `1`↵ `[0x80FF00]`↵ | **LED tile** turns orange; feed `WS2812 PIXEL word=0x80FF00` |
| **Storage** | `ls`↵ `cat bpconfig.bp`↵ | term file list + contents (`[Storage] f_open/f_read`) |
| **ADC / PSU** | `v`↵ `W 3.3 0`↵ `v`↵ | **Voltages tile** + LCD update; `VOUT=3300 mV …` |
| **Scope** | `d 2`↵ | term `Display: Scope`; `[ScopeModel] … 3.30 V` |

Storage / ADC / Scope work from any prompt (no `m` needed). The LCD redraws on
its own and when you change modes or run `v`/`W`.

---

## 1. Launch the interactive terminal (the "UI")

The UI is a VT100 terminal device (`bpv5_terminal.py`) that bridges the
firmware's USB-CDC console over ZMQ. It renders the firmware's colour TUI and
forwards your keystrokes. Run it as **two processes in two windows** (the
default backend is `unicorn` everywhere; avatar2/qemu need the Linux QEMU binaries).

```bash
cd <repo root>
export PATH="<repo>/virtualenvs/halucinator/bin:$PATH"   # put `halucinator` on PATH

# Window 1 — the terminal device FIRST (let its ZMQ SUB bind), then:
PYTHONPATH=.:src python3 -m halucinator.external_devices.bpv5_terminal

# Window 2 — after Window 1 prints "subscribed to ...":
HAL_EMULATOR=unicorn bash test/firmware-rehosting/bpv5/run.sh
```

> **Order matters on unicorn:** it boots to the banner in <1s, so the terminal
> must be subscribed first or the banner is lost in the ZMQ slow-joiner window.
> (`run_tests.bash` handles ordering for you in CI mode.)

You'll see the Bus Pirate banner, a VT100 cursor-probe handshake, then a colour
prompt. It boots into whatever mode the stored config selects — with the modeled
`bpconfig.bp` that's **`1WIRE>`** (a real device would show `HiZ>` by default);
either way type `m` ⏎ to pick any mode. Type `h` ⏎ for help, `i` ⏎ for board
info, `Ctrl-D` to disconnect.

> **Typing / echo notes (both fixed in current `bpv5_terminal.py`):**
> - *Keystrokes did nothing:* the firmware emits `ESC[6n`; your terminal
>   auto-replies `ESC[<row>;<col>R`, which used to get forwarded into the
>   firmware's command line and corrupt input. The terminal now filters those
>   replies (it answers `ESC[6n` on the firmware's behalf).
> - *Typed characters don't appear:* the firmware reads our injected input via
>   the rx-fifo bridge and does **not** echo it (a real unit echoes from its
>   USB read loop, which we bypass). The terminal now does **local echo** of
>   what you type. Commands still execute either way — you just couldn't see
>   them before. Disable with `--no-local-echo` if your firmware does echo.

### Bus Pirate command syntax (same across modes)
- `m` ⏎ — open the **mode menu**, then a number to pick a bus mode.
- `[` … `]` — frame one transaction (START/STOP, or SPI CS low/high).
- a bare value (`0x9f`, `0x30`) — **write** that byte.
- `r` / `r:N` — **read** one / N bytes.
- `⏎` at a setup prompt accepts the default.

---

## 2. Per-interface walkthrough — type this, expect this

Each block: select the mode from `HiZ>`, run the transaction, and the firmware
prints what the **modeled** device answered. (`⏎` = press Enter.)

### SPI (#6) — modeled Winbond W25Q128 NOR flash
```
m ⏎ 6 ⏎ ⏎⏎⏎⏎⏎   (accept 5 defaults)
[0x9f r:3] ⏎       read JEDEC ID
```
Expect:
```
CS Enabled
TX: 0x9F
RX: 0xEF 0x40 0x18      <- EF=Winbond, 40 18=W25Q128
CS Disabled
```

### I2C (#5) — modeled 24C02 EEPROM @ 0x50
```
m ⏎ 5 ⏎ ⏎⏎          (accept defaults)
[0xA0 0x00 [0xA1 r:2] ⏎     write addr 0x00, restart, read 2
```
Expect `TX: 0xA0 ACK 0x00 ACK` / `TX: 0xA1 ACK` then `RX: 0x00 ACK 0x01 NACK`
(the EEPROM's ramp data — byte n == n).

### 1-WIRE (#2) — modeled DS18B20 thermometer
```
m ⏎ 2 ⏎            (no setup prompts)
ds18b20 ⏎          run the conversion demo
```
Expect the 9-byte scratchpad `RX: 91 01 4b 46 7f ff 00 10 3d` and
`Temperature: 25.062` (CRC8 valid — no "CRC Fail").

### UART (#3) — modeled serial peer
```
m ⏎ 3 ⏎ ⏎⏎⏎⏎⏎⏎     (accept 6 defaults)
[0x41 r:1] ⏎        send 'A', read 1
```
Expect `RX: 0x41` (the peer echoes it back). **Bonus — NMEA GPS:** in UART mode
type `gps` ⏎ and a modeled `$GPGGA…` sentence decodes to `fix quality: 1`.

### 2WIRE (#7) — modeled SLE4442-style smartcard memory
```
m ⏎ 7 ⏎ ⏎⏎
[0x30 0x00 r:4] ⏎   READ-MAIN cmd, addr 0, read 4
```
Expect `RX: 0x00 0x01 0x02 0x03`.

### 3WIRE (#8) — modeled Microwire EEPROM
```
m ⏎ 8 ⏎ ⏎⏎
[0x80 r:4] ⏎
```
Expect `RX: 0x93 0xC4 0x6E 0x5A`.

### JTAG (#12) — modeled ARM scan chain
```
m ⏎ 12 ⏎
bluetag jtag -c 6 ⏎     blueTag IDCODE pinout scan
```
Expect `[ Device 0 ] 0x4BA00477` and the decode `(mfg: 'ARM Ltd', part: 0xba00)`.

### INFRARED (#11) — modeled NEC remote
```
m ⏎ 11 ⏎ 2 ⏎ ⏎       (pick NEC)
[0x0804] ⏎           transmit addr 0x04 cmd 0x08
```
Expect `TX: 0x0804.16`. The RX path also decodes a seeded frame to
`Address: 4 (0x04) Command: 8 (0x08)`.

### DIO (#9) — modeled per-pin GPIO
```
m ⏎ 9 ⏎
@ 5 ⏎    read IO5 (modeled HIGH input)  -> IO5 set to INPUT: 1
A 4 ⏎    drive IO4 high                 -> IO4 set to OUTPUT: 1
@ 4 ⏎    read it back                   -> IO4 set to INPUT: 1
a 4 ⏎ @ 4 ⏎   drive low, read back      -> OUTPUT: 0 / INPUT: 0
```

### LED (#10) — modeled WS2812 strip
```
m ⏎ 10 ⏎ 1 ⏎        (1 = WS2812/NeoPixel)
[0x80FF00] ⏎         one pixel
```
The model captures the emitted pixel word `0x80FF00` (wire bytes G=80 R=FF B=00).

### Always-on: voltages (no mode needed)
```
v ⏎             measure all pins  -> Vout 3.3V, IO0..IO7 = 0.4..3.2V
W 3.3 0 ⏎       enable PSU at 3.3V -> Vreg output: 3.0V ... Current: 10.0mA
```

### Always-on: storage (no mode needed)
```
ls ⏎                 -> <DIR> logs / bpconfig.bp / hello.scr / readme.txt
cat bpconfig.bp ⏎    -> the modeled config JSON ("led_brightness": 10, ...)
```

### Scope display (#14)
```
d 2 ⏎     -> Display: Scope   (modeled 3.30 V DC waveform fed to the trace)
```

> **BINLOOP (#13)** is intentionally not enabled — its binary-protocol service
> runs only on the RP2040's second core (which the rehost doesn't launch) and
> on a separate USB-CDC interface from the console, so it's unreachable
> headless. Documented in `INTERFACES.md` rather than faked.

### Capture a screenshot (no live TTY needed)
To produce an image of what the colour terminal looks like — boot bringing up
the modeled peripherals, the mode menu, and a live transaction — render a
scripted session to SVG:
```bash
bash test/firmware-rehosting/bpv5/tools/capture_session.bash session.svg            # default: SPI JEDEC read
bash test/firmware-rehosting/bpv5/tools/capture_session.bash i2c.svg 'm\r5\r\r\r[0xA0 0x00 [0xA1 r:2]\r'
```
(`tools/render_ansi.py` is the standalone ANSI→SVG terminal renderer; pure stdlib.)

### What about the physical LCD screen?
The ST7789 TFT is modeled **pixel-faithfully**: the firmware's real glyph
rasterizer runs, and `St7789Framebuffer` decodes the ST7789 SPI command stream
(`CASET`/`RASET` window + `RAMWR` RGB565 pixels) into a 320×240 framebuffer,
dumped as a real PNG:
```bash
HAL_EMULATOR=unicorn bash test/firmware-rehosting/bpv5/run_lcdfb_test.bash   # writes bpv5_lcd_screen.png
```
That's a genuine screenshot of the idle UI (pin labels + per-pin voltages in the
firmware's actual table colours), not a mock. (`tools/render_lcd.py` is a
lighter text-level *mock* from the older text-capture model — kept as an
alternate; the framebuffer PNG is the faithful render.) There is no live
graphical window — the PNG is the snapshot.

---

## 3. One-shot tests (automated proof)

Every interface has a scripted test that boots the firmware, drives the exact
sequence above, and asserts BOTH the model byte-trace AND the firmware-rendered
output. Run one:

```bash
export PATH="<repo>/virtualenvs/halucinator/bin:$PATH"
HAL_EMULATOR=unicorn bash test/firmware-rehosting/bpv5/run_spi_test.bash      # or run_i2c_test.bash, etc.
```

Run the **whole device** sweep (all 15, ~10–15 min):

| test | proves |
|------|--------|
| `run_tests.bash` | boot → `HiZ>` |
| `run_spi_test.bash` | SPI JEDEC read |
| `run_i2c_test.bash` | I2C EEPROM read |
| `run_onewire_test.bash` | DS18B20 temperature |
| `run_uart_test.bash` / `run_uart_nmea_test.bash` | UART loopback / NMEA |
| `run_twowire_test.bash` | 2WIRE + 3WIRE |
| `run_jtag_test.bash` | JTAG IDCODE |
| `run_infrared_test.bash` | NEC TX + RX |
| `run_dio_test.bash` | GPIO drive/read |
| `run_led_test.bash` | WS2812 pixel |
| `run_lcdfb_test.bash` | LCD pixel framebuffer → PNG |
| `run_adcpsu_test.bash` | voltages / PSU |
| `run_nand_test.bash` | FatFs ls/cat |
| `run_scope_test.bash` | Scope display |

> The per-interface scripts use dedicated ZMQ ports + scoped teardown so they
> don't collide. `run_tests.bash` / `run_spi_test.bash` use the default ports
> and a broad `pkill` — don't run those while another emulation is live.

---

## 4. Backends

bpv5 runs on **all five** HALucinator backends — the handlers use only the
backend-agnostic `HalBackend` API, so the same config works everywhere. Pick a
backend with `HAL_EMULATOR=<name>` (default: `unicorn` everywhere; the CI
smoke job overrides to `avatar2`):

| Backend | Notes |
|---------|-------|
| `unicorn` | In-process, no QEMU build needed; fastest. **Default everywhere.** |
| `avatar2` | QEMU via avatar2; needs the Linux QEMU binaries. CI default. |
| `qemu`    | Direct QEMU/GDB backend; **much slower** — bump the timeout. |
| `renode`  | Renode; **slowest** — bump the timeout generously. |
| `ghidra`  | Ghidra p-code emulation. |

All five boot to `HiZ>` and complete the modeled-device transactions
(e.g. SPI `RX: 0xEF 0x40 0x18`). The full per-interface suite is validated on
`unicorn` (15/15); the mostly-difference is **speed**: `qemu` and `renode`
execute far slower, so the per-interface tests can exceed the default timeout.
Bump it with the global knob (or the per-test `BPV5_<X>_TIMEOUT`):

```bash
HAL_EMULATOR=qemu   BPV5_TIMEOUT=300 bash test/firmware-rehosting/bpv5/run_spi_test.bash
HAL_EMULATOR=renode BPV5_TIMEOUT=450 bash test/firmware-rehosting/bpv5/run_spi_test.bash
```

**One backend caveat — the LCD framebuffer overlay (`run_lcdfb_test.bash`):**
it captures the ST7789 panel pixel-by-pixel (~38k pushes), so on the
round-trip backends (`avatar2`/`qemu`/`renode`) the per-pixel forwarding is
impractically slow. Run the faithful framebuffer screenshot on **`unicorn`**
(in-process, no round-trips). Every other interface — including Scope — passes
on all five backends (bump the timeout for the slow ones).

The all-backend Docker image (`Dockerfile.ci`) bundles every backend's tooling
(QEMU variants + Renode + Ghidra); `run_backend_matrix.sh` is the cross-backend
runner.
