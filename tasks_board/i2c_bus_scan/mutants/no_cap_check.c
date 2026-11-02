#include "bus_scan.h"

#define FIRST_ADDR 0x08         /* 0x00–0x07、0x78–0x7F 为 I2C 规范保留地址（UM10204 §3.1.12） */
#define LAST_ADDR  0x77
#define PROBE_TIMEOUT_MS 50

esp_err_t bus_scan(i2c_master_bus_handle_t bus, uint8_t *found, size_t cap, size_t *n)
{
    if (bus == NULL || n == NULL || (cap > 0 && found == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t count = 0;
    for (uint16_t addr = FIRST_ADDR; addr <= LAST_ADDR; addr++) {
        if (i2c_master_probe(bus, addr, PROBE_TIMEOUT_MS) == ESP_OK) {
            found[count] = (uint8_t)addr;
            count++;
        }
    }
    *n = count;
    return ESP_OK;
}
