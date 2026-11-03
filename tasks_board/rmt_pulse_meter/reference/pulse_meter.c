#include <string.h>
#include "driver/rmt_rx.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "pulse_meter.h"

#define RES_HZ        10000000u     /* 10 MHz：每个计数 100 ns；持续时间只有 15 位 */
#define MEM_SYMBOLS   48            /* ESP32-C3 每个通道 48 个符号，必须是偶数 */
#define NEED_PERIODS  32
#define MAX_KEEP      (NEED_PERIODS + 16)
#define MIN_NS        1000          /* 毛刺阈值：换算成滤波计数不能超过 255 */
#define MAX_NS        3000000       /* 结束阈值：换算成计数不能超过 32767 */

static rmt_channel_handle_t s_rx;
static rmt_symbol_word_t s_raw[MEM_SYMBOLS * 2];
static rmt_symbol_word_t s_keep[MAX_KEEP];
static volatile size_t s_count;
static TaskHandle_t s_waiter;

static bool on_recv(rmt_channel_handle_t chan, const rmt_rx_done_event_data_t *edata, void *ctx)
{
    (void)chan;
    (void)ctx;
    BaseType_t woken = pdFALSE;
    size_t n = edata->num_symbols;
    for (size_t i = 0; i < n && s_count < MAX_KEEP; i++) {      /* 中断里只拷贝 */
        s_keep[s_count++] = edata->received_symbols[i];
    }
    if (s_count >= NEED_PERIODS + 1 && s_waiter != NULL) {
        vTaskNotifyGiveFromISR(s_waiter, &woken);
        s_waiter = NULL;
    }
    return woken == pdTRUE;
}

esp_err_t pulse_meter_init(void)
{
    rmt_rx_channel_config_t cfg = {
        .gpio_num = FF_PIN_PWM_IN, .clk_src = RMT_CLK_SRC_DEFAULT, .resolution_hz = RES_HZ,
        .mem_block_symbols = MEM_SYMBOLS,
    };
    esp_err_t err = rmt_new_rx_channel(&cfg, &s_rx);
    if (err != ESP_OK) {
        s_rx = NULL;
        return err;
    }
    rmt_rx_event_callbacks_t cbs = {.on_recv_done = on_recv};
    if ((err = rmt_rx_register_event_callbacks(s_rx, &cbs, NULL)) != ESP_OK || (err = rmt_enable(s_rx)) != ESP_OK) {
        rmt_del_channel(s_rx);
        s_rx = NULL;
    }
    return err;
}

esp_err_t pulse_meter_measure(uint32_t timeout_ms, pulse_stat_t *out)
{
    if (out == NULL || timeout_ms == 0 || s_rx == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    rmt_receive_config_t rc = {
        .signal_range_min_ns = MIN_NS, .signal_range_max_ns = MAX_NS,
        .flags.en_partial_rx = 1,       /* 连续方波不会自己结束，必须分段接收 */
    };
    s_count = 0;
    s_waiter = xTaskGetCurrentTaskHandle();
    ulTaskNotifyTake(pdTRUE, 0);
    esp_err_t err = rmt_receive(s_rx, s_raw, sizeof s_raw, &rc);
    if (err != ESP_OK) {
        s_waiter = NULL;
        return err;
    }
    bool got = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(timeout_ms)) > 0;
    s_waiter = NULL;
    rmt_disable(s_rx);                  /* 停止接收，再重新启用，下次测量从头开始 */
    rmt_enable(s_rx);
    if (!got || s_count < NEED_PERIODS + 1) {
        return ESP_ERR_TIMEOUT;
    }
    uint64_t total = 0, high = 0;
    uint32_t periods = 0;
    for (size_t i = 1; i < s_count; i++) {          /* 丢掉第一个（可能不完整的）符号 */
        rmt_symbol_word_t s = s_keep[i];
        if (s.duration0 == 0 || s.duration1 == 0) {
            continue;                               /* 结束标记或不完整的符号 */
        }
        total += s.duration0 + s.duration1;
        high += s.level0 ? s.duration0 : s.duration1;
        periods++;
    }
    if (periods < NEED_PERIODS || total == 0) {
        return ESP_ERR_TIMEOUT;
    }
    out->hz = (uint32_t)(((uint64_t)RES_HZ * periods + total / 2) / total);
    out->duty_permille = (uint32_t)((high * 1000 + total / 2) / total);
    out->periods = periods;
    return ESP_OK;
}

void pulse_meter_deinit(void)
{
    if (s_rx != NULL) {
        rmt_disable(s_rx);
        rmt_del_channel(s_rx);
        s_rx = NULL;
    }
}
