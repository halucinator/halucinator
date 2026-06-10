/*
 * ARMv7-A bare-metal vector table + reset entry. Vectors live at
 * 0x00000000 (VBAR_default = 0); the firmware doesn't reprogram VBAR.
 *
 *   0x00 reset       -> _reset
 *   0x04 undef       -> Default_Handler
 *   0x08 swi/svc     -> Default_Handler
 *   0x0C prefetch    -> Default_Handler
 *   0x10 data abort  -> Default_Handler
 *   0x14 reserved    -> spin
 *   0x18 IRQ         -> _irq_entry  (pushes regs, calls IRQ_Handler,
 *                                    pops, returns via subs pc, lr, #4)
 *   0x1C FIQ         -> Default_Handler
 */

    .syntax unified
    .arch armv7-a
    .arm

    .section .vectors, "ax"
    .global _vectors
_vectors:
    ldr pc, =_reset
    ldr pc, =Default_Handler
    ldr pc, =Default_Handler
    ldr pc, =Default_Handler
    ldr pc, =Default_Handler
    .word 0
    ldr pc, =_irq_entry
    ldr pc, =Default_Handler

    .text

    .global _reset
    .type   _reset, %function
_reset:
    /* SVC mode stack at top of RAM. */
    cps #0x13                     /* SVC */
    ldr sp, =_stack_top

    /* IRQ mode: smaller stack just below SVC stack. */
    cps #0x12                     /* IRQ */
    ldr sp, =_irq_stack_top

    /* Back to SVC for main(). IRQs masked until main calls cpsie. */
    cps #0x13
    bl  main

1:  b 1b

    .global _irq_entry
    .type   _irq_entry, %function
_irq_entry:
    /* Adjust IRQ return address: lr_irq points to the next instr+4.
     * Subtract 4 so resumption returns to the interrupted insn. */
    sub lr, lr, #4
    /* Save caller-saved registers + lr_irq. The C IRQ_Handler obeys
     * the AAPCS, so r0-r3, r12 and lr need stacking. */
    push {r0-r3, r12, lr}
    bl  IRQ_Handler
    pop {r0-r3, r12, lr}
    /* Return from IRQ: restore CPSR from SPSR_irq AND set PC = lr. */
    movs pc, lr
