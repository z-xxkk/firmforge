#include "driver/i2c.h"
#include "bus_scan.h"

/* 错误：用旧版命令链接口探测（v6.1 仍能编译，但和调用方的新版总线冲突） */
esp_err_t bus_scan(i2c_master_bus_handle_t bus, uint8_t *found, size_t cap, size_t *n)
{
    (void)bus;
    if (n == NULL || (cap > 0 && found == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t count = 0;
    for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
        i2c_cmd_handle_t cmd = i2c_cmd_link_create();
        i2c_master_start(cmd);
        i2c_master_write_byte(cmd, (uint8_t)(addr << 1), true);
        i2c_master_stop(cmd);
        esp_err_t err = i2c_master_cmd_begin(I2C_NUM_0, cmd, 5);
        i2c_cmd_link_delete(cmd);
        if (err == ESP_OK) {
            if (count < cap) {
                found[count] = addr;
            }
            count++;
        }
    }
    *n = count;
    return ESP_OK;
}
