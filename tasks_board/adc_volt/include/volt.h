#pragma once
#include "esp_err.h"

esp_err_t volt_init(void);
esp_err_t volt_read_mv(int *mv);
void volt_deinit(void);
