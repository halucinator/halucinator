ENTRY(_reset)

PHDRS
{
    text PT_LOAD ;
    data PT_LOAD ;
}

MEMORY
{
    flash (rx)  : ORIGIN = 0x00000000, LENGTH = 0x10000
    ram   (rwx) : ORIGIN = 0x40000000, LENGTH = 0x10000
}

SECTIONS
{
    .vectors : { KEEP(*(.vectors)) } > flash :text
    .text    : { *(.text*)        } > flash :text
    .rodata  : { *(.rodata*)      } > flash :text

    .data    : {
        _data_start = .;
        *(.data*)
        _data_end = .;
    } > ram AT > flash :data

    .bss : {
        _bss_start = .;
        *(.bss*) *(COMMON)
        _bss_end = .;
    } > ram :NONE

    . = ORIGIN(ram) + LENGTH(ram) - 0x10;
    _stack_top = .;
}
