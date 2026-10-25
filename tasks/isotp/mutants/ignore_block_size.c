#include "isotp.h"

#define PCI_SF 0x0u
#define PCI_FF 0x1u
#define PCI_CF 0x2u
#define PCI_FC 0x3u

static void pad(uint8_t frame[ISOTP_CAN_DL], unsigned from)
{
    for (unsigned i = from; i < ISOTP_CAN_DL; i++) {
        frame[i] = ISOTP_PAD;
    }
}

static void make_fc(uint8_t out[ISOTP_CAN_DL], uint8_t fs, uint8_t bs, uint8_t st)
{
    out[0] = (uint8_t)((PCI_FC << 4) | fs);
    out[1] = bs;
    out[2] = st;
    pad(out, 3);
}

/* ---------------------------------------------------------------- 接收 */

bool isotp_rx_init(isotp_rx_t *rx, uint8_t *buf, size_t cap, uint8_t block_size, uint8_t st_min)
{
    if (rx == NULL || buf == NULL || cap == 0u) {
        return false;
    }
    rx->buf = buf;
    rx->cap = cap;
    rx->total = 0;
    rx->idx = 0;
    rx->last_len = 0;
    rx->sn = 0;
    rx->block_size = block_size;
    rx->st_min = st_min;
    rx->block_count = 0;
    rx->receiving = false;
    rx->interrupted = 0;
    return true;
}

static isotp_rx_result_t rx_single(isotp_rx_t *rx, const uint8_t *d, uint8_t dlc)
{
    unsigned n = d[0] & 0x0Fu;
    if (n == 0u || n > (unsigned)dlc - 1u || n > rx->cap) {
        return ISOTP_RX_NONE;
    }
    if (rx->receiving) {
        rx->interrupted++;
        rx->receiving = false;
    }
    for (unsigned i = 0; i < n; i++) {
        rx->buf[i] = d[1 + i];
    }
    rx->last_len = n;
    return ISOTP_RX_DONE;
}

static isotp_rx_result_t rx_first(isotp_rx_t *rx, const uint8_t *d, uint8_t dlc, uint8_t fc_out[ISOTP_CAN_DL])
{
    size_t total = ((size_t)(d[0] & 0x0Fu) << 8) | d[1];
    if (dlc != ISOTP_CAN_DL || total < 8u) {
        return ISOTP_RX_NONE;               /* 长度 0（32 位扩展）也在这里被忽略 */
    }
    if (rx->receiving) {
        rx->interrupted++;
        rx->receiving = false;
    }
    if (total > rx->cap) {
        make_fc(fc_out, ISOTP_FS_OVFLW, 0, 0);
        return ISOTP_RX_ERR_OVERFLOW;
    }
    for (unsigned i = 0; i < 6u; i++) {
        rx->buf[i] = d[2 + i];
    }
    rx->total = total;
    rx->idx = 6;
    rx->sn = 1;
    rx->block_count = 0;
    rx->receiving = true;
    make_fc(fc_out, ISOTP_FS_CTS, rx->block_size, rx->st_min);
    return ISOTP_RX_SEND_FC;
}

static isotp_rx_result_t rx_consecutive(isotp_rx_t *rx, const uint8_t *d, uint8_t dlc, uint8_t fc_out[ISOTP_CAN_DL])
{
    size_t left = rx->total - rx->idx;
    if (!rx->receiving) {
        return ISOTP_RX_NONE;
    }
    if ((d[0] & 0x0Fu) != rx->sn) {
        rx->receiving = false;
        return ISOTP_RX_ERR_SEQ;
    }
    if (left > 7u && dlc < ISOTP_CAN_DL) {
        rx->receiving = false;
        return ISOTP_RX_ERR_FORMAT;
    }
    size_t n = left < 7u ? left : 7u;
    if (n > (size_t)dlc - 1u) {
        rx->receiving = false;
        return ISOTP_RX_ERR_FORMAT;
    }
    for (size_t i = 0; i < n; i++) {
        rx->buf[rx->idx + i] = d[1 + i];
    }
    rx->idx += n;
    rx->sn = (uint8_t)((rx->sn + 1u) & 0x0Fu);
    if (rx->idx == rx->total) {
        rx->receiving = false;
        rx->last_len = rx->total;
        return ISOTP_RX_DONE;
    }
    if (rx->block_size != 0u && ++rx->block_count >= rx->block_size) {
        rx->block_count = 0;
        make_fc(fc_out, ISOTP_FS_CTS, rx->block_size, rx->st_min);
        return ISOTP_RX_SEND_FC;
    }
    return ISOTP_RX_NONE;
}

isotp_rx_result_t isotp_rx_frame(isotp_rx_t *rx, const uint8_t *data, uint8_t dlc, uint8_t fc_out[ISOTP_CAN_DL])
{
    if (rx == NULL || data == NULL || fc_out == NULL || dlc == 0u || dlc > ISOTP_CAN_DL) {
        return ISOTP_RX_NONE;
    }
    switch (data[0] >> 4) {
    case PCI_SF:
        return rx_single(rx, data, dlc);
    case PCI_FF:
        return rx_first(rx, data, dlc, fc_out);
    case PCI_CF:
        return rx_consecutive(rx, data, dlc, fc_out);
    default:
        return ISOTP_RX_NONE;                 /* 流控和不认识的帧类型：接收端忽略 */
    }
}

isotp_rx_result_t isotp_rx_timeout(isotp_rx_t *rx)
{
    if (rx == NULL || !rx->receiving) {
        return ISOTP_RX_NONE;
    }
    rx->receiving = false;
    return ISOTP_RX_ERR_TIMEOUT;
}

size_t isotp_rx_length(const isotp_rx_t *rx)
{
    return rx == NULL ? 0u : rx->last_len;
}

unsigned isotp_rx_interrupted(const isotp_rx_t *rx)
{
    return rx == NULL ? 0u : rx->interrupted;
}

/* ---------------------------------------------------------------- 发送 */

void isotp_tx_init(isotp_tx_t *tx)
{
    if (tx == NULL) {
        return;
    }
    tx->data = NULL;
    tx->len = 0;
    tx->idx = 0;
    tx->state = (uint8_t)ISOTP_TX_IDLE;
    tx->sn = 0;
    tx->block_size = 0;
    tx->block_left = 0;
    tx->gap_us = 0;
    tx->got_params = false;
    tx->first_in_block = false;
}

static uint32_t stmin_us(uint8_t st)
{
    if (st <= 0x7Fu) {
        return (uint32_t)st * 1000u;
    }
    if (st >= 0xF1u && st <= 0xF9u) {
        return (uint32_t)(st - 0xF0u) * 100u;
    }
    return 127000u;                           /* 保留值按 7Fh 处理 */
}

bool isotp_tx_start(isotp_tx_t *tx, const uint8_t *data, size_t len, uint8_t frame_out[ISOTP_CAN_DL])
{
    if (tx == NULL || data == NULL || frame_out == NULL || len == 0u || len > ISOTP_MAX_LEN) {
        return false;
    }
    if (tx->state == (uint8_t)ISOTP_TX_WAIT_FC || tx->state == (uint8_t)ISOTP_TX_SENDING) {
        return false;
    }
    tx->data = data;
    tx->len = len;
    tx->got_params = false;
    if (len <= 7u) {
        frame_out[0] = (uint8_t)((PCI_SF << 4) | len);
        for (unsigned i = 0; i < len; i++) {
            frame_out[1 + i] = data[i];
        }
        pad(frame_out, 1u + (unsigned)len);
        tx->idx = len;
        tx->state = (uint8_t)ISOTP_TX_DONE;
        return true;
    }
    frame_out[0] = (uint8_t)((PCI_FF << 4) | (uint8_t)(len >> 8));
    frame_out[1] = (uint8_t)len;
    for (unsigned i = 0; i < 6u; i++) {
        frame_out[2 + i] = data[i];
    }
    tx->idx = 6;
    tx->sn = 1;
    tx->state = (uint8_t)ISOTP_TX_WAIT_FC;
    return true;
}

void isotp_tx_fc(isotp_tx_t *tx, const uint8_t *data, uint8_t dlc)
{
    if (tx == NULL || data == NULL || tx->state != (uint8_t)ISOTP_TX_WAIT_FC) {
        return;
    }
    if ((data[0] >> 4) != PCI_FC) {
        return;                               /* 不是流控帧：发送端不管 */
    }
    if (dlc < 3u) {
        tx->state = (uint8_t)ISOTP_TX_ERR_FC;
        return;
    }
    switch (data[0] & 0x0Fu) {
    case ISOTP_FS_CTS:
        if (!tx->got_params) {
            tx->block_size = data[1];
            tx->gap_us = stmin_us(data[2]);
            tx->got_params = true;
        }
        tx->block_left = tx->block_size;
        tx->first_in_block = true;
        tx->state = (uint8_t)ISOTP_TX_SENDING;
        break;
    case ISOTP_FS_WAIT:
        break;
    case ISOTP_FS_OVFLW:
        tx->state = (uint8_t)ISOTP_TX_ERR_OVERFLOW;
        break;
    default:
        tx->state = (uint8_t)ISOTP_TX_ERR_FC;
        break;
    }
}

bool isotp_tx_next(isotp_tx_t *tx, uint8_t frame_out[ISOTP_CAN_DL], uint32_t *delay_us)
{
    if (tx == NULL || frame_out == NULL || delay_us == NULL || tx->state != (uint8_t)ISOTP_TX_SENDING) {
        return false;
    }
    *delay_us = tx->first_in_block ? 0u : tx->gap_us;
    tx->first_in_block = false;
    size_t left = tx->len - tx->idx;
    unsigned n = left < 7u ? (unsigned)left : 7u;
    frame_out[0] = (uint8_t)((PCI_CF << 4) | tx->sn);
    for (unsigned i = 0; i < n; i++) {
        frame_out[1 + i] = tx->data[tx->idx + i];
    }
    pad(frame_out, 1u + n);
    tx->idx += n;
    tx->sn = (uint8_t)((tx->sn + 1u) & 0x0Fu);
    if (tx->idx == tx->len) {
        tx->state = (uint8_t)ISOTP_TX_DONE;
    }   /* 变异：不按块大小停下等流控 */
    return true;
}

void isotp_tx_timeout(isotp_tx_t *tx)
{
    if (tx != NULL && tx->state == (uint8_t)ISOTP_TX_WAIT_FC) {
        tx->state = (uint8_t)ISOTP_TX_ERR_TIMEOUT;
    }
}

isotp_tx_state_t isotp_tx_state(const isotp_tx_t *tx)
{
    return tx == NULL ? ISOTP_TX_IDLE : (isotp_tx_state_t)tx->state;
}
