# p2im-drone end-to-end test

This directory exercises halucinator against a Cortex-M3 drone flight
controller firmware from the P²IM paper
([USENIX Security '20](https://www.usenix.org/conference/usenixsecurity20/presentation/feng)).

**The firmware binaries (`Drone.elf`, `Drone.bin`) and the symbol file
(`drone_addrs.yaml`) are not checked into this repository.** They're
produced at test time by `build_drone.sh`, which compiles a pinned
upstream commit of `RiS3-Lab/p2im-real_firmware` with
`arm-none-eabi-gcc`. See [`PROVENANCE.md`](PROVENANCE.md) for the
chain of authorship and why we don't ship the prebuilts.

## Build

```sh
# Prerequisites: arm-none-eabi-gcc, binutils-arm-none-eabi, git, curl, make.
# halucinator must be installed so hal_make_addr is on $PATH.
./build_drone.sh                 # produces Drone.elf, Drone.bin,
                                 # and regenerates drone_addrs.yaml
./build_drone.sh --clean         # force a rebuild from scratch
```

The script pins to
[`p2im-real_firmware@d4c7456`](https://github.com/RiS3-Lab/p2im-real_firmware/tree/d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7)
(2020-12-11), so the output is byte-reproducible across machines that
have the same gcc version. The entry-address field of `drone_memory.yaml`
is patched in place so halucinator's config always matches whatever
was built.

## Run

```sh
./run.sh                    # one-shot: builds if missing, then runs halucinator
./run_tests.bash            # CI flow: builds+runs, waits for QEMU, checks output
```

Either script will invoke `build_drone.sh` automatically if the
firmware isn't already present.

## Files

| File                   | Source                                          |
|------------------------|-------------------------------------------------|
| `build_drone.sh`       | builds `Drone.elf`/`Drone.bin` from upstream    |
| `drone_config.yaml`    | halucinator intercept class → function mapping  |
| `drone_intercepts.yaml`| per-intercept `function:` names (addresses are resolved by name at load time against `drone_addrs.yaml`) |
| `drone_memory.yaml`    | memory regions + `entry_addr` (patched by `build_drone.sh`) |
| `drone.py`             | test-side IO server (handshake + UART sink)     |
| `bp_handlers/`         | drone-specific bp_handler classes (MPU9250, MS5611, UART) |
| `run.sh`               | one-shot halucinator invocation                 |
| `run_tests.bash`       | CI-friendly test driver                         |

Generated / not tracked in git:

| File              | Produced by         |
|-------------------|---------------------|
| `Drone.elf`       | `build_drone.sh`    |
| `Drone.bin`       | `build_drone.sh`    |
| `drone_addrs.yaml`| `build_drone.sh` via `hal_make_addr` |
| `build/`          | intermediate object files + upstream clone |
