#pragma once
#include <stdint.h>
#include "esp_err.h"

/* 在 FF_PIN_PWM_IN 上数上升沿。 */
esp_err_t edge_counter_init(void);
uint32_t edge_counter_get(void);
void edge_counter_reset(void);
void edge_counter_deinit(void);
