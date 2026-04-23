# Dependencies

## Core Runtime

| Package | License | Notes |
|---------|---------|-------|
| avatar2 | Apache 2.0 | QEMU orchestration (submodule) |
| avatar-qemu | GPL-2.0 | Custom QEMU fork (submodule) |
| pyzmq (0MQ) | BSD | Inter-process communication |
| PyYAML | MIT | Configuration file parsing |
| IPython | BSD | Interactive debug shell |
| prompt_toolkit | BSD | Debug shell UI |

## System Dependencies

| Package | Notes |
|---------|-------|
| python3 (>=3.8) | Runtime |
| gdb-multiarch | Debugger (default GDB) |
| python3-venv | Virtual environment |
| python-tk | GUI support |
| ethtool | Ethernet peripheral support |
| gcc-arm-none-eabi | ARM cross-compiler (for make_bin) |
| binutils-arm-none-eabi | ARM binutils (for objcopy) |

## Optional Dependencies

| Package | License | Notes |
|---------|---------|-------|
| scapy | GPL-2.0 | Ethernet frame handling |
| angr / cle | BSD | ELF symbol extraction (hal_make_addrs) |
| ipdb | BSD | Interactive debugging |

## Test Dependencies

| Package | License | Notes |
|---------|---------|-------|
| pytest | MIT | Test framework |
| pytest-cov | MIT | Coverage reporting |
| pytest-timeout | MIT | Test timeout support |
