#!/usr/bin/env bash
#
# Build Drone.elf + Drone.bin from upstream source instead of shipping
# prebuilt binaries whose license status is unclear.
#
# Clones a pinned commit of RiS3-Lab/p2im-real_firmware (the P²IM paper's
# firmware submodule, which contains the Drone/ STM32CubeIDE project) and
# compiles it with arm-none-eabi-gcc using build flags equivalent to the
# STM32CubeIDE/Atollic TrueSTUDIO Debug configuration in Drone/.cproject.
#
# Usage:  ./build_drone.sh
# Output: ./Drone.elf, ./Drone.bin in the script's directory.
#
# Re-runs are cached via the clone directory — pass `--clean` to force
# a rebuild from scratch.
#
# Dependencies (CI installs these under "Install system dependencies"):
#   gcc-arm-none-eabi, binutils-arm-none-eabi, git, curl, make.

set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

# Pin to a specific p2im-real_firmware commit so the produced binary is
# byte-reproducible and the symbol addresses in drone_addrs.yaml (which
# we regenerate below) stay stable across builds.
REPO_URL="https://github.com/RiS3-Lab/p2im-real_firmware.git"
REPO_SHA="d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7"   # 2020-12-11
REPO_DIR="build/p2im-real_firmware"
SRC_DIR="${REPO_DIR}/Drone"
OUT_ELF="Drone.elf"
OUT_BIN="Drone.bin"

if [[ "${1:-}" == "--clean" ]]; then
    rm -rf "$REPO_DIR" "$OUT_ELF" "$OUT_BIN" build/obj
fi

mkdir -p build

# ---------------------------------------------------------------------------
# Fetch the upstream source (pinned commit)
# ---------------------------------------------------------------------------
if [[ ! -d "$SRC_DIR" ]]; then
    echo ">>> Cloning p2im-real_firmware @ ${REPO_SHA:0:10}"
    git init -q "$REPO_DIR"
    git -C "$REPO_DIR" remote add origin "$REPO_URL"
    git -C "$REPO_DIR" fetch -q --depth 1 origin "$REPO_SHA"
    git -C "$REPO_DIR" checkout -q FETCH_HEAD
fi

# ---------------------------------------------------------------------------
# Toolchain + build flags
# ---------------------------------------------------------------------------
CC=${CC:-arm-none-eabi-gcc}
OBJCOPY=${OBJCOPY:-arm-none-eabi-objcopy}

# From Drone/.cproject (Atollic TrueSTUDIO Debug config, cross-walked to
# arm-none-eabi-gcc):
#   MCU              = STM32F103C8 (Cortex-M3, Thumb-2, no FPU)
#   Optimisation     = -Os  (enumerated "0s" in .cproject)
#   Defines          = USE_HAL_DRIVER, STM32F103xB,
#                      __weak=__attribute__((weak)),
#                      __packed=__attribute__((__packed__))
#   Warnings         = suppressed in the assembler step
#   Link-time GC     = enabled for both code and data
CPU_FLAGS=(-mcpu=cortex-m3 -mthumb -mfloat-abi=soft)
DEFINES=(
    -DUSE_HAL_DRIVER
    -DSTM32F103xB
    "-D__weak=__attribute__((weak))"
    "-D__packed=__attribute__((__packed__))"
)
INCLUDES=(
    "-I${SRC_DIR}/Inc"
    "-I${SRC_DIR}/Drivers/STM32F1xx_HAL_Driver/Inc"
    "-I${SRC_DIR}/Drivers/STM32F1xx_HAL_Driver/Inc/Legacy"
    "-I${SRC_DIR}/Drivers/CMSIS/Device/ST/STM32F1xx/Include"
    "-I${SRC_DIR}/Drivers/CMSIS/Include"
)
CFLAGS=(
    "${CPU_FLAGS[@]}"
    -Os -g3
    -ffunction-sections -fdata-sections
    "${DEFINES[@]}"
    "${INCLUDES[@]}"
    -std=gnu11
)
ASFLAGS=("${CPU_FLAGS[@]}" -g3)
LDFLAGS=(
    "${CPU_FLAGS[@]}"
    "-T${SRC_DIR}/STM32F103C8_FLASH.ld"
    -Wl,--gc-sections
    -Wl,-Map,build/Drone.map
    --specs=nosys.specs
    --specs=nano.specs
    -lc -lm -lnosys
)

OBJDIR="build/obj"
mkdir -p "$OBJDIR"

compile_c() {
    local src="$1"
    local obj="$OBJDIR/$(basename "${src%.c}").o"
    echo "  CC  $src"
    "$CC" "${CFLAGS[@]}" -c "$src" -o "$obj"
    echo "$obj"
}

compile_s() {
    local src="$1"
    local obj="$OBJDIR/$(basename "${src%.s}").o"
    echo "  AS  $src"
    "$CC" "${ASFLAGS[@]}" -c "$src" -o "$obj"
    echo "$obj"
}

# ---------------------------------------------------------------------------
# Gather + compile every .c in Src/ and the HAL driver Src/
# ---------------------------------------------------------------------------
OBJECTS=()
echo ">>> Compiling application sources"
for src in "$SRC_DIR"/Src/*.c; do
    OBJECTS+=("$(compile_c "$src" | tail -1)")
done

echo ">>> Compiling STM32F1 HAL driver sources"
for src in "$SRC_DIR"/Drivers/STM32F1xx_HAL_Driver/Src/*.c; do
    OBJECTS+=("$(compile_c "$src" | tail -1)")
done

echo ">>> Assembling startup file"
OBJECTS+=("$(compile_s "$SRC_DIR/startup/startup_stm32f103xb.s" | tail -1)")

# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------
echo ">>> Linking $OUT_ELF"
"$CC" "${OBJECTS[@]}" "${LDFLAGS[@]}" -o "$OUT_ELF"

echo ">>> Generating $OUT_BIN"
"$OBJCOPY" -O binary "$OUT_ELF" "$OUT_BIN"

# ---------------------------------------------------------------------------
# Patch drone_memory.yaml's entry_addr from the rebuilt ELF's reset
# vector. On Cortex-M, the second word of .isr_vector at 0x08000000 is
# the reset-vector PC (the first is the initial SP). objdump prints
# word bytes in memory order, so we grab word[1] and byte-swap for
# little-endian.
# ---------------------------------------------------------------------------
ENTRY_HEX=$(arm-none-eabi-objdump -s -j .isr_vector "$OUT_ELF" \
    | awk '/^ 8000000 /{print $3}' | fold -w2 | tac | tr -d '\n')
if [[ -n "$ENTRY_HEX" ]]; then
    echo ">>> Patching drone_memory.yaml entry_addr to 0x${ENTRY_HEX}"
    # Portable sed -i across BSD + GNU via explicit backup extension.
    sed -i.bak "s/^\([[:space:]]*entry_addr:\).*/\1 0x${ENTRY_HEX}/" drone_memory.yaml
    rm -f drone_memory.yaml.bak
else
    echo ">>> WARNING: failed to extract entry address from $OUT_ELF"
fi

# ---------------------------------------------------------------------------
# Regenerate drone_addrs.yaml so the halucinator intercepts match
# whatever symbol layout this build produced. hal_make_addr ships with
# halucinator (src/tools/make_bin.sh installs hal_make_addr as an entry
# point).
# ---------------------------------------------------------------------------
if command -v hal_make_addr >/dev/null 2>&1; then
    echo ">>> Regenerating drone_addrs.yaml for this build"
    hal_make_addr -b "$OUT_ELF" -o drone_addrs.yaml
else
    echo ">>> WARNING: hal_make_addr not on PATH; drone_addrs.yaml not regenerated"
    echo ">>>          run 'pip install -e src/' from the halucinator root first."
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
echo
echo "=== Build complete ==="
ls -la "$OUT_ELF" "$OUT_BIN"
"$CC" --version | head -1
