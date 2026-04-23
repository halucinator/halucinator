/*
 * Minimal support stubs for bare-metal firmware.
 * These are intercepted by halucinator — the bodies are never executed.
 */

void uart_init(int uart_id) {
    (void)uart_id;
}

void uart_write(int uart_id, const char *buf, int len) {
    (void)uart_id;
    (void)buf;
    (void)len;
}

int uart_read(int uart_id, char *buf, int count) {
    (void)uart_id;
    (void)buf;
    (void)count;
    return 0;
}

/* Forward declaration */
extern void main(void);

/* Bare-metal entry point stubs */
void _exit(int status) {
    (void)status;
    while (1) {}
}

void _start(void) {
    main();
    _exit(0);
}
