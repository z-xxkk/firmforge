/* isotp 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "isotp.h"
#include "unity.h"

static isotp_rx_t rx;
static isotp_tx_t tx;
static uint8_t rxbuf[4095];
static uint8_t msg[4095];
static uint8_t fc[8];
static uint8_t fr[8];

void setUp(void)
{
    memset(&rx, 0xA5, sizeof rx);
    memset(&tx, 0xA5, sizeof tx);
    memset(rxbuf, 0, sizeof rxbuf);
    for (unsigned i = 0; i < sizeof msg; i++) {
        msg[i] = (uint8_t)(i * 13u + 7u);
    }
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, rxbuf, sizeof rxbuf, 0, 0));
    isotp_tx_init(&tx);
}

void tearDown(void) {}

/* ---------------------------------------------------------------- 测试自己的对端 */

static void sf(uint8_t *f, const uint8_t *d, unsigned n)
{
    memset(f, 0xCC, 8);
    f[0] = (uint8_t)n;
    memcpy(&f[1], d, n);
}

static void ff(uint8_t *f, const uint8_t *d, unsigned total)
{
    f[0] = (uint8_t)(0x10u | (total >> 8));
    f[1] = (uint8_t)total;
    memcpy(&f[2], d, 6);
}

/* 第 k 个连续帧（k 从 1 开始）；返回有效数据字节数 */
static unsigned cf(uint8_t *f, const uint8_t *d, unsigned total, unsigned k)
{
    unsigned off = 6u + (k - 1u) * 7u;
    unsigned n = total - off < 7u ? total - off : 7u;
    memset(f, 0xCC, 8);
    f[0] = (uint8_t)(0x20u | (k & 0x0Fu));
    memcpy(&f[1], &d[off], n);
    return n;
}

static void expect_fc(uint8_t fs, uint8_t bs, uint8_t st)
{
    const uint8_t want[8] = {(uint8_t)(0x30u | fs), bs, st, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(want, fc, 8);
}

/* 用自己的对端把一条消息发给被测接收方；返回收到"继续发送"流控的次数 */
static unsigned send_to_rx(unsigned total)
{
    unsigned fcs = 0;
    ff(fr, msg, total);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_SEND_FC, isotp_rx_frame(&rx, fr, 8, fc));
    fcs++;
    unsigned ncf = (total - 6u + 6u) / 7u;
    for (unsigned k = 1; k <= ncf; k++) {
        cf(fr, msg, total, k);
        isotp_rx_result_t r = isotp_rx_frame(&rx, fr, 8, fc);
        if (k == ncf) {
            TEST_ASSERT_EQUAL_INT_MESSAGE(ISOTP_RX_DONE, r, "最后一帧应当收完");
        } else if (r == ISOTP_RX_SEND_FC) {
            fcs++;
        } else {
            TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, r);
        }
    }
    TEST_ASSERT_EQUAL_UINT(total, isotp_rx_length(&rx));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, rxbuf, total);
    return fcs;
}

/* 从被测发送方取出全部连续帧，用自己的方法拼回数据并检查格式 */
static void drain_tx(unsigned total, uint8_t *out, unsigned *frames)
{
    unsigned off = 6, k = 0;
    uint32_t delay;
    while (isotp_tx_next(&tx, fr, &delay)) {
        k++;
        TEST_ASSERT_EQUAL_HEX8_MESSAGE(0x20u | (k & 0x0Fu), fr[0], "连续帧序号不对");
        unsigned n = total - off < 7u ? total - off : 7u;
        memcpy(&out[off], &fr[1], n);
        for (unsigned i = 1u + n; i < 8u; i++) {
            TEST_ASSERT_EQUAL_HEX8_MESSAGE(0xCC, fr[i], "最后一帧应补 CC");
        }
        off += n;
    }
    *frames = k;
}

/* ---------------------------------------------------------------- 接收 */

static void test_rx_single_frames(void)
{
    sf(fr, msg, 7);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, fr, 8, fc));
    TEST_ASSERT_EQUAL_UINT(7, isotp_rx_length(&rx));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, rxbuf, 7);
    const uint8_t short_sf[3] = {0x02, 0xAB, 0xCD};             /* 不补满的单帧也接受 */
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, short_sf, 3, fc));
    TEST_ASSERT_EQUAL_UINT(2, isotp_rx_length(&rx));
    TEST_ASSERT_EQUAL_HEX8(0xCD, rxbuf[1]);
}

static void test_rx_bad_single_frames_ignored(void)
{
    const uint8_t zero[8] = {0x00, 1, 2, 3, 4, 5, 6, 7};
    const uint8_t too_long[4] = {0x05, 1, 2, 3};
    const uint8_t eight[8] = {0x08, 1, 2, 3, 4, 5, 6, 7};
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, zero, 8, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, too_long, 4, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, eight, 8, fc));
    TEST_ASSERT_EQUAL_UINT(0, isotp_rx_length(&rx));
}

static void test_rx_multi_frame_sizes(void)
{
    static const unsigned sizes[] = {8, 13, 14, 62, 63, 118, 4095};
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, rxbuf, sizeof rxbuf, 0, 0x14));
    for (unsigned i = 0; i < sizeof sizes / sizeof sizes[0]; i++) {
        memset(rxbuf, 0, sizeof rxbuf);
        TEST_ASSERT_EQUAL_UINT(1, send_to_rx(sizes[i]));      /* 块大小 0：只回一帧流控 */
    }
    ff(fr, msg, 100);
    isotp_rx_frame(&rx, fr, 8, fc);
    expect_fc(0, 0, 0x14);
}

static void test_rx_sequence_wraps_to_zero(void)
{
    ff(fr, msg, 6u + 7u * 17u);                                /* 17 个连续帧：序号 1…15、0、1 */
    isotp_rx_frame(&rx, fr, 8, fc);
    for (unsigned k = 1; k <= 15; k++) {
        cf(fr, msg, 125, k);
        TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
    }
    cf(fr, msg, 125, 16);
    TEST_ASSERT_EQUAL_HEX8(0x20, fr[0]);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
    cf(fr, msg, 125, 17);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, fr, 8, fc));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, rxbuf, 125);
}

static void test_rx_block_size_flow_control(void)
{
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, rxbuf, sizeof rxbuf, 3, 0x05));
    /* 6 + 7×10 = 76 字节，10 个连续帧：第 3、6、9 个之后各回一帧，最后一帧之后不回 */
    TEST_ASSERT_EQUAL_UINT(1u + 3u, send_to_rx(76));
    expect_fc(0, 3, 0x05);
    /* 正好在块边界上收完：6 + 7×3 = 27 字节，第 3 个连续帧就是最后一帧，不回流控 */
    TEST_ASSERT_EQUAL_UINT(1, send_to_rx(27));
}

static void test_rx_wrong_sequence(void)
{
    ff(fr, msg, 40);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 40, 1);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 40, 3);                                          /* 跳过了 2 */
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_ERR_SEQ, isotp_rx_frame(&rx, fr, 8, fc));
    cf(fr, msg, 40, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));   /* 已放弃：之后的连续帧忽略 */
    TEST_ASSERT_EQUAL_UINT(0, isotp_rx_length(&rx));
}

static void test_rx_overflow(void)
{
    static uint8_t small[100];
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, small, sizeof small, 0, 0));
    ff(fr, msg, 101);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_ERR_OVERFLOW, isotp_rx_frame(&rx, fr, 8, fc));
    expect_fc(2, 0, 0);
    cf(fr, msg, 101, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
    ff(fr, msg, 100);                                            /* 正好放得下 */
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_SEND_FC, isotp_rx_frame(&rx, fr, 8, fc));
}

static void test_rx_bad_first_frames_ignored(void)
{
    ff(fr, msg, 50);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 7, fc));    /* 不足 8 字节 */
    ff(fr, msg, 7);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));    /* 总长度小于 8 */
    ff(fr, msg, 0);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));    /* 32 位扩展：不支持 */
    cf(fr, msg, 50, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));    /* 没在接收 */
}

static void test_rx_interrupted_by_new_frames(void)
{
    ff(fr, msg, 30);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 30, 1);
    isotp_rx_frame(&rx, fr, 8, fc);
    const uint8_t s[8] = {0x03, 0xAA, 0xBB, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, s, 8, fc));
    TEST_ASSERT_EQUAL_UINT(3, isotp_rx_length(&rx));
    TEST_ASSERT_EQUAL_UINT(1, isotp_rx_interrupted(&rx));
    cf(fr, msg, 30, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));   /* 旧消息已放弃 */
    ff(fr, msg, 30);
    isotp_rx_frame(&rx, fr, 8, fc);
    ff(fr, msg, 20);                                             /* 首帧打断首帧 */
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_SEND_FC, isotp_rx_frame(&rx, fr, 8, fc));
    TEST_ASSERT_EQUAL_UINT(2, isotp_rx_interrupted(&rx));
    cf(fr, msg, 20, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
    cf(fr, msg, 20, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, fr, 8, fc));
    TEST_ASSERT_EQUAL_UINT(20, isotp_rx_length(&rx));
}

static void test_rx_ignores_flow_control_and_unknown(void)
{
    const uint8_t fcf[8] = {0x30, 0, 0, 0, 0, 0, 0, 0};
    const uint8_t unk[8] = {0x40, 1, 2, 3, 4, 5, 6, 7};
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fcf, 8, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, unk, 8, fc));
    ff(fr, msg, 20);
    isotp_rx_frame(&rx, fr, 8, fc);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, unk, 8, fc));   /* 接收中也不受影响 */
    cf(fr, msg, 20, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
    cf(fr, msg, 20, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, fr, 8, fc));
}

static void test_rx_short_consecutive_frames(void)
{
    ff(fr, msg, 20);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 20, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_ERR_FORMAT, isotp_rx_frame(&rx, fr, 7, fc));  /* 不是最后一帧却不足 8 字节 */
    ff(fr, msg, 15);                                             /* 6 + 7 + 2 */
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 15, 1);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 15, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_DONE, isotp_rx_frame(&rx, fr, 3, fc));       /* 最后一帧不补满也可以 */
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, rxbuf, 15);
    ff(fr, msg, 15);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 15, 1);
    isotp_rx_frame(&rx, fr, 8, fc);
    cf(fr, msg, 15, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_ERR_FORMAT, isotp_rx_frame(&rx, fr, 2, fc));  /* 数据不够 */
}

static void test_rx_timeout(void)
{
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_timeout(&rx));
    ff(fr, msg, 20);
    isotp_rx_frame(&rx, fr, 8, fc);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_ERR_TIMEOUT, isotp_rx_timeout(&rx));
    cf(fr, msg, 20, 1);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, fc));
}

static void test_rx_null_arguments(void)
{
    TEST_ASSERT_FALSE(isotp_rx_init(NULL, rxbuf, 10, 0, 0));
    TEST_ASSERT_FALSE(isotp_rx_init(&rx, NULL, 10, 0, 0));
    TEST_ASSERT_FALSE(isotp_rx_init(&rx, rxbuf, 0, 0, 0));
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, rxbuf, 10, 0, 0));
    sf(fr, msg, 3);
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(NULL, fr, 8, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, NULL, 8, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 8, NULL));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 0, fc));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_frame(&rx, fr, 9, fc));
    TEST_ASSERT_EQUAL_UINT(0, isotp_rx_length(NULL));
    TEST_ASSERT_EQUAL_INT(ISOTP_RX_NONE, isotp_rx_timeout(NULL));
}

/* ---------------------------------------------------------------- 发送 */

static void test_tx_spec_example(void)
{
    uint8_t d[30];
    for (unsigned i = 0; i < 30; i++) {
        d[i] = (uint8_t)i;
    }
    uint32_t delay = 99;
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, d, 30, fr));
    const uint8_t f0[8] = {0x10, 0x1E, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(f0, fr, 8);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));
    TEST_ASSERT_FALSE(isotp_tx_next(&tx, fr, &delay));
    const uint8_t fcf[8] = {0x30, 0x02, 0x05, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
    isotp_tx_fc(&tx, fcf, 8);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    const uint8_t f1[8] = {0x21, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(f1, fr, 8);
    TEST_ASSERT_EQUAL_UINT32(0, delay);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    TEST_ASSERT_EQUAL_HEX8(0x22, fr[0]);
    TEST_ASSERT_EQUAL_UINT32(5000, delay);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));
    TEST_ASSERT_FALSE(isotp_tx_next(&tx, fr, &delay));
    isotp_tx_fc(&tx, fcf, 8);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    const uint8_t f3[8] = {0x23, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(f3, fr, 8);
    TEST_ASSERT_EQUAL_UINT32(0, delay);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    const uint8_t f4[8] = {0x24, 0x1B, 0x1C, 0x1D, 0xCC, 0xCC, 0xCC, 0xCC};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(f4, fr, 8);
    TEST_ASSERT_EQUAL_UINT32(5000, delay);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_DONE, isotp_tx_state(&tx));
}

static void test_tx_single_frames_padded(void)
{
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 1, fr));
    const uint8_t a[8] = {0x01, msg[0], 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(a, fr, 8);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_DONE, isotp_tx_state(&tx));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 7, fr));
    TEST_ASSERT_EQUAL_HEX8(0x07, fr[0]);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, &fr[1], 7);
}

static void test_tx_max_length_no_block_limit(void)
{
    static uint8_t out[4095];
    unsigned frames;
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 4095, fr));
    TEST_ASSERT_EQUAL_HEX8(0x1F, fr[0]);
    TEST_ASSERT_EQUAL_HEX8(0xFF, fr[1]);
    memcpy(out, &fr[2], 6);
    const uint8_t fcf[3] = {0x30, 0x00, 0x00};
    isotp_tx_fc(&tx, fcf, 3);
    drain_tx(4095, out, &frames);
    TEST_ASSERT_EQUAL_UINT((4095u - 6u + 6u) / 7u, frames);   /* 块大小 0：一口气发完，序号多次回绕 */
    TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, out, 4095);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_DONE, isotp_tx_state(&tx));
}

static void test_tx_stmin_decoding(void)
{
    static const uint8_t st[] = {0x00, 0x01, 0x7F, 0xF1, 0xF9, 0x80, 0xF0, 0xFA, 0xFF};
    static const uint32_t us[] = {0, 1000, 127000, 100, 900, 127000, 127000, 127000, 127000};
    for (unsigned i = 0; i < sizeof st; i++) {
        uint32_t delay;
        isotp_tx_init(&tx);
        TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 30, fr));
        const uint8_t fcf[8] = {0x30, 0x00, st[i], 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
        isotp_tx_fc(&tx, fcf, 8);
        isotp_tx_next(&tx, fr, &delay);
        TEST_ASSERT_EQUAL_UINT32(0, delay);
        isotp_tx_next(&tx, fr, &delay);
        TEST_ASSERT_EQUAL_UINT32_MESSAGE(us[i], delay, "最小间隔换算不对");
    }
}

static void test_tx_params_from_first_cts_only(void)
{
    uint32_t delay;
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 100, fr));
    const uint8_t first[3] = {0x30, 0x01, 0x0A};
    const uint8_t other[3] = {0x30, 0x04, 0x00};
    isotp_tx_fc(&tx, first, 3);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));      /* 块大小 1 */
    isotp_tx_fc(&tx, other, 3);                                       /* 换了块大小：不采用 */
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));
    isotp_tx_fc(&tx, other, 3);
    isotp_tx_next(&tx, fr, &delay);
    TEST_ASSERT_EQUAL_UINT32(0, delay);
}

static void test_tx_wait_then_continue(void)
{
    uint32_t delay;
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 20, fr));
    const uint8_t wait[3] = {0x31, 0x00, 0x00};
    const uint8_t cts[3] = {0x30, 0x00, 0x00};
    isotp_tx_fc(&tx, wait, 3);
    isotp_tx_fc(&tx, wait, 3);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));
    TEST_ASSERT_FALSE(isotp_tx_next(&tx, fr, &delay));
    isotp_tx_fc(&tx, cts, 3);
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    TEST_ASSERT_TRUE(isotp_tx_next(&tx, fr, &delay));
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_DONE, isotp_tx_state(&tx));
}

static void test_tx_overflow_and_bad_flow_control(void)
{
    const uint8_t ovf[3] = {0x32, 0, 0};
    const uint8_t bad[3] = {0x33, 0, 0};
    const uint8_t shortfc[2] = {0x30, 0};
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 20, fr));
    isotp_tx_fc(&tx, ovf, 3);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_ERR_OVERFLOW, isotp_tx_state(&tx));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 20, fr));                /* 出错后可以重新开始 */
    isotp_tx_fc(&tx, bad, 3);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_ERR_FC, isotp_tx_state(&tx));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 20, fr));
    isotp_tx_fc(&tx, shortfc, 2);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_ERR_FC, isotp_tx_state(&tx));
}

static void test_tx_ignores_unexpected_frames(void)
{
    uint32_t delay;
    const uint8_t cts[3] = {0x30, 0x00, 0x00};
    const uint8_t sf1[3] = {0x02, 0x11, 0x22};
    isotp_tx_fc(&tx, cts, 3);                                           /* 空闲时的流控 */
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_IDLE, isotp_tx_state(&tx));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 30, fr));
    isotp_tx_fc(&tx, sf1, 3);                                           /* 不是流控帧 */
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_WAIT_FC, isotp_tx_state(&tx));
    isotp_tx_fc(&tx, cts, 3);
    isotp_tx_next(&tx, fr, &delay);
    isotp_tx_fc(&tx, (const uint8_t[3]){0x32, 0, 0}, 3);               /* 正在发送时的流控 */
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_SENDING, isotp_tx_state(&tx));
}

static void test_tx_timeout(void)
{
    isotp_tx_timeout(&tx);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_IDLE, isotp_tx_state(&tx));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 20, fr));
    isotp_tx_timeout(&tx);
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_ERR_TIMEOUT, isotp_tx_state(&tx));
}

static void test_tx_start_rejections(void)
{
    TEST_ASSERT_FALSE(isotp_tx_start(&tx, msg, 0, fr));
    TEST_ASSERT_FALSE(isotp_tx_start(&tx, msg, 4096, fr));
    TEST_ASSERT_FALSE(isotp_tx_start(&tx, NULL, 10, fr));
    TEST_ASSERT_FALSE(isotp_tx_start(&tx, msg, 10, NULL));
    TEST_ASSERT_FALSE(isotp_tx_start(NULL, msg, 10, fr));
    TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, 10, fr));
    TEST_ASSERT_FALSE(isotp_tx_start(&tx, msg, 3, fr));                /* 上一条还没发完 */
    TEST_ASSERT_EQUAL_HEX8(0x10, fr[0]);
    uint32_t delay;
    TEST_ASSERT_FALSE(isotp_tx_next(&tx, NULL, &delay));
    TEST_ASSERT_EQUAL_INT(ISOTP_TX_IDLE, isotp_tx_state(NULL));
}

static void test_loopback_with_block_size(void)
{
    static const unsigned sizes[] = {1, 8, 50, 300, 4095};
    TEST_ASSERT_TRUE(isotp_rx_init(&rx, rxbuf, sizeof rxbuf, 4, 0xF3));
    for (unsigned s = 0; s < sizeof sizes / sizeof sizes[0]; s++) {
        uint32_t delay;
        memset(rxbuf, 0, sizeof rxbuf);
        isotp_tx_init(&tx);
        TEST_ASSERT_TRUE(isotp_tx_start(&tx, msg, sizes[s], fr));
        isotp_rx_result_t r = isotp_rx_frame(&rx, fr, 8, fc);
        while (r != ISOTP_RX_DONE) {
            TEST_ASSERT_EQUAL_INT(ISOTP_RX_SEND_FC, r);
            isotp_tx_fc(&tx, fc, 8);
            r = ISOTP_RX_NONE;
            while (r == ISOTP_RX_NONE && isotp_tx_next(&tx, fr, &delay)) {
                TEST_ASSERT_TRUE(delay == 0u || delay == 300u);
                r = isotp_rx_frame(&rx, fr, 8, fc);
            }
        }
        TEST_ASSERT_EQUAL_INT(ISOTP_TX_DONE, isotp_tx_state(&tx));
        TEST_ASSERT_EQUAL_UINT(sizes[s], isotp_rx_length(&rx));
        TEST_ASSERT_EQUAL_UINT8_ARRAY(msg, rxbuf, sizes[s]);
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_rx_single_frames);
    RUN_TEST(test_rx_bad_single_frames_ignored);
    RUN_TEST(test_rx_multi_frame_sizes);
    RUN_TEST(test_rx_sequence_wraps_to_zero);
    RUN_TEST(test_rx_block_size_flow_control);
    RUN_TEST(test_rx_wrong_sequence);
    RUN_TEST(test_rx_overflow);
    RUN_TEST(test_rx_bad_first_frames_ignored);
    RUN_TEST(test_rx_interrupted_by_new_frames);
    RUN_TEST(test_rx_ignores_flow_control_and_unknown);
    RUN_TEST(test_rx_short_consecutive_frames);
    RUN_TEST(test_rx_timeout);
    RUN_TEST(test_rx_null_arguments);
    RUN_TEST(test_tx_spec_example);
    RUN_TEST(test_tx_single_frames_padded);
    RUN_TEST(test_tx_max_length_no_block_limit);
    RUN_TEST(test_tx_stmin_decoding);
    RUN_TEST(test_tx_params_from_first_cts_only);
    RUN_TEST(test_tx_wait_then_continue);
    RUN_TEST(test_tx_overflow_and_bad_flow_control);
    RUN_TEST(test_tx_ignores_unexpected_frames);
    RUN_TEST(test_tx_timeout);
    RUN_TEST(test_tx_start_rejections);
    RUN_TEST(test_loopback_with_block_size);
    return UNITY_END();
}
