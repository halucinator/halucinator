# Provenance — demo/opendps/

This directory ships two prebuilt artifacts used by the halucinator demo:

- `opendps.elf` — a demo firmware build of the OpenDPS project.
- `libopencm3_stm32f1.a` — a prebuilt static archive of libopencm3 for STM32F1.

Both were built from the shell environment of user `ddehaas` on a
checkout at `/home/ddehaas/rep/PERETI/OpenDPS/opendps/` (discoverable
via `strings(1)` in each binary).

## opendps.elf

- **Upstream project:** [kanflo/opendps](https://github.com/kanflo/opendps)
  — "Give your DPS5005 the upgrade it deserves"
- **Upstream license:** MIT  (see
  [LICENSE](https://github.com/kanflo/opendps/blob/master/LICENSE))

The linked binary also pulls in these third-party libraries, which are
statically compiled into the ELF image:

| Library   | License                                       |
|-----------|-----------------------------------------------|
| libopencm3 | LGPL-3.0-or-later                             |
| newlib    | BSD-family (see upstream, file-by-file)        |
| libgcc    | GPL-3.0 + GCC Runtime Library Exception        |

Anyone redistributing `opendps.elf` should honor each component's
license; the LGPL obligation for libopencm3 is normally satisfied by
keeping the relocatable object form (a static archive linked into an
ELF) plus the LGPL notice included here.

## libopencm3_stm32f1.a

- **Upstream project:** [libopencm3/libopencm3](https://github.com/libopencm3/libopencm3)
  — "Open source ARM Cortex-M firmware library"
- **Upstream license:** LGPL-3.0-or-later  (see
  [COPYING.LGPL3](https://github.com/libopencm3/libopencm3/blob/master/COPYING.LGPL3)).
  The README: "The libopencm3 code is released under the terms of the
  GNU Lesser General Public License (LGPL), version 3 or later."

Shipping this archive in binary form inside a GPL-incompatible product
would require distributing the corresponding libopencm3 sources
(or a written offer per LGPL §6). For the halucinator demo (research
tooling), the archive is provided as-is for running OpenDPS under
emulation.
