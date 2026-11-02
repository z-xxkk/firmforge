#pragma once
#include <stdint.h>
#include "esp_err.h"

esp_err_t tick_start(uint32_t hz);     /* 1–10000 */
uint32_t tick_count(void);
esp_err_t tick_stop(void);
