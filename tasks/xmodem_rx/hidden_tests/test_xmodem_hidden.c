/* xmodem_rx 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "xmodem_rx.h"

static xmodem_rx_t rx;
static uint8_t store[300u * 128u];
static uint8_t frame[1100];

void setUp(void)
{
    memset(&rx, 0xA5, sizeof rx);
    memset(store, 0, sizeof store);
    TEST_ASSERT_TRUE(xmodem_rx_init(&rx, store, sizeof store));
}

void tearDown(void) {}

/* 独立实现的 CRC-16/XMODEM（逐位，"123456789" → 31C3h） */
static uint16_t crc_ref(const uint8_t *d, size_t n)
{
    uint32_t crc = 0;
    for (size_t i = 0; i < n; i++) {
        for (int b = 7; b >= 0; b--) {
            uint32_t in = (d[i] >> b) & 1u;
            uint32_t top = (crc >> 15) & 1u;
            crc = (crc << 1) & 0xFFFFu;
            if (top ^ in) {
                crc ^= 0x1021u;
            }
        }
    }
    return (uint16_t)crc;
}

static void fill(uint8_t *data, size_t n, uint8_t seed)
{
    for (size_t i = 0; i < n; i++) {
        data[i] = (uint8_t)(seed * 31u + i * 7u + 1u);
    }
}

/* 造一个块；返回帧长 */
static size_t make_block(uint8_t blk, size_t size, bool crc, uint8_t seed)
{
    size_t n = 0;
    frame[n++] = (size == 1024u) ? XMODEM_STX : XMODEM_SOH;
    frame[n++] = blk;
    frame[n++] = (uint8_t)(255u - blk);
    fill(&frame[n], size, seed);
    n += size;
    if (crc) {
        uint16_t c = crc_ref(&frame[3], size);
        frame[n++] = (uint8_t)(c >> 8);
        frame[n++] = (uint8_t)c;
    } else {
        uint8_t s = 0;
        for (size_t i = 0; i < size; i++) {
            s = (uint8_t)(s + frame[3 + i]);
        }
        frame[n++] = s;
    }
    return n;
}

/* 逐字节喂入：除最后一个字节外都应回复 NONE，返回最后一个字节的回复 */
static xmodem_reply_t send(size_t n)
{
    for (size_t i = 0; i + 1 < n; i++) {
        TEST_ASSERT_EQUAL_INT_MESSAGE(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, frame[i]), "块中间就回复了");
    }
    return xmodem_rx_feed(&rx, frame[n - 1]);
}

static xmodem_reply_t send_block(uint8_t blk, size_t size, bool crc, uint8_t seed)
{
    return send(make_block(blk, size, crc, seed));
}

static void expect_data(size_t offset, size_t size, uint8_t seed)
{
    uint8_t want[1024];
    fill(want, size, seed);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(want, &store[offset], size);
}

static void test_crc_reference_value(void)
{
    TEST_ASSERT_EQUAL_HEX16(0x31C3, crc_ref((const uint8_t *)"123456789", 9));
}

static void test_spec_example_flow_crc(void)
{
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_C, xmodem_rx_start(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    size_t n = make_block(2, 128, true, 2);
    frame[n - 1] ^= 0x01u;                                  /* 线路干扰：CRC 坏了 */
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, send(n));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));   /* 重发：ACK 丢了 */
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(3, 128, true, 3));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_DONE, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(384, xmodem_rx_length(&rx));
    expect_data(0, 128, 1);
    expect_data(128, 128, 2);
    expect_data(256, 128, 3);
}

static void test_crc_is_high_byte_first(void)
{
    xmodem_rx_start(&rx);
    size_t n = make_block(1, 128, true, 9);
    uint8_t t = frame[n - 2];
    frame[n - 2] = frame[n - 1];
    frame[n - 1] = t;                                        /* 低字节在前：应判为校验错 */
    if (frame[n - 1] != frame[n - 2]) {
        TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, send(n));
        TEST_ASSERT_EQUAL_UINT(0, xmodem_rx_length(&rx));
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 9));
}

static void test_fallback_to_checksum_after_four_c(void)
{
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_C, xmodem_rx_start(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_C, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_C, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_C, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, false, 4));
    size_t n = make_block(2, 128, false, 5);
    frame[10] ^= 0x40u;                                      /* 数据被干扰 */
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, send(n));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, false, 5));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_UINT(256, xmodem_rx_length(&rx));
    expect_data(128, 128, 5);
}

static void test_handshake_timeouts_not_counted(void)
{
    xmodem_rx_start(&rx);
    for (int i = 0; i < 3; i++) {
        xmodem_rx_timeout(&rx);
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));          /* 切到校验和模式 */
    for (int i = 0; i < 9; i++) {                                             /* 之后的超时才计错误 */
        TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_RUNNING, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_CAN, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_RETRIES, xmodem_rx_status(&rx));
}

static void test_bad_complement_nak(void)
{
    xmodem_rx_start(&rx);
    size_t n = make_block(1, 128, true, 6);
    frame[2] = 0xFF;                                         /* 反码不对 */
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, send(n));
    TEST_ASSERT_EQUAL_UINT(0, xmodem_rx_length(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 6));
}

static void test_duplicate_block_acked_and_discarded(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 7));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 8));     /* 重复块（内容不同也丢） */
    TEST_ASSERT_EQUAL_UINT(128, xmodem_rx_length(&rx));
    expect_data(0, 128, 7);
    TEST_ASSERT_EQUAL_INT(XMODEM_RUNNING, xmodem_rx_status(&rx));
}

static void test_sync_loss_cancels(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_CAN, send_block(3, 128, true, 3));     /* 跳过了第 2 块 */
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_SYNC, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_SYNC, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(128, xmodem_rx_length(&rx));
}

static void test_block_zero_first_is_sync_loss(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_CAN, send_block(0, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_SYNC, xmodem_rx_status(&rx));
}

static void test_block_number_wraps_to_zero(void)
{
    xmodem_rx_start(&rx);
    for (unsigned i = 1; i <= 257u; i++) {                    /* 块号 1…255、0、1 */
        uint8_t blk = (uint8_t)i;
        TEST_ASSERT_EQUAL_INT_MESSAGE(XMODEM_REPLY_ACK, send_block(blk, 128, true, (uint8_t)i), "回绕处出错");
    }
    TEST_ASSERT_EQUAL_UINT(257u * 128u, xmodem_rx_length(&rx));
    expect_data(255u * 128u, 128, (uint8_t)256u);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));     /* 257 号块（01h）的重复 */
    TEST_ASSERT_EQUAL_UINT(257u * 128u, xmodem_rx_length(&rx));
}

static void test_mixed_1k_and_128_blocks(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 1024, true, 11));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 12));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(3, 1024, true, 13));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_UINT(1024u + 128u + 1024u, xmodem_rx_length(&rx));
    expect_data(0, 1024, 11);
    expect_data(1024, 128, 12);
    expect_data(1152, 1024, 13);
}

static void test_two_cans_cancel_one_can_ignored(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_CAN));  /* 单个 CAN：干扰 */
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));
    TEST_ASSERT_EQUAL_INT(XMODEM_RUNNING, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_CAN));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_CAN));
    TEST_ASSERT_EQUAL_INT(XMODEM_CANCELLED, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(256, xmodem_rx_length(&rx));
}

static void test_can_and_soh_inside_data_are_data(void)
{
    xmodem_rx_start(&rx);
    size_t n = make_block(1, 128, true, 0);
    for (size_t i = 3; i < 3 + 128; i++) {
        frame[i] = (i % 2u) ? XMODEM_CAN : XMODEM_SOH;
    }
    uint16_t c = crc_ref(&frame[3], 128);
    frame[n - 2] = (uint8_t)(c >> 8);
    frame[n - 1] = (uint8_t)c;
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send(n));
    TEST_ASSERT_EQUAL_HEX8(XMODEM_CAN, store[0]);
    TEST_ASSERT_EQUAL_HEX8(XMODEM_SOH, store[1]);
}

static void test_garbage_between_blocks_ignored(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, 0x00));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, 0x7F));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, 0x55));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));
}

static void test_timeout_inside_block_discards_it(void)
{
    xmodem_rx_start(&rx);
    size_t n = make_block(1, 128, true, 1);
    for (size_t i = 0; i < 60; i++) {
        xmodem_rx_feed(&rx, frame[i]);
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send(n));          /* 整块重发能正常收下 */
    TEST_ASSERT_EQUAL_UINT(128, xmodem_rx_length(&rx));
    expect_data(0, 128, 1);
}

static void test_ten_consecutive_errors_abort(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    for (int i = 0; i < 9; i++) {
        size_t n = make_block(2, 128, true, 2);
        frame[n - 1] ^= 0xFFu;
        TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, send(n));
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_CAN, xmodem_rx_timeout(&rx));        /* 第 10 次错误 */
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_RETRIES, xmodem_rx_status(&rx));
}

static void test_errors_reset_after_good_block(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    for (uint8_t blk = 2; blk <= 4; blk++) {
        for (int i = 0; i < 9; i++) {                         /* 每块之前 9 次错误，不到上限 */
            TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));
        }
        TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(blk, 128, true, blk));
    }
    for (int i = 0; i < 9; i++) {
        TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NAK, xmodem_rx_timeout(&rx));
    }
    TEST_ASSERT_EQUAL_INT(XMODEM_RUNNING, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(512, xmodem_rx_length(&rx));
}

static void test_overflow_cancels_and_keeps_data(void)
{
    static uint8_t small[300];
    TEST_ASSERT_TRUE(xmodem_rx_init(&rx, small, sizeof small));
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_CAN, send_block(3, 128, true, 3));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_OVERFLOW, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(256, xmodem_rx_length(&rx));
    uint8_t want[128];
    fill(want, 128, 2);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(want, &small[128], 128);
}

static void test_exact_fit_is_not_overflow(void)
{
    static uint8_t exact[256];
    TEST_ASSERT_TRUE(xmodem_rx_init(&rx, exact, sizeof exact));
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(2, 128, true, 2));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_DONE, xmodem_rx_status(&rx));
}

static void test_init_and_null_arguments(void)
{
    TEST_ASSERT_FALSE(xmodem_rx_init(NULL, store, sizeof store));
    TEST_ASSERT_FALSE(xmodem_rx_init(&rx, NULL, 10));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_PARAM, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_start(&rx));
    TEST_ASSERT_FALSE(xmodem_rx_init(&rx, store, 0));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_SOH));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_start(NULL));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(NULL, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_timeout(NULL));
    TEST_ASSERT_EQUAL_INT(XMODEM_ERR_PARAM, xmodem_rx_status(NULL));
    TEST_ASSERT_EQUAL_UINT(0, xmodem_rx_length(NULL));
}

static void test_done_ignores_further_input(void)
{
    xmodem_rx_start(&rx);
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, send_block(1, 128, true, 1));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_ACK, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_feed(&rx, XMODEM_EOT));
    TEST_ASSERT_EQUAL_INT(XMODEM_REPLY_NONE, xmodem_rx_timeout(&rx));
    TEST_ASSERT_EQUAL_INT(XMODEM_DONE, xmodem_rx_status(&rx));
    TEST_ASSERT_EQUAL_UINT(128, xmodem_rx_length(&rx));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc_reference_value);
    RUN_TEST(test_spec_example_flow_crc);
    RUN_TEST(test_crc_is_high_byte_first);
    RUN_TEST(test_fallback_to_checksum_after_four_c);
    RUN_TEST(test_handshake_timeouts_not_counted);
    RUN_TEST(test_bad_complement_nak);
    RUN_TEST(test_duplicate_block_acked_and_discarded);
    RUN_TEST(test_sync_loss_cancels);
    RUN_TEST(test_block_zero_first_is_sync_loss);
    RUN_TEST(test_block_number_wraps_to_zero);
    RUN_TEST(test_mixed_1k_and_128_blocks);
    RUN_TEST(test_two_cans_cancel_one_can_ignored);
    RUN_TEST(test_can_and_soh_inside_data_are_data);
    RUN_TEST(test_garbage_between_blocks_ignored);
    RUN_TEST(test_timeout_inside_block_discards_it);
    RUN_TEST(test_ten_consecutive_errors_abort);
    RUN_TEST(test_errors_reset_after_good_block);
    RUN_TEST(test_overflow_cancels_and_keeps_data);
    RUN_TEST(test_exact_fit_is_not_overflow);
    RUN_TEST(test_init_and_null_arguments);
    RUN_TEST(test_done_ignores_further_input);
    return UNITY_END();
}
