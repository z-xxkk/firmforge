#include "esp_rom_sys.h"
#include "hw_config.h"
#include "max30102_temp.h"

#define REG_INT_STATUS2 0x01
#define REG_INT_ENABLE2 0x03
#define DIE_TEMP_RDY    0x02
#define REG_TINT        0x1F
#define REG_TFRAC       0x20
#define REG_TEMP_CONFIG 0x21
#define TEMP_EN         0x01
#define XFER_TIMEOUT_MS 50

static esp_err_t rd(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t *v)
{
    return i2c_master_transmit_receive(dev, &reg, 1, v, 1, XFER_TIMEOUT_MS);
}

float ppg_temp_convert(uint8_t tint, uint8_t tfrac)
{
    /* 整数部分为二进制补码，每位 1 °C；小数部分 bit3–0，每位 0.0625 °C（数据手册 p.22） */
    return (float)(int8_t)tint + (float)(tfrac & 0x0F) * 0.0625f;
}

esp_err_t ppg_temp_read(i2c_master_bus_handle_t bus, float *celsius)
{
    if (bus == NULL || celsius == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_PPG_ADDR, .scl_speed_hz = 400000,
    };
    i2c_master_dev_handle_t dev;
    esp_err_t err = i2c_master_bus_add_device(bus, &dc, &dev);
    if (err != ESP_OK) {
        return err;
    }
    /* 测温流程按数据手册 p.26：打开 DIE_TEMP_RDY 中断标志，置 TEMP_EN，等标志置位。
       不能以 TEMP_EN 清零为准：实测它 0.26 ms 就清零，温度数据约 27.5 ms 才出来（p.4 采集时间 29 ms） */
    uint8_t en = 0, st = 0, tint = 0, tfrac = 0;
    err = rd(dev, REG_INT_ENABLE2, &en);
    if (err == ESP_OK && !(en & DIE_TEMP_RDY)) {
        uint8_t w[2] = {REG_INT_ENABLE2, (uint8_t)(en | DIE_TEMP_RDY)};     /* 只置这一位，其他位不动 */
        err = i2c_master_transmit(dev, w, sizeof w, XFER_TIMEOUT_MS);
    }
    if (err == ESP_OK) {
        err = rd(dev, REG_INT_STATUS2, &st);                                 /* 读一次清掉旧标志 */
    }
    if (err == ESP_OK) {
        uint8_t start[2] = {REG_TEMP_CONFIG, TEMP_EN};
        err = i2c_master_transmit(dev, start, sizeof start, XFER_TIMEOUT_MS);
    }
    for (int waited = 0; err == ESP_OK; waited++) {
        err = rd(dev, REG_INT_STATUS2, &st);
        if (err != ESP_OK || (st & DIE_TEMP_RDY)) {
            break;
        }
        if (waited >= 100) {
            err = ESP_ERR_TIMEOUT;
            break;
        }
        esp_rom_delay_us(1000);
    }
    if (err == ESP_OK) {
        err = rd(dev, REG_TINT, &tint);
    }
    if (err == ESP_OK) {
        err = rd(dev, REG_TFRAC, &tfrac);
    }
    i2c_master_bus_rm_device(dev);
    if (err == ESP_OK) {
        *celsius = ppg_temp_convert(tint, tfrac);
    }
    return err;
}
