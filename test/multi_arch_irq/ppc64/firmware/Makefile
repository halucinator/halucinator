AS = powerpc64le-linux-gnu-as
LD = powerpc64le-linux-gnu-ld
OBJCOPY = powerpc64le-linux-gnu-objcopy
NM = powerpc64le-linux-gnu-nm

ASFLAGS = -a64 -mbig -mppc64
LDFLAGS = -m elf64ppc -T test_irq.ld

all: test_irq.bin test_irq.elf

test_irq.o: test_irq.s
	$(AS) $(ASFLAGS) -o $@ $<

test_irq.elf: test_irq.o test_irq.ld
	$(LD) $(LDFLAGS) -o $@ test_irq.o

test_irq.bin: test_irq.elf
	$(OBJCOPY) -O binary $< $@

addresses: test_irq.elf
	@$(NM) test_irq.elf | grep -E " [TtDdBb] " | sort

clean:
	rm -f *.o *.elf *.bin

.PHONY: all clean addresses
