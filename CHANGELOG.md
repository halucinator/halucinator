# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Multi-Architecture Emulation

HALucinator previously only supported ARM (Cortex-M3/M4) targets. This branch
extends the emulation framework to five architectures:

- **ARM32** (Cortex-M3, Cortex-A class via `arm` arch type)
- **ARM64** (AArch64, e.g. Cortex-A53)
- **MIPS** (MIPS32 big-endian, e.g. 4Kc)
- **PowerPC** (32-bit, e.g. MPC604, MPC8XX/e500)
- **PowerPC64** (64-bit ELFv2, e.g. POWER8)

Each architecture has its own `QemuTarget` subclass under `qemu_targets/`
(`ARM64QemuTarget`, `MIPSQemuTarget`, `PowerPCQemuTarget`,
`PowerPC64QemuTarget`) implementing calling-convention-aware argument reading,
heap management, and memory utilities. All targets share a common base class
`HALQemuTarget` that provides scratch memory allocation (`hal_alloc`/`hal_free`)
and string/buffer read helpers.

Architecture selection is driven by `target_archs.py`, which is now the single
source of truth mapping arch names to avatar2 arch objects, QEMU target classes,
environment variables (`HALUCINATOR_QEMU_{ARM,ARM64,MIPS,PPC,PPC64}`), and
default QEMU binary paths. This replaced duplicated lookup tables that were
previously scattered across `main.py`.

`init_sp` and `entry_addr` are now configurable for all architectures via YAML
config, not just Cortex-M3. For Cortex-M3, the vector table auto-extraction
(first 8 bytes of the binary) is preserved as a fallback when these values are
not explicitly set.

### Breakpoint Handler Libraries

Ported and expanded the BP handler ecosystem from the GT/TPCP codebase:

- **libopencm3** — ADC (`libopencm3_adc.py`), DMA (`libopencm3_dma.py`),
  flash programming (`libopencm3_flash.py`), GPIO (`libopencm3_gpio.py`),
  RCC clock config (`libopencm3_rcc.py`), SPI (`libopencm3_spi.py`),
  hardware timers (`libopencm3_timer.py`), USART (`libopencm3_usart.py`)
- **Atmel ASF v3** — Contiki OS integration (`contiki.py`), KSZ8851 Ethernet
  (`ethernet_ksz8851.py`), smart-connect WiFi (`ethernet_smart_connect.py`),
  high-level Ethernet (`hle_ethernet.py`), external interrupts
  (`ext_interrupt.py`), AT86RF233 radio transceiver (`rf233.py`),
  SD/MMC card (`sd_mmc.py`), EDBG stub (`edbg_stub.py`), timers, USART, radio
- **VxWorks** — boot, DOS filesystem, error handling, Ethernet, interrupt
  management, IOS device layer, POSIX logging, task scheduler, system clock,
  TTY device driver (`ty_dev.py`), VxWorks logging/memory, YAF filesystem
- **Zephyr RTOS** — UART poll/blocking I/O and console input
  (`zephyr_uart.py`), filesystem operations (`zephyr_fs.py`)
- **STM32F4 HAL** — base/boot/SysTick (`stm32f4_base.py`), UART, GPIO,
  timers, SPI, Ethernet, WiFi, SD card
- **mbed OS** — boot, serial I/O, timers
- **Generic** — `ReturnZero`, `ReturnConstant`, `SleepTime`, heap tracking,
  argument logging, function calling, libc stubs, newlib syscalls,
  printf-style debug output, counters, timers, basic I/O

The `debugger.py` handler adds GDB-integrated debugging with GTIRB-based
stack trace parsing, mapping raw addresses back to function names using
GrammaTech's IR format.

### Debug Adapter Protocol (DAP)

New `debug_adapter/` module implementing the DAP wire protocol for IDE
integration (VS Code, JetBrains, etc.):

- `debug_adapter.py` — Full DAP server handling launch, attach, threads,
  stack frames, scopes, variables, evaluate, and stepping
- `LineTranslator` — Maps disassembly view line numbers to instruction
  addresses so IDE breakpoints land on the correct instruction
- `variables.py` — Variable/scope/register inspection for the IDE's
  watch and locals panels

### IPython Debug Shell

`debug_shell.py` provides an interactive command-line debugging interface
with memory inspection, breakpoint management, register read/write, and
single-stepping — accessible when HALucinator is launched in debug mode.

### Peripheral Models and External Devices

New peripheral models:
- `adc.py` — Analog-to-digital converter with configurable channels
- `timer_model.py` — Hardware timer emulation
- `tcp_stack.py` — TCP/IP stack simulation
- `dos_fs_model.py` — DOS-compatible filesystem model
- `host_fs.py` — Host-backed filesystem for rehosting
- `sd_card.py` — SD card block device

New external device interfaces:
- `adc.py` — External ADC data source
- `opendps.py` — OpenDPS power supply control
- `vn8200xp.py` — VN8200XP accelerometer interface
- `trigger_interrupt.py` — Remote interrupt injection
- `publish_topic.py` — Generic ZMQ topic publisher

### Utilities

- `gtirb_common.py` — GTIRB binary analysis helpers: generate GTIRB files
  via ddisasm, extract functions, produce disassembly
- `parse_stack_trace.py` — Stack frame reconstruction from raw addresses
  using GTIRB and capstone for instruction decoding
- `parse_coverage.py` — Code coverage analysis from QEMU execution logs
- `profile_hals.py` — State recording into SQLite for post-mortem analysis

### CI/CD

GitHub Actions pipeline (`.github/workflows/virtual-environment-tests.yml`):

- Builds QEMU for all five architectures with GitHub Actions cache keyed on
  the avatar-qemu submodule commit (avoids ~20 min rebuild on cache hit)
- Runs STM32 UART e2e test, three Zephyr e2e tests (filesystem, frdm_k64f
  UART, olimex STM32 UART), and p2im-drone firmware rehosting
- Runs per-architecture e2e tests for ARM32, ARM64, MIPS, PPC, PPC64 with
  inter-test QEMU process cleanup
- Executes 28,600+ pytest tests with coverage reporting (84% line coverage),
  excluding `slow_zmq` and `needs_root` marked tests

### Test Suite

28,600+ tests across 151 test modules organized by component:

- **bp_handlers/** (59 files) — Tests for every handler library: STM32F4,
  libopencm3, Atmel ASF v3, VxWorks, Zephyr, mbed, and all generic handlers.
  Heavy use of `@pytest.mark.parametrize` for combinatorial coverage
  (e.g. debugger tests: 13 registers x 10 values per test function)
- **peripheral_models/** (30 files) — UART, Ethernet, GPIO, SPI, ADC,
  interrupts, SD card, host filesystem, TCP stack, and ZMQ server lifecycle
- **external_devices/** (20 files) — Serial tunneling, Ethernet ARP/hub,
  GPIO, interrupt triggering, 802.15.4 wireless, OpenDPS
- **debug_adapter/** (2 files, 152 tests) — DAP protocol request/response
  and variable inspection
- **qemu_targets/** (5 files) — Per-architecture QEMU target tests for
  ARM32, ARM64, MIPS, PPC, PPC64 including interrupt injection and
  register manipulation
- **config/** (4 files) — ELF loading, memory config parsing, symbol tables,
  architecture registry
- **util/** (8 files) — Symbol table parsing, coverage analysis, GTIRB
  integration, ELF helpers, MMIO function lookup

Test infrastructure includes `conftest.py` with ZMQ cleanup (disables
`zmq.Context.__del__` to prevent C-level aborts during GC), auto-applied
markers for CI-incompatible tests, and forced exit to avoid interpreter
shutdown hangs from zmq threads.

### Multi-Architecture E2E Tests

`test/multi_arch/` contains bare-metal UART firmware and HALucinator configs
for all five architectures. The firmware is compiled from a single portable
C source (`firmware_src/`) using cross-compilers:

| Arch   | Compiler                    | Flags                         |
|--------|-----------------------------|-------------------------------|
| ARM32  | `arm-linux-gnueabi-gcc`     | `-mthumb -mcpu=cortex-m3`    |
| ARM64  | `aarch64-linux-gnu-gcc`     | (standard)                    |
| MIPS   | `mips-linux-gnu-gcc`        | `-mno-abicalls -G0`          |
| PPC    | `powerpc-linux-gnu-gcc`     | (standard)                    |
| PPC64  | `powerpc64-linux-gnu-gcc`   | `-mabi=elfv2`                 |

Each architecture has its own linker script, memory map YAML, address YAML,
and run script. Tests verify that HALucinator can load firmware, set up the
stack pointer and entry point, hit the UART breakpoint handler, and produce
expected output.

### Build and Infrastructure

- `build_qemu.sh` builds all five QEMU system targets (`arm-softmmu`,
  `aarch64-softmmu`, `mips-softmmu`, `ppc-softmmu`, `ppc64-softmmu`)
  with `--extra-cflags="-Wno-error"` for GCC 13+ compatibility
- Dockerfile updated for multi-arch QEMU builds and all five env vars
- VSCode extension support via `parse_bp_handlers.py` installer
- Console entry points: `halucinator`, `hal_make_addr`, `hal_dev_uart`,
  `hal_dev_virt_hub`, `hal_dev_eth_wireless`, `hal_dev_host_eth`,
  `hal_dev_host_eth_server`, `hal_dev_802_15_4`, `hal_dev_irq_trigger`,
  `qemulog2trace`

### Changed

- Default GDB executable changed from `arm-none-eabi-gdb` to `gdb-multiarch`
- `main.py` refactored: architecture configuration now delegated to
  `target_archs.py` instead of inline lookup tables; `init_sp` written to
  `qemu.regs.sp` for all architectures (previously Cortex-M3 only);
  `entry_addr` passed to avatar2 as `entry_address` parameter
- `peripheral_server.py`: `bytes(key)` changed to `key.encode("utf-8")`
  for Python 3 zmq compatibility
- `hal_dev_uart`: handles `EOFError` when stdin is `/dev/null`
- QEMU path resolution uses architecture-specific environment variables
  instead of a single hardcoded path

### Fixed

- `gpio.py`: `setsockopt` changed to `setsockopt_string` (Python 3 zmq API)
- `host_ethernet_server.py`: added missing `msg_id` parameter, fixed
  undefined variable references
- VxWorks, Zephyr, and STM32F4 handler bug fixes from GT/TPCP port
- Cortex-M3 init: respects explicit `entry_addr`/`init_sp` from config
  instead of always overwriting with vector table values
- MIPS: uses physical addresses (kseg0 virtual addresses don't work with
  avatar2's configurable machine)
- MIPS: uses `MIPS_BE` arch (has `qemu_name` defined) instead of `MIPS32`
- Mock assertion typo in libopencm3 GPIO test (`called_once_with` →
  `assert_called_once_with`) — was silently passing on Python ≤3.11

## [1.8.0] - 2024-01-23

### Added
- **C intercepts**: HALucinator can now use compiled C code to intercept and
  replace firmware functions, in addition to Python breakpoint handlers. A
  bare-metal ELF program is injected into unused emulator memory and firmware
  instructions are rewritten to branch into it. This provides significantly
  higher performance for hot-path interception at the cost of reduced access
  to host resources. Documented in `doc/c_intercepts.md`. Includes a C driver
  framework under `src/halucinator/drivers/` with headers for VirtIO serial
  console, VirtIO network, SP804 timer, and the halucinator-irq controller.
- **QEMU device injection**: Memory regions in the YAML config can now specify
  `qemu_name` and `properties` to instantiate real QEMU device models (e.g.
  SP804 timer, VirtIO serial) inside the emulated machine, with `irq` config
  to wire interrupt lines between devices and the CPU.
- **PowerPC support**: New `PowerPCQemuTarget` with calling-convention-aware
  argument reading (r3–r10), heap management, and memory utilities. Supports
  PPC32 and MPC8XX/e500 machine types via avatar2's `PPC32` and
  `PPC_MPC8544DS` archs.
- **AARCH64 support**: New `ARM64QemuTarget` for 64-bit ARM emulation
  (e.g. Cortex-A53), with its own argument reading and memory primitives.
- **Generic IRQ controller**: `halucinator-irq` QEMU device that provides a
  configurable interrupt controller for any architecture, connectable to CPU
  IRQ lines via YAML config. Documented in `doc/irq_config.md`.
- **Configuration refactor**: New `config/` package with `target_archs.py`
  (architecture registry), `elf_program.py` (ELF loading and C intercept
  config), `memory_config.py` (memory region definitions), and
  `symbols_config.py` (symbol table configuration).
- **New generic handlers**: `basic_io.py` (simple I/O intercepts),
  `heap_tracking.py` (malloc/free tracking with statistics), `libc.py`
  (memcpy, memset, strlen stubs), `newlib_syscalls.py` (newlib _sbrk, _write)
- **Ghidra scripts**: `export_calling_stubs.py` (generate C calling stubs
  from Ghidra analysis), `export_symbol_yaml.py` (export symbols as YAML)
- **Build infrastructure**: `build_qemu.sh` for building QEMU from the
  avatar-qemu submodule for any target architecture; `reformat.sh` and
  `show_linted_files.sh` for code quality; `.pylintrc` and `.yamllint`
  configs
- `qemulog2trace` console entry point for converting QEMU execution logs

### Changed
- **VxWorks handlers substantially rewritten**: `dos_fs.py`, `errors.py`
  (expanded from 731 to ~1400 lines of error definitions), `ethernet.py`,
  `ios_dev.py`, `scheduler.py`, `tasks.py`, `ty_dev.py` all received major
  rewrites with improved state management and bug fixes. Added `vx_mem.py`
  (memory allocation) and `yaf_fs.py` (YAF filesystem).
- **Zephyr handlers rewritten**: `zephyr_fs.py` and `zephyr_uart.py`
  expanded with improved state tracking and additional operations.
- `bp_handler.py` expanded (40 → 220+ lines): `BPStruct` utility for
  C-like struct packing/unpacking, improved handler base class with
  model instantiation.
- `intercepts.py` rewritten (164 → 400+ lines): added watchpoint support,
  class caching, configurable return behavior, and `remove_bp_handler()`.
- `generic/debug.py` expanded with IPython integration and richer
  memory/register inspection commands.
- `generic/common.py` expanded (94 → 450+ lines): added `ReturnValue`,
  `ReturnConstant`, `SleepTime`, and configurable return handlers.
- Updated avatar-qemu submodule for multi-arch QEMU support
- Updated Dockerfile for multi-arch builds
- README rewritten with architecture support matrix, setup instructions,
  and C intercept documentation

## [1.7.0] - 2021-07-08

### Added
- **VxWorks RTOS support**: Complete breakpoint handler library for VxWorks
  real-time operating system under `bp_handlers/vxworks/`:
  - `boot.py` — System boot and initialization sequence
  - `dos_fs.py` — DOS-compatible filesystem operations
  - `errors.py` — VxWorks error code definitions and handling (731 error codes)
  - `ethernet.py` — Network interface and packet handling
  - `interrupts.py` — Interrupt enable/disable and context management
  - `ios_dev.py` — I/O system device driver layer (create, open, read, write)
  - `posix_logging.py` — POSIX-style logging interface
  - `scheduler.py` — Task scheduling and context switching
  - `sys_clock.py` — System clock and tick management
  - `tasks.py` — Task creation, deletion, and state management
  - `ty_dev.py` — TTY terminal device driver with input/output buffering
  - `vx_logging.py` — VxWorks native logging
- **New external devices**:
  - `console_ty.py` — Console TTY terminal interface
  - `ethernet_arp_request.py` — ARP request generation and handling
  - `publish_topic.py` — Generic ZMQ topic publisher for event injection
  - `serial_tunnel.py` — Serial port tunneling between host and emulator
  - `tty.py` — TTY terminal device interface
  - `utty.py` — Universal TTY abstraction supporting multiple backends
- **New peripheral models**:
  - `utty.py` — Universal TTY model with configurable buffering
- **Docker support**: Dockerfile for containerized development environment
  with all dependencies pre-installed, plus README instructions for
  Docker-based setup
- **ARM QEMU target rewrite**: `arm_qemu.py` expanded from ~50 lines to
  326+ lines with `HALQemuTarget` base class providing scratch heap
  allocation (`hal_alloc`/`hal_free`), string/buffer reading utilities,
  and calling-convention-aware `get_arg()` for function argument access
- Expanded `generic/common.py` with `ReturnValue` handler and improved
  `function_callers.py` with large parameter passing support
- `hal_config.py` expanded with additional config validation and memory
  permission parsing
- Five-part tutorial documentation (`doc/tutorial/`):
  0. Prerequisites
  1. HALucinator architecture overview (with component diagram)
  2. Running the UART example (step-by-step with screenshots)
  3. UART deep dive (how configs, handlers, and peripherals connect)
  4. Extending HALucinator (building a custom LED handler from scratch)
- Tutorial includes a complete `hal_tutorial` package with LED BP handler,
  peripheral model, and external device as a worked example
- Ghidra script for exporting symbol CSVs (`ghidra_scripts/export_symbol_csv.py`)

### Changed
- `peripheral_models/ethernet.py` expanded with improved packet handling
  and multiple interface support
- `peripheral_models/interrupts.py` expanded with richer interrupt state
  tracking and enable/disable semantics
- `peripheral_server.py` updated with improved ZMQ message routing
- Replaced `create_venv.sh` and `scripts/setup.sh` with unified
  `install_deps.sh` and simplified `setup.sh`
- `host_ethernet_server.py` simplified from 120 to ~60 lines
- `src/setup.py` updated with new console entry points: `hal_dev_uart`,
  `hal_dev_virt_hub`, `hal_dev_eth_wireless`, `hal_dev_host_eth`,
  `hal_dev_host_eth_server`, `hal_dev_802_15_4`, `hal_dev_irq_trigger`
- Updated avatar2 and avatar-qemu submodule dependencies

## [1.6.0] - 2020-10-15

### Added
- **Tutorial documentation** in `doc/tutorial/` — five-part guide covering
  the full workflow from prerequisites through extending HALucinator with
  custom handlers:
  - Part 0: Prerequisites (Python, Ghidra, cross-compiler setup)
  - Part 1: HALucinator architecture overview with component diagram
  - Part 2: Running the STM32 UART example end-to-end (with terminal
    screenshots showing UART I/O)
  - Part 3: Deep dive into the UART example — how YAML configs, breakpoint
    handlers, peripheral models, and external devices connect
  - Part 4: Extending HALucinator — building a custom LED handler, peripheral
    model, and external device from scratch, with a `hal_tutorial` package
    containing both skeleton and solution code
- **Zephyr RTOS support**: `zephyr_uart.py` (poll-in/out, console line
  input) and `zephyr_fs.py` (filesystem open, read, write, close) handlers,
  plus e2e test configs under `test/zephyr/` for the frdm_k64f and
  olimex_stm32_h103 boards
- Expanded `hal_config.py` with improved YAML config merging when multiple
  `-c` config files are passed on the command line
- STM32F4 UART handler (`stm32f4_uart.py`) updated with receive interrupt
  support
- Additional external device ZMQ topic routing in `ioserver.py`

### Changed
- README expanded with tutorial links, updated dependency instructions,
  and clearer setup steps
- `install_deps.sh` replaces the old `create_venv.sh` workflow
- `generic/common.py` minor fix to `ReturnZero` handler
- `intercepts.py` improved handler class lookup error messages
- STM32 UART example config (`Uart_Hyperterminal_IT_O0_config.yaml`)
  restructured for clarity

## [1.5.0] - 2020-09-15

### Changed
- Pinned scapy dependency updated from `==2.4.0` to `==2.4.4` — the
  original pin was a workaround for a scapy build error that was fixed
  upstream in 2.4.3rc3 (resolves issue #3, contributed by Antony Vennard)
- Setup script and README maintenance improvements
- Minor fixes to `elf_sym_hal_getter.py` output formatting

## [1.4.0] - 2019-11-19

### Added
- `Developing.md` with contributor guidelines and development workflow
- `build_keystone.sh` script for building keystone from source (the pip
  `keystone-engine` package requires a local build of the native library)
- `generic/argument_loggers.py` — new handler that logs function arguments
  with configurable format strings (replaces the ARM-specific
  `armv7m_param_log.py`)
- `generic/common.py` — consolidated common handlers: `ReturnZero`,
  `SleepTime`, and other basic return-value intercepts
- `generic/function_callers.py` — handlers that call target functions
  from within a breakpoint, enabling composed interception workflows
- Generic handler `__init__.py` exports for cleaner imports

### Changed
- Consolidated build system: removed separate `avatar2`, `avatar-qemu`,
  and `keystone` git submodules in favor of pip-installed dependencies
  with `setup.sh` handling the full install sequence
- `intercepts.py` refactored with improved handler class resolution,
  better error messages, and cleaner breakpoint registration flow
- `generic/debug.py` expanded with additional memory inspection commands
  and improved IPython shell integration
- BP handler base class (`bp_handler.py`) updated with cleaner model
  instantiation
- Updated to latest avatar2 with Ubuntu 18.04 compatibility fixes
- `main.py` improved QEMU logging options and target initialization

### Fixed
- Python 3 compatibility fixes across the codebase:
  - `gpio.py`: raw socket operations updated for bytes API
  - `host_ethernet.py`, `host_ethernet_server.py`: fixed string/bytes
    handling in network packet processing
  - `ioserver.py`: ZMQ socket option calls updated for Python 3 API
  - `peripheral_models/uart.py`: fixed byte encoding in UART TX/RX
- Build system fixes for `setup.sh` and `create_venv.sh` on various
  Ubuntu versions
- Copyright statement formatting cleanup

## [1.3.0] - 2019-07-16

### Changed
- **Python 3 conversion** (PR #2 by Antony Vennard / Teserakt-io): Full
  codebase port from Python 2 to Python 3, covering all source files
  (80 files, ~3,700 lines changed). Key changes:
  - Print statements → print functions throughout
  - String/bytes handling updated for Python 3 semantics
  - Dictionary `.items()` / `.keys()` / `.values()` return views
  - Integer division with `//` where needed
  - PEP 8 formatting applied across all modules
- Added `.editorconfig` for consistent formatting
- Added `.gitmodules` with avatar2 and avatar-qemu as git submodules under
  `deps/`
- Setup scripts moved from root to `scripts/` directory
- `doc/setup.md` added with detailed installation instructions

## [1.2.0] - 2019-07-05

### Added
- **Keystone assembler support** (contributed by Antony Vennard /
  Teserakt-io): Added keystone as a git submodule under `deps/keystone`.
  Keystone is a dependency of avatar2 used for runtime instruction assembly
  (e.g. writing branch instructions to redirect firmware execution).
  Includes build instructions in `doc/setup.md` since the pip
  `keystone-engine` package requires a separately built native library.
- **Safe YAML loading**: `main.py` switched from `yaml.load()` to
  `yaml.safe_load()` to prevent arbitrary code execution from malicious
  config files
- **ZMQ socket fix**: `ioserver.py` updated ZMQ socket initialization for
  compatibility with newer pyzmq versions

## [1.1.0] - 2019-07-03

### Added
- **Python 3 support** (contributed by Antony Vennard / Teserakt-io):
  Initial Python 3 compatibility pass across the codebase with `print()`
  function calls, updated string handling, and PEP 8 formatting. This
  was the first of two Python 3 PRs — 1.1.0 established basic
  compatibility, while 1.3.0 completed the full conversion.
- Code reorganization: source files moved into a proper `src/halucinator/`
  package layout with `__init__.py` files, `setup.py` for pip installation,
  and `requirements.txt` for dependency management

## [1.0.0] - 2019-06-06

### Added
- **Initial public release** of HALucinator — a firmware rehosting framework
  that emulates embedded firmware by intercepting Hardware Abstraction Layer
  (HAL) function calls and replacing them with host-side Python models.
  Originally developed at Sandia National Laboratories (NTESS) under
  Contract DE-NA0003525.
- **Breakpoint handler framework** (`bp_handlers/`): Intercepts firmware
  function calls at configured addresses via QEMU breakpoints. Handlers
  read function arguments from registers, interact with peripheral models,
  and return values — allowing firmware to run without real hardware.
  Includes handler libraries for:
  - STM32F4 HAL (UART, GPIO, SPI, Ethernet, WiFi, SD card, timers)
  - Atmel ASF v3 (USART, Ethernet KSZ8851, smart-connect WiFi, RF233
    radio, SD/MMC, timers, external interrupts, EDBG stub, Contiki OS)
  - ARM mbed (boot, serial, timer)
  - Generic handlers (debug shell, counter, timer, ARM parameter logging)
- **Peripheral model framework** (`peripheral_models/`): Stateless device
  models that emulate hardware behavior. Connected to breakpoint handlers
  and external devices via ZMQ pub/sub messaging. Includes models for
  UART (TX/RX buffering), Ethernet (packet routing), GPIO, SPI, SD card,
  IEEE 802.15.4 wireless, interrupts, TCP stack, and generic I/O.
- **External device framework** (`external_devices/`): Separate processes
  that communicate with peripheral models over ZMQ, enabling real-time
  I/O with the emulated firmware. Includes UART terminal
  (`hal_dev_uart`), virtual Ethernet hub, wireless Ethernet bridge,
  host Ethernet passthrough, GPIO interface, 802.15.4 radio, interrupt
  trigger, and VN8200XP accelerometer.
- **QEMU ARM target** (`qemu_targets/arm_qemu.py`): Cortex-M3 emulation
  via avatar2 orchestration framework, with vector table extraction for
  automatic `init_sp` and `entry_addr` detection, CPSR thumb bit fixup,
  and NVIC interrupt injection.
- **Configuration via YAML**: Machine definition (architecture, CPU model,
  memory map), intercept addresses (symbol → handler class mapping), and
  peripheral wiring all specified in composable YAML config files passed
  via `halucinator -c config1.yaml -c config2.yaml`.
- **GUI** (`gui.py`): Curses-based mapping/symbol browser for interactive
  exploration of firmware symbols and intercept configurations.
- **Profiling** (`util/profile_hals.py`): `State_Recorder` that captures
  register and memory state into SQLite during emulation for post-mortem
  analysis.
- **STM32 e2e examples**: UART hyperterminal (DMA and interrupt modes),
  Ethernet TCP/UDP client/server, SD card FAT filesystem, and st-plc
  industrial controller — each with run scripts and full YAML configs
  under `test/STM32/`.
