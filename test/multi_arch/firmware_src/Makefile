CFLAGS = -g -O0 -ffreestanding -fno-builtin
LDFLAGS = -nostdlib -static
SRCS = test_uart.c support.c

all: arm32 arm64 mips ppc ppc64
.PHONY: all arm32 arm64 mips ppc ppc64

arm32: ../arm32/firmware/test_uart.bin
arm64: ../arm64/firmware/test_uart.bin
mips: ../mips/firmware/test_uart.bin
ppc: ../ppc/firmware/test_uart.bin
ppc64: ../ppc64/firmware/test_uart.bin

# ARM32 (bare-metal, Cortex-M3 Thumb mode)
../arm32/firmware/test_uart.elf: $(SRCS) link-arm32.ld
	arm-linux-gnueabi-gcc $(CFLAGS) $(LDFLAGS) -mthumb -mcpu=cortex-m3 -T link-arm32.ld -o $@ $(SRCS)

../arm32/firmware/test_uart.bin: ../arm32/firmware/test_uart.elf
	arm-linux-gnueabi-objcopy -O binary $< $@

# ARM64 / AARCH64
../arm64/firmware/test_uart.elf: $(SRCS) link-arm64.ld
	aarch64-linux-gnu-gcc $(CFLAGS) $(LDFLAGS) -T link-arm64.ld -o $@ $(SRCS)

../arm64/firmware/test_uart.bin: ../arm64/firmware/test_uart.elf
	aarch64-linux-gnu-objcopy -O binary $< $@

# MIPS (big-endian)
../mips/firmware/test_uart.elf: $(SRCS) link-mips.ld
	mips-linux-gnu-gcc $(CFLAGS) $(LDFLAGS) -mno-abicalls -G0 -T link-mips.ld -o $@ $(SRCS)

../mips/firmware/test_uart.bin: ../mips/firmware/test_uart.elf
	mips-linux-gnu-objcopy -O binary $< $@

# PowerPC 32-bit
../ppc/firmware/test_uart.elf: $(SRCS) link-ppc.ld
	powerpc-linux-gnu-gcc $(CFLAGS) $(LDFLAGS) -T link-ppc.ld -o $@ $(SRCS)

../ppc/firmware/test_uart.bin: ../ppc/firmware/test_uart.elf
	powerpc-linux-gnu-objcopy -O binary $< $@

# PowerPC 64-bit (ELF V2 ABI - direct function pointers, no descriptors)
../ppc64/firmware/test_uart.elf: $(SRCS) link-ppc64.ld
	powerpc64-linux-gnu-gcc $(CFLAGS) $(LDFLAGS) -mabi=elfv2 -T link-ppc64.ld -o $@ $(SRCS)

../ppc64/firmware/test_uart.bin: ../ppc64/firmware/test_uart.elf
	powerpc64-linux-gnu-objcopy -O binary $< $@

# Extract symbol addresses
addresses:
	@for arch in arm32 arm64 mips ppc ppc64; do \
		elf=../$$arch/firmware/test_uart.elf; \
		if [ -f "$$elf" ]; then \
			echo "=== $$arch ==="; \
			nm "$$elf" | grep -E " [TtDd] (main|uart_init|uart_write|uart_read|_start)" | sort; \
		fi; \
	done
.PHONY: addresses

# Generate address YAML files from ELF symbols
gen-addrs:
	@for arch in arm32 arm64 mips ppc ppc64; do \
		elf=../$$arch/firmware/test_uart.elf; \
		yaml=../$$arch/test_uart_addrs.yaml; \
		if [ -f "$$elf" ]; then \
			echo "symbols:" > "$$yaml"; \
			nm "$$elf" | grep -E " [TtDd] (main|uart_init|uart_write|uart_read|_start)" | \
				awk '{printf "  0x%s: %s\n", $$1, $$3}' >> "$$yaml"; \
			echo "Generated $$yaml"; \
		fi; \
	done
.PHONY: gen-addrs

clean:
	rm -f ../*/firmware/*.elf ../*/firmware/*.bin ../*/test_uart_addrs.yaml
.PHONY: clean
