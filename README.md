# HALucinator - Firmware rehosting through abstraction layer modeling.

## Supported Architectures

- ARM Cortex-M (cortex-m3, cortex-m4, etc.)
- ARM (full, e.g. arm926)
- AARCH64
- MIPS
- PowerPC (PPC)
- PowerPC 64 (PPC64)

## Setup in Docker

Clone this repo and submodules for avatar2 and qemu:
```bash
git clone <this repo>
git submodule update --init
```
A recursive clone can be done, but QEMU will then pull a lot of submodules that
may not be needed. QEMU's build process will pull the needed modules.

Build and run (defaults to Ubuntu 22.04):
```bash
docker build -t halucinator ./
docker run --name halucinator -it --network=host halucinator bash
```

To build on Ubuntu 24.04 instead:
```bash
docker build --build-arg UBUNTU_VERSION=24.04 -t halucinator:24.04 ./
```

Building the Docker image takes a while (~20 min for QEMU builds).

The convenience script `run_hal_docker.sh` manages container lifecycle
(start, attach to existing, mount project directories):
```bash
# Mount your project and run
HAL_TARGET=/path/to/project ./run_hal_docker.sh
```

Inside the container, start the UART peripheral device:
```bash
hal_dev_uart -i=1073811456
```

In a separate terminal, exec into the same container and run the firmware:
```bash
docker exec -it halucinator bash
./test/STM32/example/run.sh
```

You will eventually see in both terminals messages containing:
```
 ****UART-Hyperterminal communication based on IT ****
 Enter 10 characters using keyboard :
```

Enter 10 characters in the first terminal running `hal_dev_uart` and press
enter. You should see the text echoed followed by:

```txt
 Example Finished
```

To clean up: `docker rm halucinator`


## Setup in Virtual Environment

Tested on Ubuntu 22.04 and 24.04.

### 1. Clone the repo and submodules

```bash
git clone <this repo>
git submodule update --init
```

### 2. Install system dependencies

**Ubuntu 22.04 (Jammy):**
```bash
sudo apt-get update
echo "deb-src http://archive.ubuntu.com/ubuntu/ jammy main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list
echo "deb-src http://archive.ubuntu.com/ubuntu/ jammy-security main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list
sudo apt-get update
sudo apt-get build-dep -y qemu
sudo apt-get install -y \
    build-essential ca-certificates cmake ninja-build g++ git vim wget \
    python3 python3-pip python3-venv python3-tk \
    gdb-multiarch gcc-arm-none-eabi binutils-arm-none-eabi \
    libaio-dev libglib2.0-dev libpixman-1-dev pkg-config \
    clang-format ethtool tcpdump
```

**Ubuntu 24.04 (Noble):**
```bash
sudo apt-get update
echo "deb-src http://archive.ubuntu.com/ubuntu/ noble main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list
echo "deb-src http://archive.ubuntu.com/ubuntu/ noble-security main restricted universe multiverse" | sudo tee -a /etc/apt/sources.list
sudo apt-get update
sudo apt-get build-dep -y qemu
sudo apt-get install -y \
    build-essential ca-certificates cmake ninja-build g++ git vim wget \
    python3 python3-pip python3-venv python3-tk \
    gdb-multiarch gcc-arm-none-eabi binutils-arm-none-eabi \
    libaio-dev libglib2.0-dev libpixman-1-dev pkg-config \
    clang-format ethtool tcpdump
```

> **Note:** On Ubuntu 24.04, pip refuses to install packages outside a
> virtual environment (PEP 668). Always use a venv as shown below.
> The package `python-tk` was renamed to `python3-tk` starting in 22.04.

**Optional cross-compilers** (for building multi-arch test firmware):
```bash
sudo apt-get install -y \
    gcc-arm-linux-gnueabi gcc-aarch64-linux-gnu \
    gcc-mips-linux-gnu gcc-powerpc-linux-gnu gcc-powerpc64-linux-gnu
```

### 3. Create and activate a Python virtual environment

```bash
python3 -m venv ~/.virtualenvs/halucinator
source ~/.virtualenvs/halucinator/bin/activate
```

### 4. Install Python packages

```bash
pip install -e deps/avatar2/
pip install -r src/requirements.txt
pip install -e src
pip install pytest-cov pytest-timeout  # for running tests
```

### 5. Build QEMU

```bash
./build_qemu.sh
```

This builds QEMU for ARM, ARM64, MIPS, PPC, and PPC64 (~20 min first time).

### 6. Set environment variables

```bash
source activate.sh
```

Or set them manually:
```bash
export HALUCINATOR_QEMU_ARM=$(pwd)/deps/build-qemu/arm-softmmu/qemu-system-arm
export HALUCINATOR_QEMU_ARM64=$(pwd)/deps/build-qemu/aarch64-softmmu/qemu-system-aarch64
export HALUCINATOR_QEMU_MIPS=$(pwd)/deps/build-qemu/mips-softmmu/qemu-system-mips
export HALUCINATOR_QEMU_PPC=$(pwd)/deps/build-qemu/ppc-softmmu/qemu-system-ppc
export HALUCINATOR_QEMU_PPC64=$(pwd)/deps/build-qemu/ppc64-softmmu/qemu-system-ppc64
```

### Note on setting HALUCINATOR_QEMU_*

You can override the QEMU binary used by HALucinator by setting the
appropriate environment variable for your target architecture:

```sh
export HALUCINATOR_QEMU_ARM=<full path to your qemu-system-arm>
export HALUCINATOR_QEMU_ARM64=<full path to your qemu-system-aarch64>
export HALUCINATOR_QEMU_MIPS=<full path to your qemu-system-mips>
export HALUCINATOR_QEMU_PPC=<full path to your qemu-system-ppc>
export HALUCINATOR_QEMU_PPC64=<full path to your qemu-system-ppc64>
```

If not set, HALucinator looks for QEMU in `deps/build-qemu/<arch>-softmmu/`.

If using virtual environments these can be set in `$VIRTUAL_ENV/bin/postactivate`
and removed in `$VIRTUAL_ENV/bin/predeactivate`.

### Optional: Symbol Extraction with angr

To auto-generate address files from ELF binaries:

```bash
pip install angr
hal_make_addr -b <path_to_elf> -o addrs.yaml
```

## VSCode Extension and Debug Adapter

HALucinator includes a Debug Adapter Protocol (DAP) server for IDE-based
debugging and VSCode extensions for breakpoint handler browsing.

### Installing VSCode Extensions

The extensions are distributed as `.vsix` files. If using Docker:

```bash
# Extract and install extensions from a running container
./extra_tools/vscode-extension-installer.sh halucinator
```

This installs the HALucinator VSCode extension and the gview assembly
viewer (for Ghidra-generated disassembly) into your host VSCode.

### Generating Handler Metadata

The VSCode extensions use `bpdata.json` for breakpoint handler autocomplete.
To regenerate it (e.g. after adding custom handlers):

```bash
python3 extra_tools/parse_bp_handlers.py -s src/halucinator -o bpdata.json
```

For projects with custom handler directories:
```bash
python3 extra_tools/add_handler_path.py -a /path/to/your/handlers
python3 extra_tools/run_parse_handlers.py
```

### Debug Adapter Protocol (DAP)

Launch HALucinator with the `--dap` flag to start a Debug Adapter Protocol
server (default port 34157):

```sh
halucinator -c config.yaml --dap
```

Connect from VSCode by adding to `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "HALucinator Debug",
      "type": "halucinator",
      "request": "attach",
      "host": "localhost",
      "port": 34157
    }
  ]
}
```

The DAP server provides execution control (continue, step, next),
breakpoint management (including HAL-specific breakpoints), register and
memory inspection, and stack trace viewing through the assembly
disassembly view (e.g., a Ghidra-generated gview file).

### GDB Remote Serial Protocol (GDB server)

Launch HALucinator with the `--gdb-server` flag to expose a GDB RSP server
(default port 3333) for external debuggers such as Ghidra, command-line
GDB, IDA, or Binary Ninja:

```sh
halucinator -c config.yaml --gdb-server
```

Connect with command-line GDB:

```sh
$ gdb-multiarch
(gdb) target remote localhost:3333
(gdb) info registers
(gdb) break *0x08001234
(gdb) continue
```

Or from Ghidra's Debugger tool, use the **gdb** launcher with
`gdb-multiarch` and connect to `localhost:3333`.

The `--gdb-server` and `--dap` flags can be combined to run both servers
simultaneously.

See [doc/debugging.md](doc/debugging.md) for a full walkthrough,
architecture-specific notes, and troubleshooting.

## Running

Running Halucinator requires a configuration file that lists the functions to
intercept and the handler to be called on that interception. These are usually split
across three files for portability.  The files are a memory file that
describes the memory layout, an intercept file that describes what to intercept
and a symbol/address file that maps addresses to symbol names.  Internally, HALucinator
concatenates these configs into one config with the last taking precidence. See the Config
File section below for full details

All of these commands assume you are in your halucinator virtual environment

```sh
halucinator  -c=<memory_file.yaml> -c=<intercept_file.yaml> -c=<address_file.yaml>
```

## Running an Example



###  STM32F469I Uart Example

To give an idea how to use Halucinator an example is provided in `test/STM32/example`.

#### Setup
Note: This was done prior and the files are in the repo in `test/STM32/example`.
If you just want to run the example without building it just go to Running UART Example below.

This procedure should be followed for other binaries.
In list below after the colon (:) denotes the file/cmd .


2. Copy binary to a dir of you choice and cd to it:  `test/STM32/example`
3. Create binary file: `<halucinator_repo_root>/src/tools/make_bin.sh Uart_Hyperterminal_IT_O0.elf` creates `Uart_Hyperterminal_IT_O0.elf.bin`
4. Create Memory Layout (specifies memory map of chip): `Uart_Hyperterminal_IT_O0_memory.yaml`
5. Create Address File (maps function names to address): `Uart_Hyperterminal_IT_O0_addrs.yaml`
6. Create Intercept File (defines functions to intercept and what handler to use for it): `Uart_Hyperterminal_IT_O0_config.yaml`
7. (Optional) create shell script to run it: `run.sh`

Note: Symbols used in the address file can be created from an elf file with symbols
using `hal_make_addr`. This requires installing angr in halucinator's virtual environment.
This was used to create `Uart_Hyperterminal_IT_O0_addrs.yaml`

To use it the first time you would install angr (e.g. `pip install angr` from
the halucinator virtual environment)

```sh
hal_make_addr -b <path to elf file>
```

#### Running UART Example

Start the UART Peripheral device,  this a script that will subscribe to the Uart
on the peripheral server and enable interacting with it.

```bash
hal_dev_uart -i=1073811456
```

In separate terminal start halucinator with the firmware.

```bash
workon halucinator
halucinator -c=test/STM32/example/Uart_Hyperterminal_IT_O0_config.yaml \
  -c=test/STM32/example/Uart_Hyperterminal_IT_O0_addrs.yaml \
  -c=test/STM32/example/Uart_Hyperterminal_IT_O0_memory.yaml --log_blocks -n Uart_Example

# or use the convenience script:
bash test/STM32/example/run.sh
```
Note the `--log_blocks` and `-n` are optional.

You will eventually see in both terminals messages containing
```
 ****UART-Hyperterminal communication based on IT ****
 Enter 10 characters using keyboard :
```

Enter 10 Characters in the first terminal running `hal_dev_uart` press enter
should then see text echoed followed by.

```txt
 Example Finished
```

#### Stopping

Press `ctrl-c`. If for some reason this doesn't work kill it with `ctrl-z`
and `kill %`, or `killall -9 halucinator`

Logs are kept in the `tmp/<value of -n option>`. e.g `tmp/Uart_Example/`

## Config file

How the emulation is performed is controlled by a yaml config file.  It is passed
in using a the -c flag, which can be repeated with the config file being appended
and the later files overwriting any collisions from previous file.  The config
is specified as follows.  Default field values are in () and types are in <>

```yaml
machine:   # Optional, describes qemu machine used in avatar entry optional defaults in ()
           # if never specified default settings as below are used.
  arch: (cortex-m3)<str>,
  cpu_model: (cortex-m3)<str>,
  entry_addr: (None)<int>,  # Initial value to pc reg. Obtained from 0x0000_0004
                        # of memory named init_mem if it exists else memory
                        # named flash
  init_sp: (None)<int>,     # Initial value for sp reg, Obtained from 0x0000_0000
                        # of memory named init_mem if it exists else memory
                        # named flash
  gdb_exe: ('gdb-multiarch')<path> # Path to gdb to use


memories:  #List of the memories to add to the machine
  - name: <str>,       # Required
    base_addr:  <int>, # Required
    size: <int>,       # Required
    perimissions: (rwx)<r--|rw-|r-x>, # Optional
    file: filename<path>   # Optional Filename to populate memory with, use full path or
                      # path relative to this config file, blank memory used if not specified
    emulate: class<AvatarPeripheral subclass>    # Class to emulate memory

peripherals:  # Optional, A list of memories, except emulate field required

intercepts:  # Optional, list of intercepts to places
  - class:  <BPHandler subclass>,  # Required use full import path
    function: <str>     # Required: Function name in @bp_handler([]) used to
                        #   determine class method used to handle this intercept
    addr: (from symbols)<int>  # Optional, Address of where to place this intercept,
                               # generally recommend not setting this value, but
                               # instead setting symbol and adding entry to
                               # symbols for this makes config files more portable
    symbol: (Value of function)<str>  # Optional, Symbol name use to determine address
    class_args: ({})<dict>  # Optional dictionary of args to pass to class's
                       # __init__ method, keys are parameter names
    registration_args: ({})<dict>  # Optional: Arguments passed to register_handler
                              # method when adding this method
    run_once: (false)<bool> # Optional: Set to true if only want intercept to run once
    watchpoint: (false)<bool> # Optional: Set to true if this is a memory watch point

symbols:  # Optional, dictionary mapping addresses to symbol names, used to
          # determine addresses for symbol values in intercepts
  addr0<int>: symbol_name<str>
  addr1<int>: symbol1_name<str>

elf_program:  # For more info on this section see doc/c_intercepts.md
  name:  (None)<str>    #  Required, used to reference symbols from the elf program
                        #  in normal intercepts

  build: {cmd: (None)<str>, dir: (None)<str>, module_relative: (None)<str>}
          # Optional: If specified the cmd: will be executed from dir.
          # dir is relative to the directory of this config
          # If module_relative is not None the string  will be used to import
          # a python module and dir will relative to the directory of that module.

  elf: main.elf  # Path to the elf file (if full path give it is used/else is
                 # assumed to be relative to location of this file

  elf_module_relative: (None)<str>  # The full path for a python module that the
                                    # elf file should be loaded from

  execute_before: (True)<bool>      # This program should execute before the
                                    # entry point specified in config file

  exit_function: (exit)<str>        # Symbol when executed, execution should be
                                    # redirected to entry_ point

  intercepts:                       # Optional, list of intercepts
    - handler: <str>                # Name of the function to redirect execution to
      symbol:  <str>                # Either symbol/addr is required.  Specifies place
      addr: <int>                   # in firmware to be redirected to handler
      options: <arch specific>      # Optional passed to the rewriter to specify
                                    # for example could be use to specify arm/thumb mode

options: # Optional, Key:Value pairs you want accessible during emulation

```

The symbols in the config can also be specified using one or more symbols files
passed in using -s. This is a csv file each line defining a symbol as shown below

```csv
symbol_name<str>, start_addr<int>, last_addr<int>
```

## Testing

HALucinator has a comprehensive test suite with 28,600+ tests achieving 84% code coverage.

### Running Unit Tests

```bash
# Run all CI-safe tests
PYTHONPATH=src:test/pytest/helpers python3 -m pytest test/pytest/ \
  -m "not slow_zmq and not needs_root" \
  -p no:timeout --tb=short

# Run with coverage
PYTHONPATH=src:test/pytest/helpers python3 -m pytest test/pytest/ \
  -m "not slow_zmq and not needs_root" \
  -p no:timeout --cov=halucinator --cov-report=term-missing
```

### Running E2e Firmware Tests

These require a built QEMU (run `./build_qemu.sh` first) and the
`HALUCINATOR_QEMU_ARM` environment variable set.

```bash
export HALUCINATOR_QEMU_ARM=<path to qemu-system-arm>
bash ./test/STM32/example/run_test.bash
bash ./test/zephyr/zephyr_fs/run_tests.bash
bash ./test/zephyr/frdm_k64f_UART_Excellent_Test/run_tests.bash
bash ./test/zephyr/olimex_stm32_h103_UART_Excellent_test/run_tests.bash
bash ./test/firmware-rehosting/p2im-drone/run_tests.bash
```

### Test Markers

Tests are categorized with pytest markers defined in `conftest.py`:

- `slow_zmq`: Tests that use real zmq sockets/threads (may hang in combined runs)
- `needs_root`: Tests that require root privileges (raw sockets, scapy)

### CI/CD

The GitHub Actions workflow (`.github/workflows/virtual-environment-tests.yml`)
runs on every push and pull request to master. It tests on both Ubuntu 22.04
and 24.04 in parallel, builds QEMU for all architectures (cached per OS),
runs QEMU smoke tests, all e2e firmware tests (STM32, Zephyr, multi-arch),
and the full pytest suite with coverage reporting.

## Available BP Handler Families

- **generic**: Common handlers (ReturnZero, SkipFunc, Counter, Timer, etc.)
- **stm32f4**: STM32F4 HAL (UART, GPIO, SPI, ethernet, timers, WiFi)
- **libopencm3**: libopencm3 (ADC, DMA, flash, GPIO, RCC, SPI, timer, USART)
- **atmel_asf_v3**: Atmel ASF (contiki, ethernet, radio, SD/MMC, timers, USART)
- **mbed**: Mbed OS (boot, serial, timer)
- **vxworks**: VxWorks RTOS (boot, filesystem, ethernet, interrupts, scheduler, tasks)
- **zephyr**: Zephyr RTOS (filesystem, UART)