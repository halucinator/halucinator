# Bus Pirate v5 (RP2040) firmware — provenance & licensing

This directory ships two third-party binary blobs that the HALucinator demo
boots. Both are redistributed under their upstream licences. The full
license texts are reproduced under `./licenses/` next to this file.

---

## `bus_pirate5_rev10.bin`

The Bus Pirate v5 application firmware.

* **Upstream**: <https://github.com/DangerousPrototypes/BusPirate5-firmware>
* **Licence**: **MIT**
* **Copyright**: © 2023 Ian Lesnet, Where Labs LLC
* **Licence text**: [`licenses/bus_pirate5-firmware.LICENSE.TXT`](licenses/bus_pirate5-firmware.LICENSE.TXT)
  (an unaltered copy of the upstream `LICENSE.TXT`)

The "rev10" in the filename refers to the **PCB revision** the firmware
detects at runtime, not a firmware release tag. The Bus Pirate v5
firmware queries `mcu_detect_revision()` early in boot and refuses to
continue if the value it reads does not match the firmware build's
expected hardware revision; the bundled file is built for the BPv5
rev10 PCB. The `mcu_detect_revision` intercept in `bpv5_config.yaml`
returns 10 so the firmware passes this check on the emulator.

To rebuild from source instead of using this committed copy:

```bash
git clone https://github.com/DangerousPrototypes/BusPirate5-firmware
cd BusPirate5-firmware
# follow the project's README — uses pico-sdk, cmake, arm-none-eabi-gcc
```

The upstream project also publishes per-commit build artefacts via
GitHub Actions (`firmware-rp2040-ubuntu-latest`); those are gated
behind a GitHub login and expire, so they're not stable enough to
fetch automatically.

---

## `b2.bin`

The RP2040 second-stage bootrom image, mapped at flash offset 0 to
satisfy the firmware's dynamic calls into the chip's ROM-resident
runtime helpers (optimised `memcpy`, the floating-point library, USB
boot path, etc.).

* **Upstream**: <https://github.com/raspberrypi/pico-bootrom-rp2040>
* **Licence**: **BSD-3-Clause** (with `bootrom/mufplib.S` and
  `bootrom/mufplib-double.S` licensed separately upstream — those
  files are not included in or required by this demo's `b2.bin`).
* **Copyright**: © 2020 Raspberry Pi (Trading) Ltd.
* **Licence text**: [`licenses/pico-bootrom-rp2040.LICENSE.TXT`](licenses/pico-bootrom-rp2040.LICENSE.TXT)
  (an unaltered copy of the upstream `LICENSE.TXT`)

The bootrom is what the RP2040 silicon executes between reset and
flash XIP handoff; the same bits are mask-ROM'd into every RP2040
chip and the upstream repo holds the source they're built from. The
"b2" name is from the upstream build's output (`b2.bin` is the
production rev-B2 bootrom image, hence the bundled filename).

To rebuild from source instead of using this committed copy:

```bash
git clone https://github.com/raspberrypi/pico-bootrom-rp2040
# follow the project's README — uses pico-sdk and arm-none-eabi-gcc.
# The build produces b2.bin under build/bootrom/ once it completes.
```

---

## Why the binaries are committed rather than fetched

Both projects are open-source with permissive licenses that allow binary
redistribution. Bundling avoids:

* A network dependency at test/CI time (the GitHub Actions artefacts
  expire and the upstream repos don't publish stable per-commit
  release downloads of these specific files).
* A multi-minute build step in CI for two small artefacts (`b2.bin`
  is 16 KB, `bus_pirate5_rev10.bin` is ~900 KB).

If a future rev of either binary is needed, replace the file in this
directory and update the SHA reference if one is added.
