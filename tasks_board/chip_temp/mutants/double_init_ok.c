/* 变异体：重复初始化返回 ESP_OK */
#include <stddef.h>
#include "driver/temperature_sensor.h"
#include "chip_temp.h"

#define SAMPLES 5

static temperature_sensor_handle_t s_ts;

esp_err_t chip_temp_init(void)
{
    if (s_ts != NULL) {
        return ESP_OK;
    }
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    temperature_sensor_handle_t ts = NULL;
    esp_err_t err = temperature_sensor_install(&cfg, &ts);
    if (err != ESP_OK) {
        return err;
    }
    if ((err = temperature_sensor_enable(ts)) != ESP_OK) {
        temperature_sensor_uninstall(ts);
        return err;
    }
    s_ts = ts;
    return ESP_OK;
}

esp_err_t chip_temp_read(float *celsius)
{
    if (celsius == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_ts == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    float sum = 0;
    for (int i = 0; i < SAMPLES; i++) {
        float t = 0;
        esp_err_t err = temperature_sensor_get_celsius(s_ts, &t);
        if (err != ESP_OK) {
            return err;
        }
        sum += t;
    }
    *celsius = sum / SAMPLES;
    return ESP_OK;
}

void chip_temp_deinit(void)
{
    if (s_ts != NULL) {
        temperature_sensor_disable(s_ts);
        temperature_sensor_uninstall(s_ts);
        s_ts = NULL;
    }
}
