# avatar-qemu-overlay

A small, additive overlay that adds the halucinator/avatar machine
machinery (the `configurable` machine type, `avatar-rmemory` MMIO
forwarder, IRQ controllers, and QMP injection commands) onto **any
upstream QEMU source tree**, so halucinator can drive a QEMU release
that wasn't pre-forked for it.

## Why

`deps/avatar-qemu` and `deps/libafl-qemu-bridge` are full QEMU forks
(~500 MB each) maintained out of tree. Rebasing them onto a new QEMU
release is a manual lift. This overlay lets a maintainer say:

    git clone https://gitlab.com/qemu-project/qemu.git qemu-src
    cd qemu-src
    git checkout v9.0.0     # or v8.2, v10.0, …
    tools/avatar-qemu-overlay/apply.sh "$(pwd)"
    mkdir -p build && cd build
    ../configure --target-list=arm-softmmu \\
        --disable-werror --disable-bpf --disable-linux-io-uring
    ninja qemu-system-arm

and end up with a QEMU binary halucinator can drive end-to-end.

## Layout

    hw/avatar/                    -- additive (copied verbatim into qsrc/hw/avatar/)
        Kconfig
        meson.build
        configurable_machine.c    -- the `configurable` machine type
        remote_memory.c           -- `avatar-rmemory` QOM device (MMIO → halucinator)
        irq_controller.c
        interrupts.c              -- armv7m IRQ injection
        arm_helper.c              -- banked-register helpers
        avatar_posix.c            -- POSIX MQ glue used by remote_memory
    qapi/
        avatar-target.json        -- QMP avatar-* commands
    patches/
        0001-avatar-mainline-hooks.patch
                                  -- the ~20-file mainline patch series:
                                     register schema, log mask, hw/Kconfig
                                     subdir, machine-class hook, NVIC IRQ
                                     hook, ARM helper hooks, etc.
    apply.sh                      -- copy + apply driver

## What the mainline patch touches

About 20 files, 250 net new lines:

| Area | File(s) | Purpose |
|---|---|---|
| build glue | `meson.build`, `hw/meson.build`, `qapi/meson.build` | wire `hw/avatar` and `qapi/avatar-target.json` into the build |
| QMP schema | `qapi/qapi-schema.json` | include `avatar-target.json` |
| Kconfig | `hw/Kconfig`, `qemu-options.hx` | enable `hw/avatar` |
| machine class | `hw/core/machine.c`, `include/hw/boards.h` | register `configurable` machine prefix |
| NVIC IRQ | `hw/intc/armv7m_nvic.c` | injection hook used by `avatar-armv7m-inject-irq` |
| log mask | `util/log.c`, `util/qemu-config.c`, `include/qemu/log.h` | `LOG_AVATAR` mask |
| ARM | `target/arm/cpu.h`, `helper.c`, `m_helper.c` | banked register access, halucinator-irq init |
| (i386) | `target/i386/cpu.{c,h}` | x86 hooks (unused by halucinator today but ship with the overlay) |
| startup | `softmmu/vl.c` | early `avatar_init` hook |
| headers | `include/hw/avatar/*.h`, `include/qemu/log.h` | header-only declarations |

## Version compatibility

| QEMU release | Status | Notes |
|---|---|---|
| v6.2.0 | ✅ verified | the base our `avatar-qemu` fork tracks |
| v8.x   | ⏳ untested | the patches/ series will likely refresh cleanly; some mainline files shifted (e.g. `softmmu/vl.c` moved) |
| v10.x  | ⏳ untested | `libafl-qemu-bridge` already ships these patches against 10.0.3 so it's known-portable; this overlay just needs minor refreshes |

Refreshing the patches for a new QEMU release is a one-time chore per
release — read the rejected hunks, do a 3-way merge, regenerate
`patches/0001-avatar-mainline-hooks.patch`. The overlay surface is
small and stable.

## See also

- `deps/avatar-qemu` — full fork pinned at QEMU 6.2.0 (default `qemu` backend)
- `deps/libafl-qemu-bridge` — full fork on QEMU 10.x + LibAFL hooks
  (`--emulator libafl-qemu` backend)
- `build_qemu.sh --source overlay --upstream <git-ref>` — driver script
  that clones upstream + applies this overlay + builds
