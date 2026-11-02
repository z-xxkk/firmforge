#include "esp_rom_sys.h"
#include "hw_config.h"
#include "max30102_temp.h"

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
    uint8_t reset[2] = {0x09, 0x40};                /* 错误：先复位器件 */
    i2c_master_transmit(dev, reset, sizeof reset, XFER_TIMEOUT_MS);
    esp_rom_delay_us(2000);
    uint8_t start[2] = {REG_TEMP_CONFIG, TEMP_EN};
    uint8_t cfg = TEMP_EN, tint = 0, tfrac = 0;
    err = i2c_master_transmit(dev, start, sizeof start, XFER_TIMEOUT_MS);
    for (int waited = 0; err == ESP_OK && (cfg & TEMP_EN); waited++) {   /* TEMP_EN 测完自动清零 */
        if (waited >= 100) {
            err = ESP_ERR_TIMEOUT;
            break;
        }
        esp_rom_delay_us(1000);
        err = rd(dev, REG_TEMP_CONFIG, &cfg);
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
