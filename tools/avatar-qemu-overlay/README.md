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

The overlay is split per QEMU API generation. `apply.sh` reads the
target tree's `VERSION` file to pick the right subdir.

    v6.2.x/                       -- tracks the avatar-qemu fork
        hw/avatar/*               -- additive QOM devices + machine type
        qapi/avatar-target.json
        patches/0001-avatar-mainline-hooks.patch
                                  -- ~17 mainline files: machine-class hook,
                                     register schema, log mask, NVIC IRQ
                                     hook, banked-register helpers, etc.

    v10.x/                        -- tracks libafl-qemu-bridge (also covers v11)
        hw/avatar/*               -- avatar files refactored for the QEMU 10
                                     hw API (some interfaces renamed/moved
                                     between 6.2 and 10)
        include/hw/avatar/*       -- public avatar headers (LOG_AVATAR lives
                                     in interrupts.h here so the overlay
                                     doesn't have to patch include/qemu/log.h
                                     and fight bit-numbering churn)
        qapi/avatar-target.json
        patches/0001-avatar-mainline-hooks.patch
                                  -- 9 mainline files: configure --enable-avatar
                                     option, hw/Kconfig + hw/meson.build
                                     wiring, NVIC IRQ hooks, qapi schema
                                     wiring, ARM_FEATURE_CONFIGURABLE,
                                     m_helper exception-exit hook, meson
                                     have_avatar plumbing.

    apply.sh                      -- variant-aware driver. Tries git apply,
                                     falls back to `patch -N -F3` so minor
                                     point-release churn (line-number drift,
                                     hunks already merged upstream) absorbs
                                     instead of breaking the build.

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

| QEMU release | Variant | Status |
|---|---|---|
| v6.2.0  | v6.2.x | ✅ verified — base of the `avatar-qemu` fork |
| v10.0.3 | v10.x  | ✅ verified — base of `libafl-qemu-bridge` |
| v10.0.9 | v10.x  | ✅ verified — applies clean with `git apply` |
| v11.0.0 | v10.x  | ✅ verified — applies via `patch -N -F3` fallback (one hunk merged upstream and is skipped) |
| v8.x    | —      | ⏳ untested — softmmu/vl.c moved to system/main.c here; needs a v8.x variant |

Adding a new variant = create `vN.x/`, copy the closest-matching
`hw/avatar/`, refresh the mainline patch against the new base, smoke-test
that `--enable-avatar` configures and `qemu-system-arm -machine help`
lists `configurable`. The header + .c files usually only need cosmetic
updates since they live in their own `hw/avatar/` namespace.

## See also

- `deps/avatar-qemu` — full fork pinned at QEMU 6.2.0 (default `qemu` backend)
- `deps/libafl-qemu-bridge` — full fork on QEMU 10.x + LibAFL hooks
  (`--emulator libafl-qemu` backend)
- `build_qemu.sh --source overlay --upstream <git-ref>` — driver script
  that clones upstream + applies this overlay + builds
