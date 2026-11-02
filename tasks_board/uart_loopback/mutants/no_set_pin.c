#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "hw_config.h"
#include "link.h"

#define LINK_UART   UART_NUM_1
#define RX_BUF_SIZE 1024            /* 必须大于硬件 FIFO（128 字节） */

static bool s_on;

esp_err_t link_init(uint32_t baud)
{
    uart_config_t cfg = {
        .baud_rate = (int)baud, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(LINK_UART, RX_BUF_SIZE, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        return err;
    }
    if ((err = uart_param_config(LINK_UART, &cfg)) != ESP_OK) {
        uart_driver_delete(LINK_UART);
        return err;
    }
    uart_flush_input(LINK_UART);
    s_on = true;
    return ESP_OK;
}

int link_write(const uint8_t *data, size_t len)
{
    if (!s_on || (data == NULL && len > 0)) {
        return -1;
    }
    return uart_write_bytes(LINK_UART, data, len);
}

int link_read_line(char *buf, size_t cap, uint32_t timeout_ms)
{
    if (!s_on || buf == NULL || cap == 0) {
        return -1;
    }
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    size_t n = 0;
    for (;;) {
        int64_t left_ms = (deadline - esp_timer_get_time()) / 1000;
        if (left_ms <= 0) {
            return -1;
        }
        TickType_t wait = pdMS_TO_TICKS((uint32_t)left_ms);     /* 毫秒换成 tick（1 tick = 10 ms） */
        if (wait == 0) {
            wait = 1;
        }
        uint8_t c;
        if (uart_read_bytes(LINK_UART, &c, 1, wait) != 1) {
            continue;
        }
        if (c == '\n') {
            buf[n] = '\0';
            return (int)n;
        }
        if (n < cap - 1) {
            buf[n++] = (char)c;             /* 超出部分丢掉，直到换行符 */
        }
    }
}

void link_deinit(void)
{
    if (s_on) {
        uart_driver_delete(LINK_UART);
        s_on = false;
    }
}
