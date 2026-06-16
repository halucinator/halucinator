/* Copyright 2026 Christopher Wright */

/* Flat i386 image loaded at 1 MiB. Everything (code/data/bss/stack)
 * lives in one rwx region; no paging, flat segments. */
ENTRY(_start)

SECTIONS
{
    . = 0x00100000;

    .text : {
        *(.text.start)
        *(.text*)
    }

    .rodata : { *(.rodata*) }
    .data   : { *(.data*) }

    .bss : {
        *(.bss*)
        *(COMMON)
    }

    . = ALIGN(16);
    . += 0x10000;          /* 64 KiB stack */
    __stack_top = .;

    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
