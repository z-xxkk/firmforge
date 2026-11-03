#pragma once
#include <stdint.h>
#include "esp_err.h"

typedef struct {
    uint32_t hz;              /* 平均频率 */
    uint32_t duty_permille;   /* 平均占空比（高电平占比，千分比） */
    uint32_t periods;         /* 参与平均的完整周期数 */
} pulse_stat_t;

esp_err_t pulse_meter_init(void);
esp_err_t pulse_meter_measure(uint32_t timeout_ms, pulse_stat_t *out);   /* 采集至少 32 个完整周期 */
void pulse_meter_deinit(void);
