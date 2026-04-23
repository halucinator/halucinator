/*
 * Minimal UART test firmware for HALucinator multi-arch e2e tests.
 *
 * This firmware calls stub functions (uart_init, uart_write, uart_read)
 * that HALucinator intercepts via breakpoint handlers. No actual
 * hardware access occurs — all I/O goes through halucinator's zmq
 * peripheral model.
 *
 * Cross-compile for each target architecture and provide the resulting
 * binary to halucinator with appropriate config YAMLs.
 */

/* These are intercepted by halucinator BP handlers */
void uart_init(int uart_id);
void uart_write(int uart_id, const char *buf, int len);
int  uart_read(int uart_id, char *buf, int count);

/* Minimal string length */
static int strlen(const char *s) {
    int n = 0;
    while (s[n]) n++;
    return n;
}

/* UART ID — matches the peripheral address used in config */
#define UART_ID 0x40013800

void main(void) {
    uart_init(UART_ID);

    const char *banner = "\r\n ****Multi-Arch UART Test****\r\n Enter 10 characters using keyboard :\r\n";
    uart_write(UART_ID, banner, strlen(banner));

    char buf[10];
    uart_read(UART_ID, buf, 10);

    const char *echo_prefix = "\r\n Received: ";
    uart_write(UART_ID, echo_prefix, strlen(echo_prefix));
    uart_write(UART_ID, buf, 10);

    const char *done = "\r\n Example Finished\r\n";
    uart_write(UART_ID, done, strlen(done));

    /* Halt */
    while (1) {}
}
