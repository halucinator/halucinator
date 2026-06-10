/*
 * ARMv7-A bare-metal layout.
 *
 *   flash 0x00000000 - 0x00010000 (vectors + code)
 *   ram   0x40000000 - 0x40010000 (.bss + stack)
 *
 * Stack lives in RAM; IRQ stack carved from the top, SVC stack just
 * below it.
 */
ENTRY(_reset)

MEMORY
{
    flash (rx)  : ORIGIN = 0x00000000, LENGTH = 0x10000
    ram   (rwx) : ORIGIN = 0x40000000, LENGTH = 0x10000
}

SECTIONS
{
    .vectors : { KEEP(*(.vectors)) } > flash
    .text    : { *(.text*)        } > flash
    .rodata  : { *(.rodata*)      } > flash

    .data    : {
        _data_start = .;
        *(.data*)
        _data_end = .;
    } > ram AT > flash

    .bss : {
        _bss_start = .;
        *(.bss*) *(COMMON)
        _bss_end = .;
    } > ram

    /* Stacks at top of ram. */
    . = ORIGIN(ram) + LENGTH(ram) - 0x100;
    _irq_stack_top = .;
    . = . - 0x800;
    _stack_top = .;
}
