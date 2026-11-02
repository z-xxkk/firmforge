#pragma once
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"

typedef enum { MPU_ACCEL_2G = 0, MPU_ACCEL_4G, MPU_ACCEL_8G, MPU_ACCEL_16G } mpu_accel_range_t;
typedef enum { MPU_GYRO_250 = 0, MPU_GYRO_500, MPU_GYRO_1000, MPU_GYRO_2000 } mpu_gyro_range_t;
typedef struct { int16_t accel[3], gyro[3], temp; } mpu_raw_t;
typedef struct { float accel_g[3], gyro_dps[3], temp_c; } mpu_data_t;

esp_err_t mpu_init(i2c_master_bus_handle_t bus, mpu_accel_range_t accel, mpu_gyro_range_t gyro);
esp_err_t mpu_read_raw(mpu_raw_t *out);
esp_err_t mpu_read(mpu_data_t *out);
void mpu_deinit(void);
