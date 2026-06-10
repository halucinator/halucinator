ENTRY(_start)

MEMORY
{
    flash (rx) : ORIGIN = 0x00000000, LENGTH = 0x10000
    ram   (rw) : ORIGIN = 0x40000000, LENGTH = 0x10000
}

SECTIONS
{
    .text   : { *(.text*)   } > flash
    .rodata : { *(.rodata*) } > flash

    .vectors 0x500 : { KEEP(*(.vector_500)) } > flash

    .bss    : { *(.bss*) *(COMMON) } > ram

    /DISCARD/ : { *(.note.gnu.build-id) *(.opd) }
}
