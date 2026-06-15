/* Copyright 2026 Christopher Wright */

/*
 * Cortex-M3 startup: vector table + Reset handler.
 *
 * Vector table layout (entries are 4-byte function pointers, except
 * entry 0 = initial MSP):
 *   0   Initial MSP
 *   1   Reset_Handler (entry point + 1 for Thumb bit)
 *   2   NMI_Handler
 *   3   HardFault_Handler
 *   4..15  reserved
 *   16+ External IRQ handlers (IRQ 0..239 → vector index 16..255)
 *
 * We point every external IRQ at a single shared IRQ_Handler so the
 * test can pick any number and still get the flag set.
 */
    .syntax unified
    .cpu cortex-m3
    .thumb

    .section .vectors, "ax"
    .align 2
    .global _vectors
_vectors:
    .word _stack_top                /* Initial MSP */
    .word Reset_Handler + 1
    .word HardFault_Handler + 1     /* NMI -> hardfault for simplicity */
    .word HardFault_Handler + 1     /* HardFault */
    .word HardFault_Handler + 1     /* MemManage */
    .word HardFault_Handler + 1     /* BusFault */
    .word HardFault_Handler + 1     /* UsageFault */
    .word 0                          /* reserved */
    .word 0                          /* reserved */
    .word 0                          /* reserved */
    .word 0                          /* reserved */
    .word HardFault_Handler + 1     /* SVCall */
    .word HardFault_Handler + 1     /* Debug */
    .word 0                          /* reserved */
    .word HardFault_Handler + 1     /* PendSV */
    .word HardFault_Handler + 1     /* SysTick */
    /* External IRQs 0..239 — all share IRQ_Handler. */
    .rept 240
    .word IRQ_Handler + 1
    .endr

    .section .text
    .thumb_func
    .global Reset_Handler
Reset_Handler:
    /* Set initial MSP (CPU does this from vector[0], but be explicit). */
    ldr r0, =_stack_top
    msr msp, r0

    /* Jump straight to main; no .data / .bss copy needed for this
     * tiny firmware (we don't use either). */
    bl main

    /* main shouldn't return, but if it does, hardfault. */
    bl HardFault_Handler
1:  b 1b
