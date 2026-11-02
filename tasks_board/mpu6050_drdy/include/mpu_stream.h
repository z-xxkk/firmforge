#pragma once
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

typedef struct { int16_t accel[3], gyro[3], temp; } mpu_sample_t;

esp_err_t mpu_stream_start(i2c_master_bus_handle_t bus, uint32_t rate_hz, QueueHandle_t q);   /* 10–200 Hz */
esp_err_t mpu_stream_stop(void);
uint32_t mpu_stream_dropped(void);
