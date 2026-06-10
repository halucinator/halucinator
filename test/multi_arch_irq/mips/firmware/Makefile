CC = mips-linux-gnu-gcc
OBJCOPY = mips-linux-gnu-objcopy
NM = mips-linux-gnu-nm

CFLAGS = -mno-abicalls -G0 -fno-pic -nostdlib -ffreestanding -Os -g \
         -ffunction-sections -fdata-sections -fno-builtin \
         -Wall -Werror -fno-stack-protector

LDFLAGS = -mno-abicalls -G0 -fno-pic -nostdlib -ffreestanding \
          -T test_irq.ld -Wl,--gc-sections -Wl,-Map=test_irq.map \
          -static -no-pie

OBJS = startup.o test_irq.o

all: test_irq.bin test_irq.elf

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

%.o: %.s
	$(CC) $(CFLAGS) -c $< -o $@

test_irq.elf: $(OBJS) test_irq.ld
	$(CC) $(LDFLAGS) $(OBJS) -o $@

test_irq.bin: test_irq.elf
	$(OBJCOPY) -O binary $< $@

addresses: test_irq.elf
	@$(NM) test_irq.elf | grep -E " [TtDd] (main|uart_init|uart_write|IRQ_Handler|Default_Handler|_reset|_irq_entry_simple)" | sort

clean:
	rm -f *.o *.elf *.bin *.map

.PHONY: all clean addresses
