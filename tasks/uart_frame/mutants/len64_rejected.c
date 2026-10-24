#include "uart_frame.h"

static uint16_t crc16_modbus(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0u; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if ((crc & 1u) != 0u) {
                crc = (uint16_t)((crc >> 1) ^ 0xA001u);
            } else {
                crc = (uint16_t)(crc >> 1);
            }
        }
    }
    return crc;
}

void uart_frame_init(uart_frame_rx_t *rx)
{
    if (rx == NULL) {
        return;
    }
    rx->len = 0u;
    rx->payload_len = 0u;
    rx->has_frame = false;
}

/* 检查缓冲区里当前这一帧：还没收完、已是好帧、还是坏帧 */
static uart_frame_result_t check_frame(uart_frame_rx_t *rx)
{
    uint8_t n;
    uint16_t crc;
    size_t total;

    if (rx->len < 2u) {
        return UART_FRAME_NONE;
    }
    n = rx->buf[1];
    if (n >= UART_FRAME_MAX_PAYLOAD) {   /* 变异：长度 64 被误判为太长 */
        return UART_FRAME_ERROR;
    }
    total = (size_t)n + 4u;
    if (rx->len < total) {
        return UART_FRAME_NONE;
    }
    crc = crc16_modbus(&rx->buf[1], (size_t)n + 1u);
    if (rx->buf[total - 2u] != (uint8_t)(crc & 0xFFu) || rx->buf[total - 1u] != (uint8_t)(crc >> 8)) {
        return UART_FRAME_ERROR;
    }
    for (uint8_t i = 0u; i < n; i++) {
        rx->payload[i] = rx->buf[2u + i];
    }
    rx->payload_len = n;
    rx->has_frame = true;
    rx->len = 0u;
    return UART_FRAME_OK;
}

/* 坏帧：丢掉帧头，在剩下的字节里找下一个 0xAA，直到不再是坏帧 */
static uart_frame_result_t resync(uart_frame_rx_t *rx)
{
    for (;;) {
        uint8_t start = 1u;
        uart_frame_result_t r;

        while (start < rx->len && rx->buf[start] != UART_FRAME_SOF) {
            start++;
        }
        if (start >= rx->len) {
            rx->len = 0u;
            return UART_FRAME_ERROR;
        }
        for (uint8_t i = start; i < rx->len; i++) {
            rx->buf[i - start] = rx->buf[i];
        }
        rx->len = (uint8_t)(rx->len - start);
        r = check_frame(rx);
        if (r == UART_FRAME_OK) {
            return UART_FRAME_OK;
        }
        if (r == UART_FRAME_NONE) {
            return UART_FRAME_ERROR;
        }
    }
}

uart_frame_result_t uart_frame_feed(uart_frame_rx_t *rx, uint8_t byte)
{
    uart_frame_result_t r;

    if (rx == NULL) {
        return UART_FRAME_NONE;
    }
    if (rx->len == 0u) {
        if (byte == UART_FRAME_SOF) {
            rx->buf[0] = byte;
            rx->len = 1u;
        }
        return UART_FRAME_NONE;
    }
    rx->buf[rx->len] = byte;
    rx->len++;
    r = check_frame(rx);
    if (r == UART_FRAME_ERROR) {
        return resync(rx);
    }
    return r;
}

uint8_t uart_frame_payload(const uart_frame_rx_t *rx, const uint8_t **data)
{
    if (rx == NULL || !rx->has_frame) {
        if (data != NULL) {
            *data = NULL;
        }
        return 0u;
    }
    if (data != NULL) {
        *data = rx->payload;
    }
    return rx->payload_len;
}
