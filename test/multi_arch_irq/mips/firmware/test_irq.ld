ENTRY(_reset)

PHDRS
{
    text PT_LOAD ;
    data PT_LOAD ;
}

MEMORY
{
    /* Map flash at kseg0 (0x80000000-0x9FFFFFFF) — unmapped,
     * cached, kernel segment. RAM at kseg1 0xA0010000 (unmapped
     * uncached). Putting ram at 0xA0010000 (not 0xA0000000) keeps
     * its physical translation (kseg1 → phys: subtract 0xA0000000)
     * separate from flash's (kseg0 → phys: subtract 0x80000000)
     * so both can coexist in QEMU MIPS where the YAML `alias_at`
     * mirror sits at the corresponding physical address. */
    flash (rx)  : ORIGIN = 0x80000000, LENGTH = 0x10000
    ram   (rwx) : ORIGIN = 0xA0010000, LENGTH = 0x10000
}

SECTIONS
{
    .text    : { *(.text*)   } > flash :text
    .rodata  : { *(.rodata*) } > flash :text

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
