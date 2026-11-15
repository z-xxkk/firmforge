#pragma once
#include "esp_err.h"

esp_err_t chip_temp_init(void);
esp_err_t chip_temp_read(float *celsius);
void chip_temp_deinit(void);
