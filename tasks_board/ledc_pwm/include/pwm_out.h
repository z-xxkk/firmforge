#pragma once
#include <stdint.h>
#include "esp_err.h"

/* 在 FF_PIN_PWM_OUT 上输出 PWM。freq_hz：100–100000；duty_permille：0–1000。 */
esp_err_t pwm_init(uint32_t freq_hz, uint32_t duty_permille);
esp_err_t pwm_set_duty(uint32_t duty_permille);
void pwm_deinit(void);
