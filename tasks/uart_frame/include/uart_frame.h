#ifndef UART_FRAME_H
#define UART_FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UART_FRAME_SOF          0xAAu
#define UART_FRAME_MAX_PAYLOAD  64u
#define UART_FRAME_MAX_SIZE     (UART_FRAME_MAX_PAYLOAD + 4u)   /* 帧头 + 长度 + 数据 + 2 字节 CRC */

typedef enum {
    UART_FRAME_NONE = 0,
    UART_FRAME_OK,
    UART_FRAME_ERROR
} uart_frame_result_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t buf[UART_FRAME_MAX_SIZE];          /* 当前正在接收的帧（从帧头开始） */
    uint8_t len;                               /* buf 中已收到的字节数，0 表示在等帧头 */
    uint8_t payload[UART_FRAME_MAX_PAYLOAD];   /* 最近一帧好帧的数据 */
    uint8_t payload_len;
    bool has_frame;                            /* 是否收到过好帧 */
} uart_frame_rx_t;

void                uart_frame_init(uart_frame_rx_t *rx);
uart_frame_result_t uart_frame_feed(uart_frame_rx_t *rx, uint8_t byte);
uint8_t             uart_frame_payload(const uart_frame_rx_t *rx, const uint8_t **data);

#endif /* UART_FRAME_H */
