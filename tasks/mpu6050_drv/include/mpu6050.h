#ifndef MPU6050_H
#define MPU6050_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    MPU6050_OK = 0,
    MPU6050_ERR_PARAM,   /* 参数错误或驱动未就绪 */
    MPU6050_ERR_BUS,     /* I2C 读写失败 */
    MPU6050_ERR_ID       /* WHO_AM_I 不是 0x68 */
} mpu6050_status_t;

typedef enum {
    MPU6050_ACCEL_2G = 0,
    MPU6050_ACCEL_4G = 1,
    MPU6050_ACCEL_8G = 2,
    MPU6050_ACCEL_16G = 3
} mpu6050_accel_range_t;

typedef enum {
    MPU6050_GYRO_250DPS = 0,
    MPU6050_GYRO_500DPS = 1,
    MPU6050_GYRO_1000DPS = 2,
    MPU6050_GYRO_2000DPS = 3
} mpu6050_gyro_range_t;

typedef struct {
    int16_t accel_raw[3];   /* X、Y、Z 原始值 */
    int16_t temp_raw;
    int16_t gyro_raw[3];
    int32_t accel_mg[3];    /* 单位：mg */
    int32_t gyro_cdps[3];   /* 单位：0.01 °/s */
    int32_t temp_cdeg;      /* 单位：0.01 °C */
} mpu6050_data_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t addr;           /* 7 位 I2C 地址 */
    uint8_t accel_range;
    uint8_t gyro_range;
    bool ready;             /* 初始化成功后为 true */
} mpu6050_t;

mpu6050_status_t mpu6050_init(mpu6050_t *dev, bool ad0_high,
                              mpu6050_accel_range_t accel_range, mpu6050_gyro_range_t gyro_range);
mpu6050_status_t mpu6050_read(const mpu6050_t *dev, mpu6050_data_t *out);

#endif /* MPU6050_H */
