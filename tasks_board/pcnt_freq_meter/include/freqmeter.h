#pragma once
#include <stdint.h>
#include "esp_err.h"

/* 在 FF_PIN_PWM_IN 上测方波频率。 */
esp_err_t freqmeter_init(void);
esp_err_t freqmeter_measure(uint32_t gate_ms, uint32_t *hz);   /* gate_ms：10–1000 */
void freqmeter_deinit(void);
