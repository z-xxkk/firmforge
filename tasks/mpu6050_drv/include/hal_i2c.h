#ifndef HAL_I2C_H
#define HAL_I2C_H

#include <stdint.h>

typedef enum {
    HAL_I2C_OK = 0,
    HAL_I2C_ERROR = 1
} hal_i2c_status_t;

/* dev_addr 是 7 位器件地址（例如 0x68），不需要左移。
 * 从器件的 reg 寄存器开始连续读 len 个字节到 data。 */
hal_i2c_status_t hal_i2c_mem_read(uint8_t dev_addr, uint8_t reg, uint8_t *data, uint16_t len);

/* 从器件的 reg 寄存器开始连续写 len 个字节。 */
hal_i2c_status_t hal_i2c_mem_write(uint8_t dev_addr, uint8_t reg, const uint8_t *data, uint16_t len);

#endif /* HAL_I2C_H */
