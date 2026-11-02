#pragma once
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"

esp_err_t ppg_temp_read(i2c_master_bus_handle_t bus, float *celsius);
float ppg_temp_convert(uint8_t tint, uint8_t tfrac);
