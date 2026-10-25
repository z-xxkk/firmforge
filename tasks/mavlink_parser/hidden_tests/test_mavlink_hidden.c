/* mavlink_parser 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "mavlink_parser.h"
#include "unity.h"

/* 真实消息定义（common.xml / minimal.xml）里的长度与 CRC_EXTRA */
static const mavlink_msg_info_t TABLE[] = {
    {0, 9, 50},          /* HEARTBEAT */
    {1, 31, 124},        /* SYS_STATUS */
    {30, 28, 39},        /* ATTITUDE */
    {76, 33, 152},       /* COMMAND_LONG */
    {300, 20, 77},       /* 消息号超过 255，只能用 v2 发 */
    {30, 5, 99},         /* 与 ATTITUDE 重复：应当用前一项 */
};

static mavlink_parser_t p;
static uint8_t fr[400];

void setUp(void)
{
    memset(&p, 0xA5, sizeof p);
    TEST_ASSERT_TRUE(mavlink_parser_init(&p, TABLE, sizeof TABLE / sizeof TABLE[0]));
}

void tearDown(void) {}

/* 独立实现：按位的反射 CRC-16（多项式 8408h，初值 FFFFh） */
static uint16_t crc_bits(const uint8_t *d, size_t n, uint16_t crc)
{
    for (size_t i = 0; i < n; i++) {
        crc ^= d[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc & 1u) ? (uint16_t)((crc >> 1) ^ 0x8408u) : (uint16_t)(crc >> 1);
        }
    }
    return crc;
}

static uint16_t frame_crc(const uint8_t *from_len, size_t n, uint8_t extra)
{
    return crc_bits(&extra, 1, crc_bits(from_len, n, 0xFFFFu));
}

static size_t v1(uint8_t seq, uint8_t sys, uint8_t comp, uint8_t msgid, const uint8_t *pl, uint8_t n, uint8_t extra)
{
    size_t k = 0;
    fr[k++] = 0xFE;
    fr[k++] = n;
    fr[k++] = seq;
    fr[k++] = sys;
    fr[k++] = comp;
    fr[k++] = msgid;
    memcpy(&fr[k], pl, n);
    k += n;
    uint16_t c = frame_crc(&fr[1], k - 1, extra);
    fr[k++] = (uint8_t)c;
    fr[k++] = (uint8_t)(c >> 8);
    return k;
}

static size_t v2(uint8_t flags, uint8_t seq, uint8_t sys, uint8_t comp, uint32_t msgid, const uint8_t *pl, uint8_t n,
                 uint8_t extra)
{
    size_t k = 0;
    fr[k++] = 0xFD;
    fr[k++] = n;
    fr[k++] = flags;
    fr[k++] = 0x00;
    fr[k++] = seq;
    fr[k++] = sys;
    fr[k++] = comp;
    fr[k++] = (uint8_t)msgid;
    fr[k++] = (uint8_t)(msgid >> 8);
    fr[k++] = (uint8_t)(msgid >> 16);
    memcpy(&fr[k], pl, n);
    k += n;
    uint16_t c = frame_crc(&fr[1], k - 1, extra);
    fr[k++] = (uint8_t)c;
    fr[k++] = (uint8_t)(c >> 8);
    if (flags & 0x01u) {
        for (int i = 0; i < 13; i++) {
            fr[k++] = (uint8_t)(0xFE - i);                   /* 签名里也会出现起始字节 */
        }
    }
    return k;
}

/* 喂入一段字节：最后一个字节之前都应是 NONE，返回最后一个字节的结果 */
static mavlink_result_t feed_frame(const uint8_t *d, size_t n)
{
    for (size_t i = 0; i + 1 < n; i++) {
        TEST_ASSERT_EQUAL_INT_MESSAGE(MAVLINK_NONE, mavlink_parser_feed(&p, d[i]), "帧没收齐就出了结果");
    }
    return mavlink_parser_feed(&p, d[n - 1]);
}

static const uint8_t HB_PAYLOAD[9] = {0x00, 0x00, 0x00, 0x00, 0x02, 0x03, 0x51, 0x04, 0x03};

static void test_reference_values(void)
{
    TEST_ASSERT_EQUAL_HEX16(0x6F91, crc_bits((const uint8_t *)"123456789", 9, 0xFFFFu));
}

static void test_v1_heartbeat_spec_example(void)
{
    static const uint8_t f[] = {0xFE, 0x09, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
                                0x02, 0x03, 0x51, 0x04, 0x03, 0x7D, 0xDD};
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(f, sizeof f));
    const mavlink_message_t *m = mavlink_parser_message(&p);
    TEST_ASSERT_NOT_NULL(m);
    TEST_ASSERT_EQUAL_UINT8(1, m->version);
    TEST_ASSERT_EQUAL_UINT8(0, m->seq);
    TEST_ASSERT_EQUAL_UINT8(1, m->sysid);
    TEST_ASSERT_EQUAL_UINT8(1, m->compid);
    TEST_ASSERT_EQUAL_UINT32(0, m->msgid);
    TEST_ASSERT_EQUAL_UINT8(9, m->len);
    TEST_ASSERT_FALSE(m->is_signed);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(HB_PAYLOAD, m->payload, 9);
}

static void test_v2_heartbeat_spec_example(void)
{
    static const uint8_t f[] = {0xFD, 0x09, 0x00, 0x00, 0x07, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
                                0x00, 0x00, 0x00, 0x02, 0x03, 0x51, 0x04, 0x03, 0xA4, 0xAC};
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(f, sizeof f));
    const mavlink_message_t *m = mavlink_parser_message(&p);
    TEST_ASSERT_EQUAL_UINT8(2, m->version);
    TEST_ASSERT_EQUAL_UINT8(7, m->seq);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(HB_PAYLOAD, m->payload, 9);
}

static void test_garbage_before_frame_skipped(void)
{
    static const uint8_t junk[] = {0x00, 0x55, 0xAA, 0x12, 0xFF};
    for (size_t i = 0; i < sizeof junk; i++) {
        TEST_ASSERT_EQUAL_INT(MAVLINK_NONE, mavlink_parser_feed(&p, junk[i]));
    }
    size_t n = v1(3, 9, 8, 0, HB_PAYLOAD, 9, 50);
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    TEST_ASSERT_EQUAL_UINT8(9, mavlink_parser_message(&p)->sysid);
}

static void test_bad_crc_keeps_last_message(void)
{
    size_t n = v1(0, 1, 1, 0, HB_PAYLOAD, 9, 50);
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    uint8_t pl[9];
    memcpy(pl, HB_PAYLOAD, 9);
    pl[4] = 0x77;
    n = v1(1, 1, 1, 0, pl, 9, 50);
    fr[n - 1] ^= 0x01u;
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_CRC, feed_frame(fr, n));
    TEST_ASSERT_EQUAL_UINT8(0x02, mavlink_parser_message(&p)->payload[4]);
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(1, st.received);
    TEST_ASSERT_EQUAL_UINT32(1, st.bad_crc);
}

static void test_crc_extra_is_required(void)
{
    size_t n = v1(0, 1, 1, 0, HB_PAYLOAD, 9, 0);              /* 发送方没加 CRC_EXTRA */
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_CRC, feed_frame(fr, n));
    n = v1(0, 1, 1, 30, (const uint8_t[28]){1, 2, 3}, 28, 39);
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));     /* 重复项用第一项（CRC_EXTRA 39） */
}

static void test_crc_low_byte_first(void)
{
    size_t n = v1(0, 1, 1, 0, HB_PAYLOAD, 9, 50);
    uint8_t t = fr[n - 2];
    fr[n - 2] = fr[n - 1];
    fr[n - 1] = t;
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_CRC, feed_frame(fr, n));
}

static void test_v2_truncated_payload_zero_filled(void)
{
    uint8_t full[28];
    for (int i = 0; i < 28; i++) {
        full[i] = (uint8_t)(0xC0 + i);
    }
    size_t n = v2(0, 0, 1, 1, 30, full, 28, 39);
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    n = v2(0, 1, 1, 1, 30, (const uint8_t[3]){0x11, 0x22, 0x33}, 3, 39);   /* 末尾 25 个 0 被截掉 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    const mavlink_message_t *m = mavlink_parser_message(&p);
    TEST_ASSERT_EQUAL_UINT8(28, m->len);
    TEST_ASSERT_EQUAL_HEX8(0x33, m->payload[2]);
    uint8_t zeros[25] = {0};
    TEST_ASSERT_EQUAL_UINT8_ARRAY(zeros, &m->payload[3], 25);
}

static void test_v2_signed_frame(void)
{
    size_t n = v2(0x01, 5, 1, 1, 0, HB_PAYLOAD, 9, 50);
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    TEST_ASSERT_TRUE(mavlink_parser_message(&p)->is_signed);
    n = v1(6, 1, 1, 0, HB_PAYLOAD, 9, 50);                    /* 签名之后的下一帧照常解析 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    TEST_ASSERT_FALSE(mavlink_parser_message(&p)->is_signed);
}

static void test_unknown_incompat_flag_dropped(void)
{
    size_t n = v2(0x02, 0, 1, 1, 0, HB_PAYLOAD, 9, 50);
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_FLAGS, feed_frame(fr, n));
    TEST_ASSERT_NULL(mavlink_parser_message(&p));
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(1, st.dropped);
}

static void test_unknown_message_dropped(void)
{
    size_t n = v1(0, 1, 1, 77, HB_PAYLOAD, 9, 50);
    TEST_ASSERT_EQUAL_INT(MAVLINK_UNKNOWN_MSG, feed_frame(fr, n));
    n = v2(0, 0, 1, 1, 300, (const uint8_t[20]){9}, 20, 77);   /* 24 位消息号 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    TEST_ASSERT_EQUAL_UINT32(300, mavlink_parser_message(&p)->msgid);
}

static void test_length_rules(void)
{
    size_t n = v1(0, 1, 1, 0, HB_PAYLOAD, 8, 50);             /* v1 长度必须固定 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_LEN, feed_frame(fr, n));
    uint8_t big[10] = {1};
    n = v2(0, 0, 1, 1, 0, big, 10, 50);                        /* v2 比完整长度长 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_LEN, feed_frame(fr, n));
    n = v2(0, 0, 1, 1, 0, big, 0, 50);                         /* v2 长度为 0 */
    TEST_ASSERT_EQUAL_INT(MAVLINK_BAD_LEN, feed_frame(fr, n));
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(3, st.dropped);
    TEST_ASSERT_EQUAL_UINT32(0, st.bad_crc);
}

static void test_resync_finds_frame_inside_bad_frame(void)
{
    /* 一帧坏校验的 COMMAND_LONG，负载里藏着一帧完整的 HEARTBEAT */
    uint8_t inner[17];
    size_t in = v1(42, 3, 4, 0, HB_PAYLOAD, 9, 50);
    memcpy(inner, fr, in);
    uint8_t pl[33] = {0};
    memcpy(&pl[8], inner, in);
    size_t n = v1(0, 1, 1, 76, pl, 33, 152);
    fr[n - 1] ^= 0x5Au;
    TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    const mavlink_message_t *m = mavlink_parser_message(&p);
    TEST_ASSERT_EQUAL_UINT8(42, m->seq);
    TEST_ASSERT_EQUAL_UINT8(3, m->sysid);
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(1, st.bad_crc);
    TEST_ASSERT_EQUAL_UINT32(1, st.received);
}

static void test_resync_after_stray_start_byte(void)
{
    TEST_ASSERT_EQUAL_INT(MAVLINK_NONE, mavlink_parser_feed(&p, 0xFE));   /* 线路上的杂散起始字节 */
    size_t n = v1(9, 1, 1, 0, HB_PAYLOAD, 9, 50);
    /* 杂散字节被当成帧头，长度字段是 FEh：要等到收齐才知道是坏帧，再从下一个字节重新找 */
    int ok = 0;
    for (size_t i = 0; i < n; i++) {
        ok += mavlink_parser_feed(&p, fr[i]) == MAVLINK_OK;
    }
    for (int i = 0; i < 300 && !ok; i++) {
        ok += mavlink_parser_feed(&p, 0x00) == MAVLINK_OK;
    }
    TEST_ASSERT_EQUAL_INT(1, ok);
    TEST_ASSERT_EQUAL_UINT8(9, mavlink_parser_message(&p)->seq);
}

static void test_sequence_loss_counting(void)
{
    static const uint8_t seqs[] = {250, 251, 254, 255, 0, 3};   /* 丢 2 + 0 + 0 + 2 = 4（跨过 255→0） */
    for (size_t i = 0; i < sizeof seqs; i++) {
        size_t n = v1(seqs[i], 1, 1, 0, HB_PAYLOAD, 9, 50);
        TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
        n = v2(0, (uint8_t)(100 + i), 2, 1, 0, HB_PAYLOAD, 9, 50);   /* 另一个来源，序号连续 */
        TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    }
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(4, st.lost);
    TEST_ASSERT_EQUAL_UINT32(12, st.received);
}

static void test_at_most_eight_sources_tracked(void)
{
    for (uint8_t s = 1; s <= 10; s++) {
        size_t n = v1(0, s, 1, 0, HB_PAYLOAD, 9, 50);
        TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    }
    for (uint8_t s = 1; s <= 10; s++) {
        size_t n = v1(10, s, 1, 0, HB_PAYLOAD, 9, 50);          /* 每个来源都跳了 9 个 */
        TEST_ASSERT_EQUAL_INT(MAVLINK_OK, feed_frame(fr, n));
    }
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(8u * 9u, st.lost);
}

static void test_many_frames_back_to_back(void)
{
    static uint8_t stream[60u * 50u];
    size_t total = 0;
    for (int i = 0; i < 50; i++) {
        size_t n = (i % 2) ? v2(0, (uint8_t)i, 1, 1, 0, HB_PAYLOAD, 9, 50) : v1((uint8_t)i, 1, 1, 0, HB_PAYLOAD, 9, 50);
        memcpy(&stream[total], fr, n);
        total += n;
    }
    int ok = 0;
    for (size_t i = 0; i < total; i++) {
        ok += mavlink_parser_feed(&p, stream[i]) == MAVLINK_OK;
    }
    TEST_ASSERT_EQUAL_INT(50, ok);
    mavlink_stats_t st;
    mavlink_parser_stats(&p, &st);
    TEST_ASSERT_EQUAL_UINT32(0, st.lost);
}

static void test_null_and_init(void)
{
    TEST_ASSERT_FALSE(mavlink_parser_init(NULL, TABLE, 1));
    TEST_ASSERT_FALSE(mavlink_parser_init(&p, NULL, 1));
    TEST_ASSERT_FALSE(mavlink_parser_init(&p, TABLE, 0));
    TEST_ASSERT_EQUAL_INT(MAVLINK_NONE, mavlink_parser_feed(NULL, 0xFE));
    TEST_ASSERT_NULL(mavlink_parser_message(NULL));
    mavlink_stats_t st;
    memset(&st, 0xFF, sizeof st);
    mavlink_parser_stats(NULL, &st);
    TEST_ASSERT_EQUAL_UINT32(0, st.received);
    TEST_ASSERT_EQUAL_UINT32(0, st.lost);
    mavlink_parser_stats(&p, NULL);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_reference_values);
    RUN_TEST(test_v1_heartbeat_spec_example);
    RUN_TEST(test_v2_heartbeat_spec_example);
    RUN_TEST(test_garbage_before_frame_skipped);
    RUN_TEST(test_bad_crc_keeps_last_message);
    RUN_TEST(test_crc_extra_is_required);
    RUN_TEST(test_crc_low_byte_first);
    RUN_TEST(test_v2_truncated_payload_zero_filled);
    RUN_TEST(test_v2_signed_frame);
    RUN_TEST(test_unknown_incompat_flag_dropped);
    RUN_TEST(test_unknown_message_dropped);
    RUN_TEST(test_length_rules);
    RUN_TEST(test_resync_finds_frame_inside_bad_frame);
    RUN_TEST(test_resync_after_stray_start_byte);
    RUN_TEST(test_sequence_loss_counting);
    RUN_TEST(test_at_most_eight_sources_tracked);
    RUN_TEST(test_many_frames_back_to_back);
    RUN_TEST(test_null_and_init);
    return UNITY_END();
}
