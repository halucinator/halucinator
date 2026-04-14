ENTRY(_start)
SECTIONS {
    . = 0x40000000;
    .text : { *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : { *(.bss*) }
}
/* Stack at top of RAM (RAM starts at 0x50000000, size 0x100000) */
