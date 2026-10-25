#ifndef XMODEM_RX_H
#define XMODEM_RX_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define XMODEM_SOH  0x01u
#define XMODEM_STX  0x02u
#define XMODEM_EOT  0x04u
#define XMODEM_ACK  0x06u
#define XMODEM_NAK  0x15u
#define XMODEM_CAN  0x18u
#define XMODEM_C    0x43u

#define XMODEM_MAX_ERRORS  10u   /* 连续错误达到这个次数就中止 */
#define XMODEM_C_TRIES      4u   /* 握手时一共发几次 C */

/* 模块要求调用方回复什么 */
typedef enum {
    XMODEM_REPLY_NONE = 0,   /* 不回复 */
    XMODEM_REPLY_ACK,        /* 发 06h */
    XMODEM_REPLY_NAK,        /* 发 15h */
    XMODEM_REPLY_C,          /* 发 43h */
    XMODEM_REPLY_CAN         /* 连发 2 个 18h */
} xmodem_reply_t;

typedef enum {
    XMODEM_RUNNING = 0,      /* 接收进行中 */
    XMODEM_DONE,             /* 收到 EOT，接收完成 */
    XMODEM_CANCELLED,        /* 对方连发两个 CAN 取消 */
    XMODEM_ERR_SYNC,         /* 块号失步，已中止 */
    XMODEM_ERR_RETRIES,      /* 连续错误太多，已中止 */
    XMODEM_ERR_OVERFLOW,     /* 缓冲区放不下，已中止 */
    XMODEM_ERR_PARAM         /* 初始化参数不合法 */
} xmodem_status_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t *buf;            /* 调用方提供的接收缓冲区 */
    size_t cap;
    size_t len;              /* 已收到的字节数 */
    uint8_t status;          /* xmodem_status_t */
    uint8_t phase;           /* 当前在等什么 */
    bool crc_mode;           /* true：CRC-16；false：1 字节校验和 */
    bool handshake;          /* 还没收到第一个块头 */
    bool got_block;          /* 已经收到过有效块 */
    bool can_pending;        /* 刚收到一个 CAN */
    uint8_t c_sent;          /* 已经发了几次 C */
    uint8_t errors;          /* 连续错误次数 */
    uint8_t expected;        /* 期望的块号 */
    uint8_t blk;
    uint8_t blk_inv;
    uint16_t size;           /* 本块数据长度：128 或 1024 */
    uint16_t pos;            /* 本块已收到的数据字节数 */
    uint16_t check;          /* 收到的校验值 */
    uint8_t check_pos;
    uint8_t block[1024];     /* 本块数据，校验通过后才写进 buf */
} xmodem_rx_t;

bool            xmodem_rx_init(xmodem_rx_t *x, uint8_t *buf, size_t cap);
xmodem_reply_t  xmodem_rx_start(xmodem_rx_t *x);
xmodem_reply_t  xmodem_rx_feed(xmodem_rx_t *x, uint8_t byte);
xmodem_reply_t  xmodem_rx_timeout(xmodem_rx_t *x);
xmodem_status_t xmodem_rx_status(const xmodem_rx_t *x);
size_t          xmodem_rx_length(const xmodem_rx_t *x);

#endif /* XMODEM_RX_H */
