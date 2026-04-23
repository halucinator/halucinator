CFLAGS=-g -static -nostdlib -ffreestanding

all: test-arm.bin test-mips.bin test-ppc.bin test-ppc64.bin test-aarch64.bin
.PHONY: all

# ARM (bare-metal)
test-arm.elf: test.c support.c
	arm-none-eabi-gcc $(CFLAGS) -o$@ $^

test-arm.bin: test-arm.elf
	arm-none-eabi-objcopy -O binary $< $@

# MIPS (big-endian, bare-metal)
test-mips.elf: test.c support.c
	mips-linux-gnu-gcc $(CFLAGS) -mno-abicalls -o$@ $^

test-mips.bin: test-mips.elf
	mips-linux-gnu-objcopy -O binary $< $@

# PowerPC (32-bit)
test-ppc.elf: test.c support.c
	powerpc-linux-gnu-gcc $(CFLAGS) -o$@ $^

test-ppc.bin: test-ppc.elf
	powerpc-linux-gnu-objcopy -O binary $< $@

# PowerPC 64-bit
test-ppc64.elf: test.c support.c
	powerpc64-linux-gnu-gcc $(CFLAGS) -o$@ $^

test-ppc64.bin: test-ppc64.elf
	powerpc64-linux-gnu-objcopy -O binary $< $@

# AARCH64
test-aarch64.elf: test.c support.c
	aarch64-linux-gnu-gcc $(CFLAGS) -o$@ $^

test-aarch64.bin: test-aarch64.elf
	aarch64-linux-gnu-objcopy -O binary $< $@

# Generate address files for all architectures
addresses: test-arm.elf test-mips.elf test-ppc.elf test-ppc64.elf test-aarch64.elf
	@for elf in $^; do \
		echo "=== $$elf ==="; \
		python3 find-addresses.py $$elf; \
	done
.PHONY: addresses

clean:
	rm -f *.elf *.bin
.PHONY: clean
