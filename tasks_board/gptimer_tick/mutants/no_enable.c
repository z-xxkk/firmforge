#include <stdatomic.h>
#include "driver/gptimer.h"
#include "esp_attr.h"
#include "tick.h"

#define RESOLUTION_HZ 1000000u

static gptimer_handle_t s_timer;
static atomic_uint_fast32_t s_count;

static bool IRAM_ATTR on_alarm(gptimer_handle_t timer, const gptimer_alarm_event_data_t *edata, void *ctx)
{
    (void)timer;
    (void)edata;
    (void)ctx;
    atomic_fetch_add_explicit(&s_count, 1, memory_order_relaxed);
    return false;               /* 没有唤醒更高优先级的任务 */
}

esp_err_t tick_start(uint32_t hz)
{
    if (hz < 1 || hz > 10000) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_timer != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    gptimer_config_t cfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP, .resolution_hz = RESOLUTION_HZ,
    };
    esp_err_t err = gptimer_new_timer(&cfg, &s_timer);
    if (err != ESP_OK) {
        s_timer = NULL;
        return err;
    }
    gptimer_event_callbacks_t cbs = {.on_alarm = on_alarm};
    gptimer_alarm_config_t alarm = {
        .alarm_count = RESOLUTION_HZ / hz, .reload_count = 0, .flags.auto_reload_on_alarm = true,
    };
    atomic_store(&s_count, 0);
    if ((err = gptimer_register_event_callbacks(s_timer, &cbs, NULL)) != ESP_OK ||
        (err = gptimer_set_alarm_action(s_timer, &alarm)) != ESP_OK ||
        (err = gptimer_start(s_timer)) != ESP_OK) {
        gptimer_del_timer(s_timer);
        s_timer = NULL;
        return err;
    }
    return ESP_OK;
}

uint32_t tick_count(void)
{
    return (uint32_t)atomic_load(&s_count);
}

esp_err_t tick_stop(void)
{
    if (s_timer == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    gptimer_stop(s_timer);
    gptimer_disable(s_timer);
    esp_err_t err = gptimer_del_timer(s_timer);
    s_timer = NULL;
    return err;
}
