#include "driver/pulse_cnt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "freqmeter.h"

#define HIGH_LIMIT 30000            /* 16 位计数器，上限必须 ≤ 32767 */

static pcnt_unit_handle_t s_unit;
static pcnt_channel_handle_t s_chan;

esp_err_t freqmeter_init(void)
{
    pcnt_unit_config_t ucfg = {
        .low_limit = -1,
        .high_limit = HIGH_LIMIT,
    };
    esp_err_t err = pcnt_new_unit(&ucfg, &s_unit);
    if (err != ESP_OK) {
        return err;
    }
    pcnt_glitch_filter_config_t filter = {.max_glitch_ns = 1000};
    pcnt_chan_config_t ccfg = {.edge_gpio_num = FF_PIN_PWM_IN, .level_gpio_num = -1};
    if ((err = pcnt_unit_set_glitch_filter(s_unit, &filter)) != ESP_OK ||
        (err = pcnt_new_channel(s_unit, &ccfg, &s_chan)) != ESP_OK ||
        (err = pcnt_channel_set_edge_action(s_chan, PCNT_CHANNEL_EDGE_ACTION_INCREASE,
                                            PCNT_CHANNEL_EDGE_ACTION_HOLD)) != ESP_OK ||
        (err = pcnt_unit_enable(s_unit)) != ESP_OK ||
        (err = pcnt_unit_clear_count(s_unit)) != ESP_OK ||
        (err = pcnt_unit_start(s_unit)) != ESP_OK) {
        freqmeter_deinit();
        return err;
    }
    return ESP_OK;
}

esp_err_t freqmeter_measure(uint32_t gate_ms, uint32_t *hz)
{
    if (hz == NULL || gate_ms < 10 || gate_ms > 1000 || s_unit == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    int count = 0;
    esp_err_t err = pcnt_unit_clear_count(s_unit);
    if (err != ESP_OK) {
        return err;
    }
    int64_t t0 = esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(gate_ms));
    err = pcnt_unit_get_count(s_unit, &count);
    int64_t t1 = esp_timer_get_time();
    if (err != ESP_OK) {
        return err;
    }
    int64_t elapsed_us = t1 - t0;
    if (elapsed_us <= 0) {
        return ESP_FAIL;
    }
    *hz = (uint32_t)(((int64_t)count * 1000000 + elapsed_us / 2) / elapsed_us);
    return ESP_OK;
}

void freqmeter_deinit(void)
{
    if (s_unit != NULL) {
        pcnt_unit_stop(s_unit);
        pcnt_unit_disable(s_unit);
    }
    if (s_chan != NULL) {
        pcnt_del_channel(s_chan);
        s_chan = NULL;
    }
    if (s_unit != NULL) {
        pcnt_del_unit(s_unit);
        s_unit = NULL;
    }
}
