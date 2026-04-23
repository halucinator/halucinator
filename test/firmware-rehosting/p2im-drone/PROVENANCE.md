# Provenance — test/firmware-rehosting/p2im-drone/

This directory contains `Drone.elf` and `Drone.bin`, used as an
end-to-end firmware test for halucinator. The binary is a
statically-linked Cortex-M3 image (STM32F103C8 target) built from
several upstream sources. This file records every attribution I could
trace; some links were discovered by reading strings out of the ELF,
others by walking authors' publicly-visible repositories.

---

## Build context (from `strings(1)` on the ELF)

| Field              | Value                                                      |
|--------------------|------------------------------------------------------------|
| Local compile user | `tneale`                                                   |
| Toolchain          | STM32CubeIDE 1.12.1, `arm-none-eabi-gcc 10.3.1 (2021-08-24)` |
| Flags              | `-mcpu=cortex-m3 -mfloat-abi=soft -mthumb -march=armv7-m -g3 -Os` |
| Source tree at build | `~/GitHub/RiS3-Lab/p2im/externals/p2im-real_firmware/Drone/` |
| Rough date         | 2023 (toolchain release date is Aug 2021; STM32CubeIDE 1.12.1 was Jan 2023) |

So the binary was built by user `tneale` from the P²IM paper's
submoduled `p2im-real_firmware` tree. That tree in turn mirrors an
e-Yantra Summer Internship Program 2017 (eYSIP) project.

---

## Firmware source — primary flight controller code

The source files whose `.c` paths are baked into the ELF's debug info
are all Heethesh Vhavle's eYSIP code. Identical copies live in two
public repos:

| Repo (both verified identical) | Kind | License | Created |
|---|---|---|---|
| [`eYSIP-2017/eYSIP-2017_Control_and_Algorithms_development_for_Quadcopter`](https://github.com/eYSIP-2017/eYSIP-2017_Control_and_Algorithms_development_for_Quadcopter) | e-Yantra organization's authoritative publication | **None** (no LICENSE file, GitHub `NOASSERTION`) | 2017-05-25 |
| [`heethesh/eYSIP-2017_Control_and_Algorithms_development_for_Quadcopter`](https://github.com/heethesh/eYSIP-2017_Control_and_Algorithms_development_for_Quadcopter) | Author's personal mirror | **None** (same) | 2017-07-13 |

**Project metadata** (from headers in `main.c` and the upstream README):

- **Project name:** Firmware V101-103C8
- **Product name:** "Firmware for Pluto Drone, Version 1.0.1A"
- **Intern / author:** Heethesh Vhavle ([@heethesh](https://github.com/heethesh))
- **Mentors:** Sanam Shakya, Pushkar Raj
- **Internship duration:** 2017-05-22 through 2017-07-07
- **Internship program:** [e-Yantra Summer Internship Program 2017 (eYSIP)](https://www.e-yantra.org/), run by the e-Yantra Lab at IIT Bombay
- **Project title:** "Control and Algorithms Development for Quadcopter"
- **Demonstration playlist:** https://www.youtube.com/playlist?list=PLpmYCSzmshOh5sILdRdHFuRQIxO0Ilp0M

**Per-file authorship (from source-file banners):**

| File (`Src/…`)       | Author          | Date          | Notes |
|----------------------|-----------------|---------------|-------|
| `main.c`             | Heethesh Vhavle | 2017-06-07    | Mentors Shakya & Raj named. |
| `MPU9250.c`          | Heethesh        | 2017-05-31    | IMU driver (Invensense MPU-9250). |
| `MS5611.c`           | Heethesh        | 2017-06-22    | Barometer driver (TE/MEAS MS5611). |
| `msp.c` (as `multiwii.c`) | Heethesh   | 2017-06-08    | MultiWii Serial Protocol handler. |
| `pid.c`              | Heethesh        | 2017-06-15    |       |
| `motor.c`            | Heethesh        | 2017-06-17    |       |
| `devices.c`          | Heethesh        | 2017-06-07    |       |
| `peripherals.c`      | Heethesh        | 2017-06-07    |       |
| `joystick.c`         | Heethesh        | 2017-06-26    |       |
| `common.c`           | Heethesh        | 2017-06-15    |       |
| `serial.c` (as `serial_new.c`) | Heethesh | 2017-06-12  |       |
| `timing.c`           | Heethesh        | 2017-06-19    |       |
| `i2c.c`              | Heethesh        | 2017-06-22    |       |
| `circular_buffer.c`  | Heethesh        | 2017-06-13    |       |
| `telemetry.c`        | Heethesh        | 2017-06-04    | Marked "Library deprecated, use MSP Debug Frame". |
| `MadgwickAHRS.c`     | *(no header in eYSIP copy)* | see below | Sebastian Madgwick's AHRS algorithm. |

**License status (primary flight-controller code).** Neither the
e-Yantra organization repo nor Heethesh's personal mirror ships a
`LICENSE`, `COPYING`, or `NOTICE` file, and GitHub classifies both as
license `None`. Under GitHub's default terms of service, this means
viewing is permitted but redistribution / modification / derivative
works are *not* licensed. Practically, `Drone.elf` and `Drone.bin`
should be treated as **"all rights reserved by Heethesh Vhavle"** until
a license is added upstream.

---

## Firmware hardware target (not a code dependency, for context)

The firmware targets **Drona Aviation's Pluto Drone** (STM32F103C8
flight controller). The code here is *not* derived from Drona's own
firmware — it's a student implementation built from scratch during
eYSIP 2017 using Drona's published pinout and datasheets. Drona
Aviation ([@DronaAviation](https://github.com/DronaAviation)) maintains
separate Pluto-related repos (some MIT, some Apache-2.0, many
unlicensed) but none of those are inputs to this binary.

---

## Instrumentation added by P²IM

| Component        | Origin / provenance                                       |
|------------------|-----------------------------------------------------------|
| `afl_call.c` + `afl_call.h` | Added by P²IM for fuzzer instrumentation. Paste-block reproduced verbatim in `p2im/docs/prep_fw_for_fuzzing.md`; that doc says the snippet was *inherited from [TriforceAFL](https://github.com/nccgroup/TriforceAFL)* (NCC Group, Apache-2.0). |

**P²IM repositories:**

| Repo | What | License |
|---|---|---|
| [`RiS3-Lab/p2im`](https://github.com/RiS3-Lab/p2im) | P²IM framework (paper code) | **Apache-2.0** — "Unless otherwise stated, the P2IM framework is released under Apache License Version 2.0." |
| [`RiS3-Lab/p2im-real_firmware`](https://github.com/RiS3-Lab/p2im-real_firmware) | Companion firmware repo that includes `Drone/` | **NOASSERTION** — its `LICENSE.md` reads verbatim "The firmware in this repo are collected from public github repos or private sources. Please refer to their original repo for license information." |

**P²IM paper:** *P²IM: Scalable and Hardware-independent Firmware
Testing via Automatic Peripheral Interface Modeling*, Bo Feng, Alejandro
Mera, Long Lu, USENIX Security '20.
https://www.usenix.org/conference/usenixsecurity20/presentation/feng

---

## Third-party code compiled into `Drone.elf`

| File / path                 | Origin                     | License (as stated in the source header in this project) |
|-----------------------------|----------------------------|---|
| `Src/MadgwickAHRS.c`, `Inc/MadgwickAHRS.h` | Sebastian O. H. Madgwick, *Madgwick AHRS* algorithm (≈2010). Original site: [x-io.co.uk/open-source-imu-and-ahrs-algorithms/](https://x-io.co.uk/open-source-imu-and-ahrs-algorithms/) | **No header present** in the eYSIP copy. Madgwick's original release was distributed as public-domain / "free to use" per the x-io page; the stripped-down version shipped here should ideally re-add the upstream header. |
| `Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal*.c` | STMicroelectronics HAL for STM32F1 (MCD Application Team, V1.0.4, 29-April-2016) | **BSD-3-Clause** — "Copyright(c) 2016 STMicroelectronics" + the standard 3-clause BSD text. (Newer STM32Cube packages ship under SLA0044, but this vintage is genuine BSD-3-Clause.) |
| `Src/system_stm32f1xx.c`    | STMicro CMSIS device implementation (MCD App Team, V4.1.0, 29-April-2016) | BSD-3-Clause, same as above. |
| `startup/startup_stm32f103xb.s` | STMicro Cortex-M3 startup for Atollic toolchain (MCD App Team, V4.1.0, 29-April-2016) | BSD-3-Clause, same. |
| `Drivers/CMSIS/Include/core_cm3.h` et al. | ARM Cortex Microcontroller Software Interface Standard (CMSIS), ARM Limited, V4.30, 20 Oct 2015 | BSD-3-Clause — "Copyright (c) 2009 - 2015 ARM LIMITED". (ARM re-licensed CMSIS to Apache-2.0 in CMSIS 5+; the version here predates that switch.) |
| `Drivers/CMSIS/Device/ST/STM32F1xx/...` | STMicro CMSIS device-specific headers (V4.1.0) | BSD-3-Clause, Copyright STMicroelectronics 2016. |
| Newlib C runtime (pulled by linker; paths `newlib/libc/...` visible in ELF) | Cygwin / Red Hat newlib | BSD-family, license per file (mix of 2- and 3-clause BSD, some permissive). |
| `libgcc` (paths `src/libgcc/config/arm/...`) | GCC runtime support | **GPL-3.0 with GCC Runtime Library Exception** (so static-linking into a non-GPL binary is permitted). |

---

## License status — summary

The single remaining license gap is Heethesh Vhavle's eYSIP code,
which is the bulk of the firmware. Every other input has a recognizable
open-source license with straightforward redistribution terms.

### Recommended next steps (not yet taken)

Pick one:

1. **Contact the authors** — the [e-Yantra project](https://www.e-yantra.org/)
   or Heethesh directly ([@heethesh](https://github.com/heethesh)) — and
   ask for an SPDX-identifiable license (MIT or Apache-2.0 would match
   halucinator's research-tool conventions). When granted, copy the
   upstream LICENSE file here.
2. **Replace the firmware** with something whose license is explicit and
   compatible — for example, an ST STM32CubeF4 example (BSD-3-Clause),
   a Zephyr sample (Apache-2.0), or a libopencm3 demo (LGPL-3+).
3. **Remove the binary** and have the test script fetch it from upstream
   at test time, with a note explaining the license situation so
   downstream mirrors don't pick it up blindly.

Until one of these is done, anyone redistributing halucinator should
assume `Drone.elf` / `Drone.bin` carries no redistribution grant.
