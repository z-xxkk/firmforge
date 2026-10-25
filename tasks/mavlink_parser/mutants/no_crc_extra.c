#include "mavlink_parser.h"

#define HDR_V1 6u
#define HDR_V2 10u

/* CRC-16/MCRF4XX，与 MAVLink C 参考实现 checksum.h 的 crc_accumulate 相同 */
static uint16_t crc_accumulate(uint8_t data, uint16_t crc)
{
    uint8_t tmp = (uint8_t)(data ^ (uint8_t)(crc & 0xFFu));
    tmp = (uint8_t)(tmp ^ (uint8_t)(tmp << 4));
    return (uint16_t)((crc >> 8) ^ ((uint16_t)tmp << 8) ^ ((uint16_t)tmp << 3) ^ (uint16_t)(tmp >> 4));
}

static const mavlink_msg_info_t *lookup(const mavlink_parser_t *p, uint32_t msgid)
{
    for (size_t i = 0; i < p->table_count; i++) {
        if (p->table[i].msgid == msgid) {
            return &p->table[i];
        }
    }
    return NULL;
}

bool mavlink_parser_init(mavlink_parser_t *p, const mavlink_msg_info_t *table, size_t count)
{
    if (p == NULL || table == NULL || count == 0u) {
        return false;
    }
    p->table = table;
    p->table_count = count;
    p->len = 0;
    p->has_msg = false;
    p->stats.received = 0;
    p->stats.bad_crc = 0;
    p->stats.dropped = 0;
    p->stats.lost = 0;
    p->source_count = 0;
    return true;
}

static void count_lost(mavlink_parser_t *p, uint8_t sysid, uint8_t compid, uint8_t seq)
{
    for (uint8_t i = 0; i < p->source_count; i++) {
        mavlink_source_t *s = &p->sources[i];
        if (s->sysid == sysid && s->compid == compid) {
            p->stats.lost += (uint8_t)(seq - s->last_seq - 1u);
            s->last_seq = seq;
            return;
        }
    }
    if (p->source_count < MAVLINK_MAX_SOURCES) {
        mavlink_source_t *s = &p->sources[p->source_count++];
        s->sysid = sysid;
        s->compid = compid;
        s->last_seq = seq;
    }
}

/* 当前缓冲区里的帧：还没收完返回 NONE，否则返回判定结果 */
static mavlink_result_t check_frame(mavlink_parser_t *p)
{
    const uint8_t *b = p->buf;
    bool v2 = b[0] == MAVLINK_STX_V2;
    uint16_t hdr = v2 ? HDR_V2 : HDR_V1;
    if (p->len < (v2 ? 3u : 2u)) {
        return MAVLINK_NONE;
    }
    uint8_t plen = b[1];
    uint16_t sig = (v2 && (b[2] & MAVLINK_IFLAG_SIGNED)) ? MAVLINK_SIGNATURE_LEN : 0u;
    uint16_t total = (uint16_t)(hdr + plen + 2u + sig);
    if (p->len < total) {
        return MAVLINK_NONE;
    }
    uint32_t msgid = v2 ? ((uint32_t)b[7] | ((uint32_t)b[8] << 8) | ((uint32_t)b[9] << 16)) : b[5];
    if (v2 && (b[2] & (uint8_t)~MAVLINK_IFLAG_SIGNED) != 0u) {
        p->stats.dropped++;
        return MAVLINK_BAD_FLAGS;
    }
    const mavlink_msg_info_t *info = lookup(p, msgid);
    if (info == NULL) {
        p->stats.dropped++;
        return MAVLINK_UNKNOWN_MSG;
    }
    if (v2 ? (plen == 0u || plen > info->len) : (plen != info->len)) {
        p->stats.dropped++;
        return MAVLINK_BAD_LEN;
    }
    uint16_t crc = 0xFFFFu;
    for (uint16_t i = 1; i < (uint16_t)(hdr + plen); i++) {
        crc = crc_accumulate(b[i], crc);
    }
    /* 变异：漏掉 CRC_EXTRA */
    if (b[hdr + plen] != (uint8_t)(crc & 0xFFu) || b[hdr + plen + 1u] != (uint8_t)(crc >> 8)) {
        p->stats.bad_crc++;
        return MAVLINK_BAD_CRC;
    }
    mavlink_message_t *m = &p->msg;
    m->version = v2 ? 2u : 1u;
    m->seq = v2 ? b[4] : b[2];
    m->sysid = v2 ? b[5] : b[3];
    m->compid = v2 ? b[6] : b[4];
    m->msgid = msgid;
    m->len = info->len;
    m->is_signed = sig != 0u;
    for (uint16_t i = 0; i < info->len; i++) {
        m->payload[i] = (i < plen) ? b[hdr + i] : 0u;      /* v2 截断的负载补 0 */
    }
    p->has_msg = true;
    p->stats.received++;
    count_lost(p, m->sysid, m->compid, m->seq);
    p->len = 0;
    return MAVLINK_OK;
}

static bool is_stx(uint8_t byte)
{
    return byte == MAVLINK_STX_V1 || byte == MAVLINK_STX_V2;
}

/* 坏帧：从它起始字节的下一个字节开始，在已收到的字节里重新找起始字节 */
static mavlink_result_t resync(mavlink_parser_t *p, mavlink_result_t first)
{
    for (;;) {
        uint16_t start = 1;
        while (start < p->len && !is_stx(p->buf[start])) {
            start++;
        }
        if (start >= p->len) {
            p->len = 0;
            return first;
        }
        for (uint16_t i = start; i < p->len; i++) {
            p->buf[i - start] = p->buf[i];
        }
        p->len = (uint16_t)(p->len - start);
        mavlink_result_t r = check_frame(p);
        if (r == MAVLINK_OK) {
            return MAVLINK_OK;              /* 交付好帧优先于报告错误 */
        }
        if (r == MAVLINK_NONE) {
            return first;
        }
    }
}

mavlink_result_t mavlink_parser_feed(mavlink_parser_t *p, uint8_t byte)
{
    if (p == NULL) {
        return MAVLINK_NONE;
    }
    if (p->len == 0u) {
        if (is_stx(byte)) {
            p->buf[0] = byte;
            p->len = 1;
        }
        return MAVLINK_NONE;
    }
    p->buf[p->len++] = byte;
    mavlink_result_t r = check_frame(p);
    if (r != MAVLINK_NONE && r != MAVLINK_OK) {
        return resync(p, r);
    }
    return r;
}

const mavlink_message_t *mavlink_parser_message(const mavlink_parser_t *p)
{
    return (p == NULL || !p->has_msg) ? NULL : &p->msg;
}

void mavlink_parser_stats(const mavlink_parser_t *p, mavlink_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    if (p == NULL) {
        out->received = 0;
        out->bad_crc = 0;
        out->dropped = 0;
        out->lost = 0;
        return;
    }
    *out = p->stats;
}
