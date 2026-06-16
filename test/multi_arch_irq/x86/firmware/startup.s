/* Copyright 2026 Christopher Wright */

/* i386 entry stub: set the stack and jump to main().
 * halucinator also sets EIP=entry_addr and ESP=init_sp from the YAML,
 * but we set ESP here too so the .bin is self-contained if run directly. */
        .code32
        .section .text.start, "ax"
        .global _start
_start:
        movl    $__stack_top, %esp
        call    main
.hang:
        pause
        jmp     .hang
