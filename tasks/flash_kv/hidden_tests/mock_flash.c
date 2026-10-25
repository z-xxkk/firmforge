#include "mock_flash.h"

#include <string.h>

#include "hal_flash.h"

uint8_t mock_flash[MOCK_FLASH_SIZE];
unsigned mock_erases, mock_write_calls, mock_violations, mock_units;
bool mock_read_fail;

static bool cut_armed;
static unsigned cut_left;

void mock_flash_blank(void)
{
    memset(mock_flash, 0xFF, sizeof mock_flash);
    mock_erases = mock_write_calls = mock_violations = mock_units = 0;
    mock_read_fail = false;
    cut_armed = false;
}

void mock_cut_after(unsigned units)
{
    cut_armed = true;
    cut_left = units;
}

void mock_power_on(void)
{
    cut_armed = false;
}

static bool unit_allowed(void)
{
    if (cut_armed) {
        if (cut_left == 0) {
            return false;
        }
        cut_left--;
    }
    mock_units++;
    return true;
}

hal_flash_status_t hal_flash_read(uint32_t addr, uint8_t *buf, size_t len)
{
    if (mock_read_fail || buf == NULL || addr + len > MOCK_FLASH_SIZE) {
        return HAL_FLASH_ERROR;
    }
    memcpy(buf, &mock_flash[addr], len);
    return HAL_FLASH_OK;
}

hal_flash_status_t hal_flash_write(uint32_t addr, const uint8_t *data, size_t len)
{
    if (data == NULL || addr + len > MOCK_FLASH_SIZE) {
        return HAL_FLASH_ERROR;
    }
    mock_write_calls++;
    for (size_t i = 0; i < len; i++) {
        if (!unit_allowed()) {
            return HAL_FLASH_ERROR;
        }
        if ((uint8_t)(data[i] & (uint8_t)~mock_flash[addr + i]) != 0) {
            mock_violations++;
        }
        mock_flash[addr + i] &= data[i];
    }
    return HAL_FLASH_OK;
}

hal_flash_status_t hal_flash_erase(uint32_t sector)
{
    if (sector >= 2u) {
        return HAL_FLASH_ERROR;
    }
    if (!unit_allowed()) {
        return HAL_FLASH_ERROR;
    }
    mock_erases++;
    memset(&mock_flash[sector * 1024u], 0xFF, 1024u);
    return HAL_FLASH_OK;
}
