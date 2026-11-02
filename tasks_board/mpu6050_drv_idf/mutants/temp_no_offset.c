#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "mpu6050.h"

#define REG_SMPLRT_DIV   0x19
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B
#define REG_WHO_AM_I     0x75
#define XFER_TIMEOUT_MS  50

static const float ACCEL_LSB[4] = {16384.0f, 8192.0f, 4096.0f, 2048.0f};   /* PS p.13 */
static const float GYRO_LSB[4] = {131.0f, 65.5f, 32.8f, 16.4f};            /* PS p.12 */

static i2c_master_dev_handle_t s_dev;
static mpu_accel_range_t s_accel;
static mpu_gyro_range_t s_gyro;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(s_dev, b, sizeof b, XFER_TIMEOUT_MS);
}

static esp_err_t rd(uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, buf, len, XFER_TIMEOUT_MS);
}

static int16_t be16(const uint8_t *p)
{
    return (int16_t)(((uint16_t)p[0] << 8) | p[1]);         /* 高字节在前（RM p.30） */
}

esp_err_t mpu_init(i2c_master_bus_handle_t bus, mpu_accel_range_t accel, mpu_gyro_range_t gyro)
{
    if (bus == NULL || (unsigned)accel > MPU_ACCEL_16G || (unsigned)gyro > MPU_GYRO_2000) {
        return ESP_ERR_INVALID_ARG;
    }
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_IMU_ADDR, .scl_speed_hz = 400000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &dc, &s_dev);
    if (err != ESP_OK) {
        return err;
    }
    uint8_t who = 0;
    if ((err = rd(REG_WHO_AM_I, &who, 1)) != ESP_OK) {
        mpu_deinit();
        return err;
    }
    if (who != 0x68) {
        mpu_deinit();
        return ESP_ERR_NOT_FOUND;
    }
    /* 上电默认睡眠（RM p.42）：SLEEP = 0，时钟源 CLKSEL = 1（X 轴陀螺 PLL） */
    if ((err = wr(REG_PWR_MGMT_1, 0x01)) != ESP_OK ||
        (err = wr(REG_CONFIG, 0x03)) != ESP_OK ||
        (err = wr(REG_SMPLRT_DIV, 9)) != ESP_OK ||
        (err = wr(REG_ACCEL_CONFIG, (uint8_t)(accel << 3))) != ESP_OK ||
        (err = wr(REG_GYRO_CONFIG, (uint8_t)(gyro << 3))) != ESP_OK) {
        mpu_deinit();
        return err;
    }
    s_accel = accel;
    s_gyro = gyro;
    vTaskDelay(pdMS_TO_TICKS(50));          /* 等第一组数据就绪 */
    return ESP_OK;
}

esp_err_t mpu_read_raw(mpu_raw_t *out)
{
    if (out == NULL || s_dev == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t b[14];
    esp_err_t err = rd(REG_ACCEL_XOUT_H, b, sizeof b);      /* 一次连续读 14 字节 */
    if (err != ESP_OK) {
        return err;
    }
    for (int i = 0; i < 3; i++) {
        out->accel[i] = be16(&b[2 * i]);
        out->gyro[i] = be16(&b[8 + 2 * i]);
    }
    out->temp = be16(&b[6]);
    return ESP_OK;
}

esp_err_t mpu_read(mpu_data_t *out)
{
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    mpu_raw_t r;
    esp_err_t err = mpu_read_raw(&r);
    if (err != ESP_OK) {
        return err;
    }
    for (int i = 0; i < 3; i++) {
        out->accel_g[i] = r.accel[i] / ACCEL_LSB[s_accel];
        out->gyro_dps[i] = r.gyro[i] / GYRO_LSB[s_gyro];
    }
    out->temp_c = r.temp / 340.0f;                  /* RM p.31 */
    return ESP_OK;
}

void mpu_deinit(void)
{
    if (s_dev != NULL) {
        i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
}
