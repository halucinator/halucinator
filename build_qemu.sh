#!/bin/bash
# usage: build_qemu.sh [--source <subdir>] [target-list...]
#
# Default source tree is deps/avatar-qemu (QEMU 6.2 fork carrying the
# avatar hooks). Pass --source libafl-qemu-bridge to build the
# halucinator/libafl-qemu-bridge fork instead — a newer QEMU 10.x base
# that also carries the avatar hooks plus LibAFL fuzzing surface.
#
# Build output goes to:
#   deps/build-qemu/        (default / avatar-qemu)
#   deps/build-qemu-libafl/ (libafl-qemu-bridge)
set -o errexit

SOURCE="avatar-qemu"
BUILD_SUBDIR="build-qemu"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            SOURCE="$2"
            case "$SOURCE" in
                avatar-qemu)
                    BUILD_SUBDIR="build-qemu" ;;
                libafl-qemu-bridge)
                    BUILD_SUBDIR="build-qemu-libafl" ;;
                *)
                    echo "build_qemu.sh: unknown --source '$SOURCE' "\
                         "(want avatar-qemu or libafl-qemu-bridge)" >&2
                    exit 1
                    ;;
            esac
            shift 2
            ;;
        --source=*)
            arg="${1#--source=}"
            exec "$0" --source "$arg" "${@:2}"
            ;;
        *)
            break
            ;;
    esac
done

if [ "$1" == "" ] ; then
    TARGET_LIST=("ppc-softmmu" "arm-softmmu" "aarch64-softmmu" "mips-softmmu" "ppc64-softmmu")
else
    TARGET_LIST=$@
fi

# build qemu
for target in ${TARGET_LIST[@]}
do
    pushd deps/
    mkdir -p "$BUILD_SUBDIR"/"$target"
    cd "$BUILD_SUBDIR"/"$target"
    # avatar-qemu is pinned at QEMU 6.2.0 and doesn't compile cleanly
    # against newer toolchains/libraries on its own:
    #
    #   --disable-linux-io-uring
    #     util/fdmon-io_uring.c calls io_uring_prep_poll_remove(sqe,
    #     AioHandler*) — pre-2.0 liburing took void*; ubuntu-24.04 ships
    #     liburing 2.5 which made the second arg __u64, so the call
    #     fails -Werror=int-conversion under gcc-13.
    #
    #   --disable-werror
    #     gcc-13's tightened diagnostics (dangling-pointer,
    #     array-bounds, stringop-overflow, use-after-free) fire on
    #     QEMU 6.2 sources that compiled clean on gcc-11/12. None of
    #     them are real bugs in our code path; suppress -Werror so the
    #     warnings still print but don't fail the build.
    #
    #   --disable-bpf
    #     ebpf/ebpf_rss.c calls bpf_program__set_socket_filter() —
    #     that helper was removed in libbpf 1.0+. ubuntu-24.04 ships
    #     libbpf 1.3, so the link step fails with `undefined reference`
    #     for any -softmmu target. eBPF RSS is a virtio-net offload
    #     feature halucinator doesn't use.
    #
    # Halucinator doesn't drive QEMU's block-I/O backends or eBPF RSS,
    # so all three flags are no-ops on the halucinator code path. They
    # also do nothing on ubuntu-22.04 + gcc-11 + libbpf 0.5 where the
    # build was already clean.
    #
    # libafl-qemu-bridge is on a much newer QEMU base (10.x); these
    # flags remain safe no-ops there too.
    ../../"$SOURCE"/configure --target-list=$target \
        --disable-linux-io-uring --disable-werror --disable-bpf
    make all -j`nproc`
    popd
done
