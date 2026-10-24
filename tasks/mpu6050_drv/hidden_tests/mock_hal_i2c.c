#include "mock_hal_i2c.h"

#include <stddef.h>

uint8_t mock_regs[256];
mock_txn_t mock_log[MOCK_LOG_MAX];
unsigned mock_log_count;

static uint8_t present;
static unsigned fail_n;
static unsigned txn_n;

void mock_reset(uint8_t present_addr)
{
    for (unsigned i = 0; i < 256u; i++) {
        mock_regs[i] = 0u;
    }
    mock_regs[0x6B] = 0x40u;
    mock_regs[0x75] = 0x68u;
    present = present_addr;
    fail_n = 0u;
    txn_n = 0u;
    mock_log_count = 0u;
}

void mock_fail_on(unsigned n)
{
    fail_n = n;
}

void mock_set_be16(uint8_t reg, int16_t value)
{
    uint16_t u = (uint16_t)value;
    mock_regs[reg] = (uint8_t)(u >> 8);
    mock_regs[(uint8_t)(reg + 1u)] = (uint8_t)(u & 0xFFu);
}

unsigned mock_write_count(void)
{
    unsigned n = 0;
    for (unsigned i = 0; i < mock_log_count; i++) {
        if (mock_log[i].is_write) {
            n++;
        }
    }
    return n;
}

static bool begin(bool is_write, uint8_t addr, uint8_t reg, uint16_t len)
{
    txn_n++;
    if (mock_log_count < MOCK_LOG_MAX) {
        mock_log[mock_log_count].is_write = is_write;
        mock_log[mock_log_count].addr = addr;
        mock_log[mock_log_count].reg = reg;
        mock_log[mock_log_count].len = len;
        mock_log_count++;
    }
    if (fail_n != 0u && txn_n == fail_n) {
        return false;
    }
    return addr == present;   /* 地址不对相当于没有应答 */
}

hal_i2c_status_t hal_i2c_mem_read(uint8_t dev_addr, uint8_t reg, uint8_t *data, uint16_t len)
{
    if (data == NULL || !begin(false, dev_addr, reg, len)) {
        return HAL_I2C_ERROR;
    }
    for (uint16_t i = 0; i < len; i++) {
        data[i] = mock_regs[(uint8_t)(reg + i)];
    }
    return HAL_I2C_OK;
}

hal_i2c_status_t hal_i2c_mem_write(uint8_t dev_addr, uint8_t reg, const uint8_t *data, uint16_t len)
{
    if (data == NULL || !begin(true, dev_addr, reg, len)) {
        return HAL_I2C_ERROR;
    }
    for (uint16_t i = 0; i < len; i++) {
        mock_regs[(uint8_t)(reg + i)] = data[i];
    }
    return HAL_I2C_OK;
}
