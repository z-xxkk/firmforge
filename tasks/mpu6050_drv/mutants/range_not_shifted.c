#include "mpu6050.h"

#include <stddef.h>

#include "hal_i2c.h"

#define REG_GYRO_CONFIG   0x1Bu
#define REG_ACCEL_CONFIG  0x1Cu
#define REG_DATA_START    0x3Bu
#define REG_PWR_MGMT_1    0x6Bu
#define REG_WHO_AM_I      0x75u
#define WHO_AM_I_VALUE    0x68u
#define PWR_WAKE_PLL_X    0x01u
#define DATA_LEN          14u

static const int32_t accel_lsb_per_g[4] = {16384, 8192, 4096, 2048};
static const int32_t gyro_lsb_x10[4] = {1310, 655, 328, 164};

static mpu6050_status_t write_reg(uint8_t addr, uint8_t reg, uint8_t value)
{
    return hal_i2c_mem_write(addr, reg, &value, 1u) == HAL_I2C_OK ? MPU6050_OK : MPU6050_ERR_BUS;
}

mpu6050_status_t mpu6050_init(mpu6050_t *dev, bool ad0_high,
                              mpu6050_accel_range_t accel_range, mpu6050_gyro_range_t gyro_range)
{
    uint8_t id = 0u;
    mpu6050_status_t st;

    if (dev == NULL) {
        return MPU6050_ERR_PARAM;
    }
    dev->ready = false;
    if ((unsigned)accel_range > 3u || (unsigned)gyro_range > 3u) {
        return MPU6050_ERR_PARAM;
    }
    dev->addr = ad0_high ? 0x69u : 0x68u;
    dev->accel_range = (uint8_t)accel_range;
    dev->gyro_range = (uint8_t)gyro_range;

    if (hal_i2c_mem_read(dev->addr, REG_WHO_AM_I, &id, 1u) != HAL_I2C_OK) {
        return MPU6050_ERR_BUS;
    }
    if (id != WHO_AM_I_VALUE) {
        return MPU6050_ERR_ID;
    }
    st = write_reg(dev->addr, REG_PWR_MGMT_1, PWR_WAKE_PLL_X);
    if (st == MPU6050_OK) {
        st = write_reg(dev->addr, REG_GYRO_CONFIG, (uint8_t)(dev->gyro_range << 1));   /* 变异：位置不对 */
    }
    if (st == MPU6050_OK) {
        st = write_reg(dev->addr, REG_ACCEL_CONFIG, (uint8_t)(dev->accel_range << 1));
    }
    if (st == MPU6050_OK) {
        dev->ready = true;
    }
    return st;
}

static int16_t be16(const uint8_t *p)
{
    return (int16_t)(uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

mpu6050_status_t mpu6050_read(const mpu6050_t *dev, mpu6050_data_t *out)
{
    uint8_t raw[DATA_LEN];

    if (dev == NULL || out == NULL || !dev->ready || dev->accel_range > 3u || dev->gyro_range > 3u) {
        return MPU6050_ERR_PARAM;
    }
    if (hal_i2c_mem_read(dev->addr, REG_DATA_START, raw, DATA_LEN) != HAL_I2C_OK) {
        return MPU6050_ERR_BUS;
    }
    for (unsigned i = 0u; i < 3u; i++) {
        out->accel_raw[i] = be16(&raw[2u * i]);
        out->gyro_raw[i] = be16(&raw[8u + 2u * i]);
        out->accel_mg[i] = (int32_t)out->accel_raw[i] * 1000 / accel_lsb_per_g[dev->accel_range];
        out->gyro_cdps[i] = (int32_t)out->gyro_raw[i] * 1000 / gyro_lsb_x10[dev->gyro_range];
    }
    out->temp_raw = be16(&raw[6]);
    out->temp_cdeg = (int32_t)out->temp_raw * 100 / 340 + 3653;
    return MPU6050_OK;
}
