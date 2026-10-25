#ifndef ISOTP_H
#define ISOTP_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ISOTP_CAN_DL     8u       /* 经典 CAN：每帧最多 8 字节 */
#define ISOTP_MAX_LEN    4095u    /* 12 位长度字段的上限 */
#define ISOTP_PAD        0xCCu    /* 发出的帧补满 8 字节时的填充值 */

#define ISOTP_FS_CTS     0u       /* 流控状态：继续发送 */
#define ISOTP_FS_WAIT    1u       /* 等待 */
#define ISOTP_FS_OVFLW   2u       /* 溢出 */

/* ---------------------------------------------------------------- 接收 */

typedef enum {
    ISOTP_RX_NONE = 0,        /* 没什么要做的 */
    ISOTP_RX_SEND_FC,         /* fc_out 里是要发给对方的流控帧 */
    ISOTP_RX_DONE,            /* 收完一条消息：数据在缓冲区里，长度见 isotp_rx_length */
    ISOTP_RX_ERR_SEQ,         /* 序号不对，放弃这次接收 */
    ISOTP_RX_ERR_FORMAT,      /* 不是最后一帧的连续帧不足 8 字节，放弃这次接收 */
    ISOTP_RX_ERR_OVERFLOW,    /* 消息比缓冲区大：fc_out 里是"溢出"流控帧，不接收 */
    ISOTP_RX_ERR_TIMEOUT      /* 等连续帧超时，放弃这次接收 */
} isotp_rx_result_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t *buf;
    size_t cap;
    size_t total;             /* 正在接收的消息总长度 */
    size_t idx;               /* 已收到的字节数 */
    size_t last_len;          /* 最近一条收完的消息长度 */
    uint8_t sn;               /* 期望的下一个序号 */
    uint8_t block_size;       /* 本端流控里的块大小 */
    uint8_t st_min;           /* 本端流控里的最小间隔 */
    uint8_t block_count;      /* 本块已收到的连续帧数 */
    bool receiving;
    unsigned interrupted;     /* 接收被新的单帧 / 首帧打断的次数 */
} isotp_rx_t;

bool              isotp_rx_init(isotp_rx_t *rx, uint8_t *buf, size_t cap, uint8_t block_size, uint8_t st_min);
isotp_rx_result_t isotp_rx_frame(isotp_rx_t *rx, const uint8_t *data, uint8_t dlc, uint8_t fc_out[ISOTP_CAN_DL]);
isotp_rx_result_t isotp_rx_timeout(isotp_rx_t *rx);
size_t            isotp_rx_length(const isotp_rx_t *rx);
unsigned          isotp_rx_interrupted(const isotp_rx_t *rx);

/* ---------------------------------------------------------------- 发送 */

typedef enum {
    ISOTP_TX_IDLE = 0,        /* 空闲 */
    ISOTP_TX_WAIT_FC,         /* 等对方的流控 */
    ISOTP_TX_SENDING,         /* 可以用 isotp_tx_next 取连续帧 */
    ISOTP_TX_DONE,            /* 最后一帧已经交给调用方 */
    ISOTP_TX_ERR_OVERFLOW,    /* 对方回"溢出" */
    ISOTP_TX_ERR_FC,          /* 流控帧不合法（状态不认识或不足 3 字节） */
    ISOTP_TX_ERR_TIMEOUT      /* 等流控超时 */
} isotp_tx_state_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    const uint8_t *data;      /* 调用方的数据，发送期间必须保持有效 */
    size_t len;
    size_t idx;               /* 已经发出的字节数 */
    uint8_t state;            /* isotp_tx_state_t */
    uint8_t sn;
    uint8_t block_size;       /* 第一帧"继续发送"流控里的块大小 */
    uint8_t block_left;       /* 本块还能发几帧 */
    uint32_t gap_us;          /* 连续帧之间的最小间隔（微秒） */
    bool got_params;
    bool first_in_block;
} isotp_tx_t;

void             isotp_tx_init(isotp_tx_t *tx);
bool             isotp_tx_start(isotp_tx_t *tx, const uint8_t *data, size_t len, uint8_t frame_out[ISOTP_CAN_DL]);
void             isotp_tx_fc(isotp_tx_t *tx, const uint8_t *data, uint8_t dlc);
bool             isotp_tx_next(isotp_tx_t *tx, uint8_t frame_out[ISOTP_CAN_DL], uint32_t *delay_us);
void             isotp_tx_timeout(isotp_tx_t *tx);
isotp_tx_state_t isotp_tx_state(const isotp_tx_t *tx);

#endif /* ISOTP_H */
