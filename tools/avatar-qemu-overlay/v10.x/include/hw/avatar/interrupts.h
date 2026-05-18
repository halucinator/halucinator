#ifndef HW_AVATAR_INTERRUPTS_H
#define HW_AVATAR_INTERRUPTS_H

/*
 * Avatar log mask. Defined here instead of patching include/qemu/log.h so
 * the overlay doesn't fight with churn in upstream's log-bit numbering.
 * Bit 24 is the first slot above LOG_INVALID_MEM (1<<23) across v10–v11.
 */
#ifndef LOG_AVATAR
#define LOG_AVATAR (1 << 24)
#endif

enum RemoteInterruptOperation{
  INTERRUPT_ENTER,
  INTERRUPT_EXIT,
};

typedef struct V7MInterruptReq{
  uint64_t id;
  uint32_t num_irq;
  uint32_t operation;
  uint32_t type;
} V7MInterruptReq;

typedef struct V7MInterruptResp{
    uint64_t id;
    bool success;
    uint32_t operation;
} V7MInterruptResp;

void avatar_armv7m_exception_exit(int irq, uint32_t type);
void avatar_armv7m_exception_enter(int irq);
void avatar_armv7m_nvic_forward_write(uint32_t offset, uint32_t value,unsigned size);

#endif
