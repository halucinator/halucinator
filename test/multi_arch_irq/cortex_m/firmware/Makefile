CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy

CFLAGS = -mcpu=cortex-m3 -mthumb -nostdlib -ffreestanding -Os -g \
         -ffunction-sections -fdata-sections -fno-builtin \
         -Wall -Werror

LDFLAGS = -mcpu=cortex-m3 -mthumb -nostdlib -ffreestanding \
          -T test_irq.ld -Wl,--gc-sections -Wl,-Map=test_irq.map

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

clean:
	rm -f *.o *.elf *.bin *.map

.PHONY: all clean
