# Copyright 2026 Christopher Wright

# i386 (32-bit x86) freestanding build. Uses clang/lld/llvm-objcopy so it
# reproduces on macOS and Linux without an i686-elf cross-gcc (CI ubuntu
# and dev macs both have LLVM). The committed test_irq.bin was built this
# way; `make` regenerates it identically.
CC      ?= clang
OBJCOPY ?= llvm-objcopy
NM      ?= llvm-nm

TARGET  = --target=i386-unknown-linux-gnu
CFLAGS  = $(TARGET) -ffreestanding -nostdlib -fno-pic -fno-pie \
          -fno-stack-protector -fno-builtin -mno-sse -Os -g \
          -ffunction-sections -fdata-sections -Wall -Wextra
# IRQ_Handler has no in-firmware call site (X86Pic vectors to it by
# address from the YAML), so force-keep it against --gc-sections.
LDFLAGS = $(TARGET) -nostdlib -fno-pic -fno-pie -fuse-ld=lld \
          -Wl,-T,test_irq.ld -Wl,--gc-sections -Wl,-Map=test_irq.map \
          -Wl,--undefined=isr -static -no-pie

OBJS = startup.o test_irq.o

all: test_irq.bin test_irq.elf

startup.o: startup.s
	$(CC) $(TARGET) -c $< -o $@

test_irq.o: test_irq.c
	$(CC) $(CFLAGS) -c $< -o $@

test_irq.elf: $(OBJS) test_irq.ld
	$(CC) $(LDFLAGS) $(OBJS) -o $@
	$(NM) -n $@ > test_irq.sym

test_irq.bin: test_irq.elf
	$(OBJCOPY) -O binary $< $@

clean:
	rm -f *.o *.elf *.bin *.map *.sym
