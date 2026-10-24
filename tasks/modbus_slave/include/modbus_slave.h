#ifndef MODBUS_SLAVE_H
#define MODBUS_SLAVE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MODBUS_MAX_FRAME          256u
#define MODBUS_BROADCAST_ADDRESS  0u

#define MODBUS_EX_ILLEGAL_FUNCTION      0x01u
#define MODBUS_EX_ILLEGAL_DATA_ADDRESS  0x02u
#define MODBUS_EX_ILLEGAL_DATA_VALUE    0x03u
#define MODBUS_EX_SERVER_DEVICE_FAILURE 0x04u

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t address;     /* 本机地址 1–247；初始化失败时为 0 */
    uint16_t *regs;      /* 保持寄存器数组（调用方提供） */
    uint16_t reg_count;
} modbus_slave_t;

bool   modbus_slave_init(modbus_slave_t *s, uint8_t address, uint16_t *regs, uint16_t reg_count);
size_t modbus_slave_handle(modbus_slave_t *s, const uint8_t *req, size_t req_len,
                           uint8_t *resp, size_t resp_size);

#endif /* MODBUS_SLAVE_H */
