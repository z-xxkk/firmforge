#include "esp_rom_sys.h"
#include "hw_config.h"
#include "max30102.h"

#define REG_FIFO_WR_PTR  0x04
#define REG_OVF_COUNTER  0x05
#define REG_FIFO_RD_PTR  0x06
#define REG_FIFO_DATA    0x07
#define REG_FIFO_CONFIG  0x08
#define REG_MODE_CONFIG  0x09
#define REG_SPO2_CONFIG  0x0A
#define REG_LED1_PA      0x0C
#define REG_LED2_PA      0x0D
#define REG_PART_ID      0xFF
#define MODE_RESET       0x40
#define MODE_SPO2        0x03       /* 011：红光 + 红外（p.18） */
#define FIFO_DEPTH       32
#define XFER_TIMEOUT_MS  50

static i2c_master_dev_handle_t s_dev;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(s_dev, b, sizeof b, XFER_TIMEOUT_MS);
}

static esp_err_t rd(uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, buf, len, XFER_TIMEOUT_MS);
}

static int rate_code(uint16_t sps)
{
    switch (sps) {          /* 表 6 SPO2_SR（p.19） */
    case 50: return 0;
    case 100: return 1;
    case 200: return 2;
    case 400: return 3;
    default: return -1;
    }
}

esp_err_t ppg_init(i2c_master_bus_handle_t bus, uint16_t rate_sps)
{
    int code = rate_code(rate_sps);
    if (bus == NULL || code < 0) {
        return ESP_ERR_INVALID_ARG;
    }
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_PPG_ADDR, .scl_speed_hz = 400000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &dc, &s_dev);
    if (err != ESP_OK) {
        s_dev = NULL;
        return err;
    }
    uint8_t v = 0;
    if ((err = rd(REG_PART_ID, &v, 1)) == ESP_OK && v != 0x15) {
        err = ESP_ERR_NOT_FOUND;
    }
    if (err == ESP_OK) {
        err = wr(REG_MODE_CONFIG, MODE_RESET);
    }
    for (int waited = 0; err == ESP_OK; waited++) {     /* RESET 位复位完成后自动清零（p.18） */
        if ((err = rd(REG_MODE_CONFIG, &v, 1)) != ESP_OK || !(v & MODE_RESET)) {
            break;
        }
        if (waited >= 100) {
            err = ESP_ERR_TIMEOUT;
            break;
        }
        esp_rom_delay_us(1000);
    }
    uint8_t spo2 = (uint8_t)((0x1 << 5) | (code << 2) | 0x3);      /* ADC 量程 01、采样率、LED_PW 11 */
    if (err == ESP_OK &&
        ((err = wr(REG_FIFO_CONFIG, 0x10)) != ESP_OK ||             /* SMP_AVE = 0，FIFO_ROLLOVER_EN = 1 */
         (err = wr(REG_SPO2_CONFIG, spo2)) != ESP_OK ||
         (err = wr(REG_LED1_PA, 0x24)) != ESP_OK || (err = wr(REG_LED2_PA, 0x24)) != ESP_OK ||
         (err = wr(REG_FIFO_WR_PTR, 0)) != ESP_OK || (err = wr(REG_OVF_COUNTER, 0)) != ESP_OK ||
         (err = wr(REG_FIFO_RD_PTR, 0)) != ESP_OK || (err = wr(REG_MODE_CONFIG, MODE_SPO2)) != ESP_OK)) {
        /* err 已设置 */
    }
    if (err != ESP_OK) {
        ppg_deinit();
    }
    return err;
}

static uint32_t sample18(const uint8_t *p)
{
    /* 3 字节高字节在前；数据左对齐，MSB 总在 bit17，不要移位，只取低 18 位（p.14–15） */
    return (((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2]) & 0x3FFFF;
}

int ppg_read(ppg_sample_t *buf, size_t cap)
{
    if (s_dev == NULL || (buf == NULL && cap > 0)) {
        return -1;
    }
    uint8_t wr_ptr = 0, rd_ptr = 0;
    if (rd(REG_FIFO_WR_PTR, &wr_ptr, 1) != ESP_OK || rd(REG_FIFO_RD_PTR, &rd_ptr, 1) != ESP_OK) {
        return -1;
    }
    int diff = (int)wr_ptr - (int)rd_ptr;
    size_t avail = diff > 0 ? (size_t)diff : 0;
    size_t n = avail < cap ? avail : cap;
    if (n == 0) {
        return 0;
    }
    uint8_t raw[FIFO_DEPTH * 6];
    if (rd(REG_FIFO_DATA, raw, n * 6) != ESP_OK) {
        return -1;
    }
    for (size_t i = 0; i < n; i++) {
        buf[i].red = sample18(&raw[6 * i]);
        buf[i].ir = sample18(&raw[6 * i + 3]);
    }
    return (int)n;
}

void ppg_deinit(void)
{
    if (s_dev != NULL) {
        i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
}
