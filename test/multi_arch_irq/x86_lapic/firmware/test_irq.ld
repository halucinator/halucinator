/* Copyright 2026 Christopher Wright */

/* Loaded at 1 MiB — the configurable machine now starts flat (4 GiB
 * segments), so high loading works. Code, GDT/IDT and stack live in the
 * one rwx region. */
ENTRY(_start)
SECTIONS
{
    . = 0x00100000;
    .text   : { *(.text.start) *(.text*) }
    .rodata : { *(.rodata*) }
    .data   : { *(.data*) }
    .bss    : { *(.bss*) *(COMMON) }
    . = ALIGN(16);
    . += 0x10000;          /* 64 KiB stack */
    __stack_top = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
