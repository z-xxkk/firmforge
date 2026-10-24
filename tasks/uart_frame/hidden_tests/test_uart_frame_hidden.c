/* uart_frame 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "uart_frame.h"

static uart_frame_rx_t rx;

void setUp(void)
{
    memset(&rx, 0x5A, sizeof rx);
    uart_frame_init(&rx);
}

void tearDown(void) {}

static uint16_t crc16(const uint8_t *d, size_t n)
{
    uint16_t c = 0xFFFFu;
    for (size_t i = 0; i < n; i++) {
        c ^= d[i];
        for (int b = 0; b < 8; b++) {
            c = (c & 1u) ? (uint16_t)((c >> 1) ^ 0xA001u) : (uint16_t)(c >> 1);
        }
    }
    return c;
}

/* 组一帧，返回总长度 */
static size_t build(uint8_t *out, const uint8_t *payload, uint8_t n)
{
    out[0] = 0xAA;
    out[1] = n;
    if (n > 0u) {
        memcpy(&out[2], payload, n);
    }
    uint16_t c = crc16(&out[1], (size_t)n + 1u);
    out[2 + n] = (uint8_t)(c & 0xFFu);
    out[3 + n] = (uint8_t)(c >> 8);
    return (size_t)n + 4u;
}

/* 喂入一串字节，返回每个字节对应的结果中 OK 和 ERROR 出现的次数 */
static void feed_all(const uint8_t *d, size_t n, int *ok, int *err)
{
    *ok = 0;
    *err = 0;
    for (size_t i = 0; i < n; i++) {
        uart_frame_result_t r = uart_frame_feed(&rx, d[i]);
        if (r == UART_FRAME_OK) {
            (*ok)++;
        } else if (r == UART_FRAME_ERROR) {
            (*err)++;
        }
    }
}

static void assert_payload(const uint8_t *expect, uint8_t n)
{
    const uint8_t *data = NULL;
    TEST_ASSERT_EQUAL_UINT8(n, uart_frame_payload(&rx, &data));
    TEST_ASSERT_NOT_NULL(data);
    if (n > 0) {
        TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, data, n);
    }
}

static void test_good_frame_ok_only_on_last_byte(void)
{
    const uint8_t p[3] = {1, 2, 3};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 3);
    for (size_t i = 0; i + 1 < n; i++) {
        TEST_ASSERT_EQUAL_INT(UART_FRAME_NONE, uart_frame_feed(&rx, f[i]));
    }
    TEST_ASSERT_EQUAL_INT(UART_FRAME_OK, uart_frame_feed(&rx, f[n - 1]));
    assert_payload(p, 3);
}

static void test_known_frame_bytes(void)
{
    /* 数据 01 02 03：CRC 计算 03 01 02 03 */
    const uint8_t p[3] = {1, 2, 3};
    uint8_t f[UART_FRAME_MAX_SIZE];
    build(f, p, 3);
    uint16_t c = crc16((const uint8_t[]){3, 1, 2, 3}, 4);
    TEST_ASSERT_EQUAL_HEX8(c & 0xFF, f[5]);
    int ok, err;
    feed_all(f, 7, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
}

static void test_empty_payload(void)
{
    uint8_t f[4];
    build(f, NULL, 0);
    int ok, err;
    feed_all(f, 4, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    TEST_ASSERT_EQUAL_INT(0, err);
    const uint8_t *data = NULL;
    TEST_ASSERT_EQUAL_UINT8(0, uart_frame_payload(&rx, &data));
}

static void test_max_payload_64(void)
{
    uint8_t p[64];
    uint8_t f[UART_FRAME_MAX_SIZE];
    for (int i = 0; i < 64; i++) {
        p[i] = (uint8_t)(i * 7 + 1);
    }
    size_t n = build(f, p, 64);
    int ok, err;
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    assert_payload(p, 64);
}

static void test_noise_before_header_ignored(void)
{
    const uint8_t noise[5] = {0x00, 0x55, 0xFF, 0x13, 0x37};
    const uint8_t p[2] = {9, 8};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 2);
    int ok, err;
    feed_all(noise, 5, &ok, &err);
    TEST_ASSERT_EQUAL_INT(0, ok + err);
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    assert_payload(p, 2);
}

static void test_back_to_back_frames(void)
{
    const uint8_t a[2] = {0x11, 0x22};
    const uint8_t b[3] = {0x33, 0x44, 0x55};
    uint8_t stream[2 * UART_FRAME_MAX_SIZE];
    size_t n = build(stream, a, 2);
    n += build(&stream[n], b, 3);
    int ok = 0;
    for (size_t i = 0; i < n; i++) {
        if (uart_frame_feed(&rx, stream[i]) == UART_FRAME_OK) {
            ok++;
            if (ok == 1) {
                assert_payload(a, 2);
            }
        }
    }
    TEST_ASSERT_EQUAL_INT(2, ok);
    assert_payload(b, 3);
}

static void test_payload_containing_header_byte(void)
{
    const uint8_t p[4] = {0xAA, 0xAA, 0x02, 0xAA};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 4);
    int ok, err;
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    TEST_ASSERT_EQUAL_INT(0, err);
    assert_payload(p, 4);
}

static void test_bad_crc_then_good_frame(void)
{
    const uint8_t p[3] = {1, 2, 3};
    uint8_t bad[UART_FRAME_MAX_SIZE];
    uint8_t good[UART_FRAME_MAX_SIZE];
    size_t nb = build(bad, p, 3);
    size_t ng = build(good, p, 3);
    bad[3] ^= 0x01;
    for (size_t i = 0; i + 1 < nb; i++) {
        TEST_ASSERT_EQUAL_INT(UART_FRAME_NONE, uart_frame_feed(&rx, bad[i]));
    }
    TEST_ASSERT_EQUAL_INT(UART_FRAME_ERROR, uart_frame_feed(&rx, bad[nb - 1]));
    int ok, err;
    feed_all(good, ng, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    TEST_ASSERT_EQUAL_INT(0, err);
}

static void test_swapped_crc_rejected(void)
{
    const uint8_t p[2] = {0x10, 0x20};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 2);
    uint8_t t = f[n - 2];
    f[n - 2] = f[n - 1];
    f[n - 1] = t;
    int ok, err;
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(0, ok);
    TEST_ASSERT_EQUAL_INT(1, err);
}

static void test_length_over_64_rejected_immediately(void)
{
    TEST_ASSERT_EQUAL_INT(UART_FRAME_NONE, uart_frame_feed(&rx, 0xAA));
    TEST_ASSERT_EQUAL_INT(UART_FRAME_ERROR, uart_frame_feed(&rx, 65));
    const uint8_t p[1] = {7};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 1);
    int ok, err;
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, ok);
}

static void test_noise_header_followed_by_real_frame(void)
{
    /* 噪声 0xAA 被当成帧头，真正的帧头被当成长度 0xAA(>64) → 重新同步后真帧仍能收到 */
    const uint8_t p[3] = {4, 5, 6};
    uint8_t stream[1 + UART_FRAME_MAX_SIZE];
    stream[0] = 0xAA;
    size_t n = 1 + build(&stream[1], p, 3);
    int ok, err;
    feed_all(stream, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    assert_payload(p, 3);
}

static void test_real_frame_hidden_inside_bad_frame(void)
{
    /* AA 02 | AA 01 33 crcL crcH：先把 AA 02 当成帧，CRC 失败后在已收字节里找到真帧头 */
    const uint8_t p[1] = {0x33};
    uint8_t real[UART_FRAME_MAX_SIZE];
    size_t nr = build(real, p, 1);
    uint8_t stream[2 + UART_FRAME_MAX_SIZE];
    stream[0] = 0xAA;
    stream[1] = 0x02;
    memcpy(&stream[2], real, nr);
    int ok, err;
    feed_all(stream, 2 + nr, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, err);
    TEST_ASSERT_EQUAL_INT(1, ok);
    assert_payload(p, 1);
}

static void test_last_good_frame_survives_errors(void)
{
    const uint8_t p[2] = {0xDE, 0xAD};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 2);
    int ok, err;
    feed_all(f, n, &ok, &err);
    f[2] ^= 0xFF;
    feed_all(f, n, &ok, &err);
    TEST_ASSERT_EQUAL_INT(1, err);
    const uint8_t expect[2] = {0xDE, 0xAD};
    assert_payload(expect, 2);
}

static void test_payload_before_any_frame(void)
{
    const uint8_t *data = (const uint8_t *)&rx;
    TEST_ASSERT_EQUAL_UINT8(0, uart_frame_payload(&rx, &data));
    TEST_ASSERT_NULL(data);
    TEST_ASSERT_EQUAL_UINT8(0, uart_frame_payload(&rx, NULL));
}

static void test_init_discards_partial_and_old_frame(void)
{
    const uint8_t p[2] = {1, 2};
    uint8_t f[UART_FRAME_MAX_SIZE];
    size_t n = build(f, p, 2);
    int ok, err;
    feed_all(f, n, &ok, &err);
    feed_all(f, 3, &ok, &err);                 /* 半帧 */
    uart_frame_init(&rx);
    TEST_ASSERT_EQUAL_UINT8(0, uart_frame_payload(&rx, NULL));
    feed_all(f, n, &ok, &err);                 /* 从头再来一帧完整的 */
    TEST_ASSERT_EQUAL_INT(1, ok);
}

static void test_long_stream_with_noise(void)
{
    uint8_t stream[2048];
    uint8_t expect_len[40];
    size_t n = 0;
    uint32_t x = 2024u;
    for (int k = 0; k < 40; k++) {
        uint8_t p[UART_FRAME_MAX_PAYLOAD];
        x = x * 1103515245u + 12345u;
        uint8_t len = (uint8_t)((x >> 16) % 65u);
        for (uint8_t i = 0; i < len; i++) {
            x = x * 1103515245u + 12345u;
            p[i] = (uint8_t)(x >> 16);
        }
        x = x * 1103515245u + 12345u;
        uint8_t gap = (uint8_t)((x >> 16) % 4u);
        for (uint8_t g = 0; g < gap; g++) {
            stream[n++] = 0x55;                /* 帧间噪声（不含 0xAA） */
        }
        expect_len[k] = len;
        n += build(&stream[n], p, len);
    }
    int ok = 0;
    for (size_t i = 0; i < n; i++) {
        uart_frame_result_t r = uart_frame_feed(&rx, stream[i]);
        TEST_ASSERT_NOT_EQUAL(UART_FRAME_ERROR, r);
        if (r == UART_FRAME_OK) {
            TEST_ASSERT_EQUAL_UINT8(expect_len[ok], uart_frame_payload(&rx, NULL));
            ok++;
        }
    }
    TEST_ASSERT_EQUAL_INT(40, ok);
}

static void test_null_arguments(void)
{
    const uint8_t *data = (const uint8_t *)&rx;
    uart_frame_init(NULL);
    TEST_ASSERT_EQUAL_INT(UART_FRAME_NONE, uart_frame_feed(NULL, 0xAA));
    TEST_ASSERT_EQUAL_UINT8(0, uart_frame_payload(NULL, &data));
    TEST_ASSERT_NULL(data);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_good_frame_ok_only_on_last_byte);
    RUN_TEST(test_known_frame_bytes);
    RUN_TEST(test_empty_payload);
    RUN_TEST(test_max_payload_64);
    RUN_TEST(test_noise_before_header_ignored);
    RUN_TEST(test_back_to_back_frames);
    RUN_TEST(test_payload_containing_header_byte);
    RUN_TEST(test_bad_crc_then_good_frame);
    RUN_TEST(test_swapped_crc_rejected);
    RUN_TEST(test_length_over_64_rejected_immediately);
    RUN_TEST(test_noise_header_followed_by_real_frame);
    RUN_TEST(test_real_frame_hidden_inside_bad_frame);
    RUN_TEST(test_last_good_frame_survives_errors);
    RUN_TEST(test_payload_before_any_frame);
    RUN_TEST(test_init_discards_partial_and_old_frame);
    RUN_TEST(test_long_stream_with_noise);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
