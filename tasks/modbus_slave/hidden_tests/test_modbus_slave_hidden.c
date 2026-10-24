/* modbus_slave 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "modbus_slave.h"

#define ADDR 0x11u

static modbus_slave_t s;
static uint16_t regs[200];
static uint8_t resp[300];

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

/* 在 f[0..n) 后加 CRC（低字节在前），返回总长度 */
static size_t with_crc(uint8_t *f, size_t n)
{
    uint16_t c = crc16(f, n);
    f[n] = (uint8_t)(c & 0xFFu);
    f[n + 1] = (uint8_t)(c >> 8);
    return n + 2;
}

static size_t req4(uint8_t *f, uint8_t addr, uint8_t fc, uint16_t a, uint16_t b)
{
    f[0] = addr;
    f[1] = fc;
    f[2] = (uint8_t)(a >> 8);
    f[3] = (uint8_t)a;
    f[4] = (uint8_t)(b >> 8);
    f[5] = (uint8_t)b;
    return with_crc(f, 6);
}

static size_t handle(const uint8_t *req, size_t n)
{
    memset(resp, 0xEE, sizeof resp);
    return modbus_slave_handle(&s, req, n, resp, sizeof resp);
}

static void assert_crc_ok(const uint8_t *f, size_t n)
{
    uint16_t c = crc16(f, n - 2);
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(c & 0xFFu, f[n - 2], "CRC 低字节应在前");
    TEST_ASSERT_EQUAL_HEX8(c >> 8, f[n - 1]);
}

static void assert_exception(size_t n, uint8_t fc, uint8_t code)
{
    TEST_ASSERT_EQUAL_UINT(5, n);
    TEST_ASSERT_EQUAL_HEX8(ADDR, resp[0]);
    TEST_ASSERT_EQUAL_HEX8((uint8_t)(fc | 0x80u), resp[1]);
    TEST_ASSERT_EQUAL_HEX8(code, resp[2]);
    assert_crc_ok(resp, 5);
}

void setUp(void)
{
    for (unsigned i = 0; i < 200; i++) {
        regs[i] = (uint16_t)(i * 3u + 1u);
    }
    memset(&s, 0xA5, sizeof s);
    TEST_ASSERT_TRUE(modbus_slave_init(&s, ADDR, regs, 100));
}

void tearDown(void) {}

static void test_init_address_and_args(void)
{
    modbus_slave_t t;
    TEST_ASSERT_TRUE(modbus_slave_init(&t, 1, regs, 1));
    TEST_ASSERT_TRUE(modbus_slave_init(&t, 247, regs, 10));
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 0, regs, 10));
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 248, regs, 10));
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 255, regs, 10));
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 5, NULL, 10));
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 5, regs, 0));
    TEST_ASSERT_FALSE(modbus_slave_init(NULL, 5, regs, 10));
}

static void test_failed_init_never_replies(void)
{
    modbus_slave_t t;
    uint8_t f[8];
    TEST_ASSERT_FALSE(modbus_slave_init(&t, 0, regs, 10));
    size_t n = req4(f, 0, 0x03, 0, 1);
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(&t, f, n, resp, sizeof resp));
    n = req4(f, 0x11, 0x03, 0, 1);
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(&t, f, n, resp, sizeof resp));
}

static void test_read_spec_example(void)
{
    uint8_t f[8];
    TEST_ASSERT_TRUE(modbus_slave_init(&s, ADDR, regs, 120));
    regs[0x6B] = 0x022B;
    regs[0x6C] = 0x0000;
    regs[0x6D] = 0x0064;
    size_t n = handle(f, req4(f, ADDR, 0x03, 0x006B, 3));
    const uint8_t expect[9] = {ADDR, 0x03, 0x06, 0x02, 0x2B, 0x00, 0x00, 0x00, 0x64};
    TEST_ASSERT_EQUAL_UINT(11, n);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, resp, 9);
    assert_crc_ok(resp, 11);
}

static void test_read_max_125_registers(void)
{
    uint8_t f[8];
    TEST_ASSERT_TRUE(modbus_slave_init(&s, ADDR, regs, 125));
    size_t n = handle(f, req4(f, ADDR, 0x03, 0, 125));
    TEST_ASSERT_EQUAL_UINT(5 + 250, n);
    TEST_ASSERT_EQUAL_HEX8(250, resp[2]);
    TEST_ASSERT_EQUAL_HEX8(regs[124] >> 8, resp[3 + 248]);
    TEST_ASSERT_EQUAL_HEX8(regs[124] & 0xFF, resp[3 + 249]);
    assert_crc_ok(resp, n);
}

static void test_read_quantity_out_of_range(void)
{
    uint8_t f[8];
    assert_exception(handle(f, req4(f, ADDR, 0x03, 0, 0)), 0x03, 0x03);
    assert_exception(handle(f, req4(f, ADDR, 0x03, 0, 126)), 0x03, 0x03);
}

static void test_read_address_range_spec_example(void)
{
    uint8_t f[8];
    size_t n = handle(f, req4(f, ADDR, 0x03, 96, 4));       /* 100 个寄存器：96–99 可以 */
    TEST_ASSERT_EQUAL_UINT(5 + 8, n);
    TEST_ASSERT_EQUAL_HEX8(regs[99] & 0xFF, resp[3 + 7]);
    assert_exception(handle(f, req4(f, ADDR, 0x03, 96, 5)), 0x03, 0x02);
    assert_exception(handle(f, req4(f, ADDR, 0x03, 0xFFFF, 1)), 0x03, 0x02);
}

static void test_quantity_checked_before_address(void)
{
    uint8_t f[8];
    /* 地址非法且数量非法：规范图 13 先查数量，应报 03 */
    assert_exception(handle(f, req4(f, ADDR, 0x03, 1000, 0)), 0x03, 0x03);
    assert_exception(handle(f, req4(f, ADDR, 0x03, 1000, 200)), 0x03, 0x03);
}

static void test_wrong_length_is_illegal_value(void)
{
    uint8_t f[16];
    f[0] = ADDR; f[1] = 0x03; f[2] = 0; f[3] = 0; f[4] = 0;
    assert_exception(handle(f, with_crc(f, 5)), 0x03, 0x03);               /* 少一个字节 */
    f[5] = 1; f[6] = 0x99;
    assert_exception(handle(f, with_crc(f, 7)), 0x03, 0x03);               /* 多一个字节 */
    f[1] = 0x06;
    assert_exception(handle(f, with_crc(f, 5)), 0x06, 0x03);
}

static void test_write_single_spec_example(void)
{
    uint8_t f[8];
    size_t len = req4(f, ADDR, 0x06, 0x0001, 0x0003);
    size_t n = handle(f, len);
    TEST_ASSERT_EQUAL_UINT(8, n);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(f, resp, 8);                 /* 原样回显 */
    TEST_ASSERT_EQUAL_HEX16(0x0003, regs[1]);
}

static void test_write_last_and_out_of_range(void)
{
    uint8_t f[8];
    TEST_ASSERT_EQUAL_UINT(8, handle(f, req4(f, ADDR, 0x06, 99, 0xBEEF)));
    TEST_ASSERT_EQUAL_HEX16(0xBEEF, regs[99]);
    uint16_t before = regs[100];
    assert_exception(handle(f, req4(f, ADDR, 0x06, 100, 0x1234)), 0x06, 0x02);
    TEST_ASSERT_EQUAL_HEX16(before, regs[100]);
}

static void test_unsupported_function(void)
{
    static const uint8_t fcs[4] = {0x01, 0x04, 0x10, 0x2B};
    uint8_t f[8];
    for (int i = 0; i < 4; i++) {
        assert_exception(handle(f, req4(f, ADDR, fcs[i], 0, 1)), fcs[i], 0x01);
    }
}

static void test_bad_crc_ignored(void)
{
    uint8_t f[8];
    size_t n = req4(f, ADDR, 0x06, 2, 0x5555);
    f[n - 1] ^= 0x01;
    uint16_t before = regs[2];
    TEST_ASSERT_EQUAL_UINT(0, handle(f, n));
    TEST_ASSERT_EQUAL_HEX16(before, regs[2]);
    n = req4(f, ADDR, 0x03, 0, 1);
    uint8_t t = f[6];
    f[6] = f[7];
    f[7] = t;                                                   /* CRC 两字节对调 */
    TEST_ASSERT_EQUAL_UINT(0, handle(f, n));
}

static void test_other_address_ignored(void)
{
    uint8_t f[8];
    uint16_t before = regs[3];
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0x12, 0x06, 3, 0x7777)));
    TEST_ASSERT_EQUAL_HEX16(before, regs[3]);
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0xF8, 0x03, 0, 1)));
}

static void test_broadcast_write_executes_without_reply(void)
{
    uint8_t f[8];
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0x00, 0x06, 5, 0xA1B2)));
    TEST_ASSERT_EQUAL_HEX16(0xA1B2, regs[5]);
    TEST_ASSERT_EQUAL_HEX8(0xEE, resp[0]);                     /* 响应缓冲区没被写 */
}

static void test_broadcast_other_cases_silent(void)
{
    uint8_t f[8];
    uint16_t snapshot[100];
    memcpy(snapshot, regs, sizeof snapshot);
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0x00, 0x03, 0, 2)));
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0x00, 0x10, 0, 2)));
    TEST_ASSERT_EQUAL_UINT(0, handle(f, req4(f, 0x00, 0x06, 150, 1)));   /* 广播写地址非法 */
    TEST_ASSERT_EQUAL_MEMORY(snapshot, regs, sizeof snapshot);
    TEST_ASSERT_EQUAL_HEX8(0xEE, resp[0]);
}

static void test_frame_length_limits(void)
{
    uint8_t f[300];
    f[0] = ADDR;
    f[1] = 0x03;
    TEST_ASSERT_EQUAL_UINT(0, handle(f, 3));
    memset(f, 0, sizeof f);
    f[0] = ADDR;
    f[1] = 0x03;
    size_t n = with_crc(f, 255);                               /* 257 字节，CRC 正确 */
    TEST_ASSERT_EQUAL_UINT(0, handle(f, n));
}

static void test_response_buffer_too_small(void)
{
    uint8_t f[8];
    size_t len = req4(f, ADDR, 0x03, 0, 10);
    memset(resp, 0xEE, sizeof resp);
    TEST_ASSERT_EQUAL_UINT(5, modbus_slave_handle(&s, f, len, resp, 10));
    TEST_ASSERT_EQUAL_HEX8(0x83, resp[1]);
    TEST_ASSERT_EQUAL_HEX8(0x04, resp[2]);
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(&s, f, len, resp, 4));

    uint16_t before = regs[7];
    len = req4(f, ADDR, 0x06, 7, 0x4242);
    TEST_ASSERT_EQUAL_UINT(5, modbus_slave_handle(&s, f, len, resp, 7));
    TEST_ASSERT_EQUAL_HEX8(0x86, resp[1]);
    TEST_ASSERT_EQUAL_HEX8(0x04, resp[2]);
    TEST_ASSERT_EQUAL_HEX16(before, regs[7]);                  /* 不执行写入 */
}

static void test_null_arguments(void)
{
    uint8_t f[8];
    size_t n = req4(f, ADDR, 0x03, 0, 1);
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(NULL, f, n, resp, sizeof resp));
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(&s, NULL, n, resp, sizeof resp));
    TEST_ASSERT_EQUAL_UINT(0, modbus_slave_handle(&s, f, n, NULL, sizeof resp));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_address_and_args);
    RUN_TEST(test_failed_init_never_replies);
    RUN_TEST(test_read_spec_example);
    RUN_TEST(test_read_max_125_registers);
    RUN_TEST(test_read_quantity_out_of_range);
    RUN_TEST(test_read_address_range_spec_example);
    RUN_TEST(test_quantity_checked_before_address);
    RUN_TEST(test_wrong_length_is_illegal_value);
    RUN_TEST(test_write_single_spec_example);
    RUN_TEST(test_write_last_and_out_of_range);
    RUN_TEST(test_unsupported_function);
    RUN_TEST(test_bad_crc_ignored);
    RUN_TEST(test_other_address_ignored);
    RUN_TEST(test_broadcast_write_executes_without_reply);
    RUN_TEST(test_broadcast_other_cases_silent);
    RUN_TEST(test_frame_length_limits);
    RUN_TEST(test_response_buffer_too_small);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
