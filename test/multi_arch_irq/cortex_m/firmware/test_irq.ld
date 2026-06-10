/*
 * Linker script for Cortex-M3 IRQ test firmware.
 *
 * Memory layout matches HALucinator's test/multi_arch_irq/cortex_m/
 * test_irq_memory.yaml:
 *   flash @ 0x08000000  (vector table + .text)
 *   ram   @ 0x20000000  (.bss + .stack — .data unused)
 *
 * The matching `alias` region at 0x00000000 in the YAML is what QEMU's
 * configurable machine reads on reset; flash is the live-load target.
 */
ENTRY(Reset_Handler)

MEMORY {
    flash (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
    ram   (rwx) : ORIGIN = 0x20000000, LENGTH = 32K
}

SECTIONS {
    . = ORIGIN(flash);

    .vectors : {
        KEEP(*(.vectors))
    } > flash

    .text : {
        *(.text*)
        *(.rodata*)
    } > flash

    . = ORIGIN(ram);

    .bss : {
        *(.bss*)
        *(COMMON)
    } > ram

    /* Stack at top of RAM, growing down. */
    _stack_top = ORIGIN(ram) + LENGTH(ram);
}
