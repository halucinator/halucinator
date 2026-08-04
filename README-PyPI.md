# HALucinator

**Firmware rehosting through abstraction layer modeling.**

HALucinator runs embedded firmware binaries without the original hardware. Rather
than emulating every peripheral register, it intercepts calls to the firmware's
*hardware abstraction layer* — `HAL_UART_Transmit`, `spi_write`, and friends —
and answers them in Python. Replacing a handful of HAL functions is usually
enough to boot a binary that would otherwise fault immediately on missing
hardware, which makes firmware reachable for debugging, analysis, and fuzzing.

## Install

```sh
pip install halucinator[unicorn]
```

The `unicorn` extra is the self-contained install: it is the only backend that
needs no externally-built binary, so this yields a working rehosting setup from
pip alone.

| Extra | Installs | For |
| --- | --- | --- |
| `[unicorn]` | unicorn, capstone | The pip-only backend. Start here. |
| `[net]` | scapy, pyserial | Host network / serial external devices |
| `[symbols]` | cle | ELF symbol extraction via `hal_make_addr` |
| `[mcp]` | mcp[cli] | The `halucinator-mcp` server |
| `[all]` | all of the above | Everything except the dev tooling |

Requires Python 3.10 or newer.

### Other backends

HALucinator supports several execution backends, selected with `--emulator`:
`unicorn`, `qemu`, `avatar2`, `libafl-qemu`, `renode`, and `ghidra`. Only
`unicorn` is installable purely from PyPI — the others depend on an external
QEMU build, a Renode install, or the avatar2 fork tracked in the source
repository. See the [repository
README](https://github.com/halucinator/halucinator#readme) for those setups.

## Usage

Emulation is driven by YAML configuration. Configs are conventionally split into
three files — memory layout, intercepts, and a symbol/address map — which
HALucinator concatenates, with later files taking precedence:

```sh
halucinator -c memory.yaml -c intercepts.yaml -c addresses.yaml --emulator unicorn
```

Splitting them this way keeps the intercept list portable across builds of the
same firmware: only the address map changes when the binary is recompiled.

### Configuration sketch

Memory layout and emulated peripherals:

```yaml
memories:
  flash: {base_addr: 0x8000000, size: 0x200000, permissions: r-x, file: firmware.bin}
  ram:   {base_addr: 0x20000000, size: 0x51000}
peripherals:
  logger: {base_addr: 0x40000000, size: 0x20000000, permissions: rw-, emulate: GenericPeripheral}
```

What to intercept, and which handler answers it:

```yaml
intercepts:
  - class: halucinator.bp_handlers.stm32f4.stm32f4_uart.STM32F4UART
    function: HAL_UART_Transmit
    symbol: HAL_UART_Transmit
  - class: halucinator.bp_handlers.generic.common.ReturnZero
    function: HAL_RCC_OscConfig
    symbol: HAL_RCC_OscConfig
```

`function` selects the handler method (matched against the handler's
`@bp_handler` decorator); `symbol` resolves the address from the symbol map, so
prefer it over hardcoding `addr`. Handlers accept `class_args` for
per-intercept configuration.

The full schema — machine settings, watchpoints, `registration_args`,
`run_once`, and the peripheral model list — is documented in the [repository
README](https://github.com/halucinator/halucinator#readme).

## Handler families

Prebuilt intercept handlers ship for common HALs and RTOSes:

- **generic** — `ReturnZero`, `ReturnConstant`, `SkipFunc`, counters, timers
- **stm32f4** — STM32F4 HAL: UART, GPIO, SPI, ethernet, timers, WiFi
- **libopencm3** — ADC, DMA, flash, GPIO, RCC, SPI, timer, USART
- **atmel_asf_v3** — Atmel ASF: contiki, ethernet, radio, SD/MMC, timers, USART
- **mbed** — Mbed OS: boot, serial, timer
- **vxworks** — VxWorks: boot, filesystem, ethernet, interrupts, scheduler, tasks
- **zephyr** — Zephyr: filesystem, UART

## Command-line tools

| Command | Purpose |
| --- | --- |
| `halucinator` | Run an emulation from config files |
| `hal_make_addr` | Extract a symbol/address map from an ELF (needs `[symbols]`) |
| `hal_dev_uart` | UART external device — the interactive console |
| `hal_dev_host_eth`, `hal_dev_host_eth_server` | Bridge emulated ethernet to the host |
| `hal_dev_virt_hub`, `hal_dev_eth_wireless` | Virtual network hub and wireless link |
| `hal_dev_802_15_4` | IEEE 802.15.4 radio device |
| `hal_dev_irq_trigger` | Inject interrupts into a running emulation |
| `halucinator-mcp` | MCP server for agent-driven rehosting (needs `[mcp]`) |
| `qemulog2trace` | Convert QEMU logs to execution traces |

External devices communicate with the emulator over ZeroMQ, so they run as
separate processes — typically one terminal for `hal_dev_uart` and another for
`halucinator` itself.

## Supported architectures

ARM Cortex-M · ARM (full, e.g. arm926) · AArch64 · MIPS · PowerPC · PowerPC64

## Documentation

Full documentation, worked examples, the VSCode extension and debug-adapter
integration, and the developer setup live in the source repository:

**https://github.com/halucinator/halucinator**

## License and credit

GPL-3.0-or-later.

HALucinator was created at Sandia National Laboratories by Abraham Clements and
the Sandia HALucinator team, with later contributions from the GrammaTech
HALucinator team and Christopher Wright. The complete contributor list is the
project's git history.

Sandia National Laboratories is a multimission laboratory managed and operated
by National Technology & Engineering Solutions of Sandia, LLC, a wholly owned
subsidiary of Honeywell International Inc., for the U.S. Department of Energy's
National Nuclear Security Administration under contract DE-NA0003525.
