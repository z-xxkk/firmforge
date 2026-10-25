#include "xmodem_rx.h"

enum { PH_HEADER, PH_BLK, PH_BLK_INV, PH_DATA, PH_CHECK };

static uint16_t crc16_xmodem(const uint8_t *data, size_t len)
{
    uint16_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (unsigned b = 0; b < 8u; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static uint8_t checksum8(const uint8_t *data, size_t len)
{
    uint8_t sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum = (uint8_t)(sum + data[i]);
    }
    return sum;
}

static xmodem_reply_t abort_with(xmodem_rx_t *x, xmodem_status_t st)
{
    x->status = (uint8_t)st;
    return XMODEM_REPLY_CAN;
}

static xmodem_reply_t error_nak(xmodem_rx_t *x)
{
    x->phase = PH_HEADER;
    x->errors++;
    if (x->errors >= XMODEM_MAX_ERRORS) {
        return abort_with(x, XMODEM_ERR_RETRIES);
    }
    return XMODEM_REPLY_NAK;
}

bool xmodem_rx_init(xmodem_rx_t *x, uint8_t *buf, size_t cap)
{
    if (x == NULL) {
        return false;
    }
    x->buf = buf;
    x->cap = cap;
    x->len = 0;
    x->phase = PH_HEADER;
    x->crc_mode = true;
    x->handshake = true;
    x->got_block = false;
    x->can_pending = false;
    x->c_sent = 0;
    x->errors = 0;
    x->expected = 1;
    x->pos = 0;
    x->size = 0;
    x->check = 0;
    x->check_pos = 0;
    if (buf == NULL || cap == 0u) {
        x->status = (uint8_t)XMODEM_ERR_PARAM;
        return false;
    }
    x->status = (uint8_t)XMODEM_RUNNING;
    return true;
}

xmodem_reply_t xmodem_rx_start(xmodem_rx_t *x)
{
    if (x == NULL || x->status != (uint8_t)XMODEM_RUNNING) {
        return XMODEM_REPLY_NONE;
    }
    x->c_sent = 1;
    return XMODEM_REPLY_C;
}

static xmodem_reply_t finish_block(xmodem_rx_t *x)
{
    bool ok = (uint8_t)(x->blk + x->blk_inv) == 0xFFu;
    if (ok) {
        if (x->crc_mode) {
            ok = crc16_xmodem(x->block, x->size) == x->check;
        } else {
            ok = checksum8(x->block, x->size) == (uint8_t)x->check;
        }
    }
    if (!ok) {
        return error_nak(x);
    }
    x->phase = PH_HEADER;
    if (x->blk == x->expected) {
        if (x->size > x->cap - x->len) {
            return abort_with(x, XMODEM_ERR_OVERFLOW);
        }
        for (uint16_t i = 0; i < x->size; i++) {
            x->buf[x->len + i] = x->block[i];
        }
        x->len += x->size;
        x->expected = (uint8_t)(x->expected + 1u);
        x->got_block = true;
        x->errors = 0;
        return XMODEM_REPLY_ACK;
    }
    if (x->got_block && x->blk == (uint8_t)(x->expected - 1u)) {
        return XMODEM_REPLY_NAK;          /* 变异：重复块回 NAK，发送方会一直重发 */
    }
    return abort_with(x, XMODEM_ERR_SYNC);
}

static xmodem_reply_t on_header(xmodem_rx_t *x, uint8_t byte)
{
    if (x->can_pending) {
        x->can_pending = false;
        if (byte == XMODEM_CAN) {
            x->status = (uint8_t)XMODEM_CANCELLED;
            return XMODEM_REPLY_NONE;
        }
    }
    switch (byte) {
    case XMODEM_SOH:
    case XMODEM_STX:
        x->size = (byte == XMODEM_SOH) ? 128u : 1024u;
        x->pos = 0;
        x->check = 0;
        x->check_pos = 0;
        x->handshake = false;
        x->phase = PH_BLK;
        return XMODEM_REPLY_NONE;
    case XMODEM_EOT:
        x->status = (uint8_t)XMODEM_DONE;
        return XMODEM_REPLY_ACK;
    case XMODEM_CAN:
        x->can_pending = true;
        return XMODEM_REPLY_NONE;
    default:
        return XMODEM_REPLY_NONE;           /* 线路上的杂波：等超时再 NAK */
    }
}

xmodem_reply_t xmodem_rx_feed(xmodem_rx_t *x, uint8_t byte)
{
    if (x == NULL || x->status != (uint8_t)XMODEM_RUNNING) {
        return XMODEM_REPLY_NONE;
    }
    switch (x->phase) {
    case PH_HEADER:
        return on_header(x, byte);
    case PH_BLK:
        x->blk = byte;
        x->phase = PH_BLK_INV;
        return XMODEM_REPLY_NONE;
    case PH_BLK_INV:
        x->blk_inv = byte;
        x->phase = PH_DATA;
        return XMODEM_REPLY_NONE;
    case PH_DATA:
        x->block[x->pos++] = byte;
        if (x->pos == x->size) {
            x->phase = PH_CHECK;
        }
        return XMODEM_REPLY_NONE;
    default:
        x->check = (uint16_t)((x->check << 8) | byte);    /* CRC 高字节在前 */
        x->check_pos++;
        if (x->check_pos == (x->crc_mode ? 2u : 1u)) {
            return finish_block(x);
        }
        return XMODEM_REPLY_NONE;
    }
}

xmodem_reply_t xmodem_rx_timeout(xmodem_rx_t *x)
{
    if (x == NULL || x->status != (uint8_t)XMODEM_RUNNING) {
        return XMODEM_REPLY_NONE;
    }
    x->can_pending = false;
    if (x->handshake && x->phase == PH_HEADER) {
        if (x->crc_mode && x->c_sent < XMODEM_C_TRIES) {
            x->c_sent++;
            return XMODEM_REPLY_C;
        }
        x->crc_mode = false;                  /* 发送方不支持 CRC：改用校验和 */
        if (x->c_sent == XMODEM_C_TRIES) {
            x->c_sent++;
            return XMODEM_REPLY_NAK;          /* 切换模式的这一次不计错误 */
        }
    }
    return error_nak(x);
}

xmodem_status_t xmodem_rx_status(const xmodem_rx_t *x)
{
    return (x == NULL) ? XMODEM_ERR_PARAM : (xmodem_status_t)x->status;
}

size_t xmodem_rx_length(const xmodem_rx_t *x)
{
    return (x == NULL) ? 0u : x->len;
}
