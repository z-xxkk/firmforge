/* 隐藏测试用的假 I2C：模拟一颗 MPU6050 的寄存器，并记录驱动发出的每一次读写 */
#ifndef MOCK_HAL_I2C_H
#define MOCK_HAL_I2C_H

#include <stdbool.h>
#include <stdint.h>

#include "hal_i2c.h"

#define MOCK_LOG_MAX 64u

typedef struct {
    bool is_write;
    uint8_t addr;
    uint8_t reg;
    uint16_t len;
} mock_txn_t;

extern uint8_t mock_regs[256];
extern mock_txn_t mock_log[MOCK_LOG_MAX];
extern unsigned mock_log_count;

/* 复位：芯片挂在 present_addr 上，寄存器恢复上电值（0x6B = 0x40，0x75 = 0x68，其余 0） */
void mock_reset(uint8_t present_addr);
/* 让第 n 次（从 1 数起）总线操作失败；0 表示不失败 */
void mock_fail_on(unsigned n);
/* 按高字节在前写入一个 16 位寄存器对 */
void mock_set_be16(uint8_t reg, int16_t value);
unsigned mock_write_count(void);

#endif /* MOCK_HAL_I2C_H */
