/* crc16_modbus 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "crc16_modbus.h"

void setUp(void) {}
void tearDown(void) {}

/* 逐位参考计算，用于对照 */
static uint16_t ref_crc(const uint8_t *d, size_t n)
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

static void test_check_value_123456789(void)
{
    const uint8_t s[] = "123456789";
    TEST_ASSERT_EQUAL_HEX16(0x4B37, crc16_modbus(s, 9));
}

static void test_zero_len_returns_ffff(void)
{
    const uint8_t s[] = {0x12, 0x34};
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16_modbus(s, 0));
}

static void test_null_data_returns_ffff(void)
{
    TEST_ASSERT_EQUAL_HEX16(0xFFFF, crc16_modbus(NULL, 5));
}

static void test_single_zero_byte(void)
{
    const uint8_t s[] = {0x00};
    TEST_ASSERT_EQUAL_HEX16(0x40BF, crc16_modbus(s, 1));
}

static void test_known_read_holding_frame(void)
{
    const uint8_t f[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x0A};
    TEST_ASSERT_EQUAL_HEX16(0xCDC5, crc16_modbus(f, sizeof f));
}

static void test_all_byte_values(void)
{
    uint8_t buf[256];
    for (int i = 0; i < 256; i++) {
        buf[i] = (uint8_t)i;
    }
    TEST_ASSERT_EQUAL_HEX16(0xDE6C, crc16_modbus(buf, sizeof buf));
}

static void test_matches_bitwise_reference_for_prefixes(void)
{
    uint8_t buf[64];
    for (size_t i = 0; i < sizeof buf; i++) {
        buf[i] = (uint8_t)(i * 37u + 11u);
    }
    for (size_t n = 1; n <= sizeof buf; n++) {
        TEST_ASSERT_EQUAL_HEX16(ref_crc(buf, n), crc16_modbus(buf, n));
    }
}

static void test_append_low_byte_first(void)
{
    uint8_t f[8] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0, 0};
    crc16_modbus_append(f, 6);
    TEST_ASSERT_EQUAL_HEX8(0xC5, f[6]);
    TEST_ASSERT_EQUAL_HEX8(0xCD, f[7]);
}

static void test_append_touches_only_two_bytes(void)
{
    uint8_t f[10];
    uint8_t before[10];
    memset(f, 0xEE, sizeof f);
    f[0] = 0x11; f[1] = 0x06; f[2] = 0x00; f[3] = 0x01;
    memcpy(before, f, sizeof f);
    crc16_modbus_append(f, 4);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(before, f, 4);
    TEST_ASSERT_EQUAL_HEX8(0xEE, f[6]);
    TEST_ASSERT_EQUAL_HEX8(0xEE, f[7]);
}

static void test_append_null_does_nothing(void)
{
    crc16_modbus_append(NULL, 4);   /* 不能崩溃 */
    TEST_PASS();
}

static void test_check_valid_frame(void)
{
    const uint8_t f[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0xC5, 0xCD};
    TEST_ASSERT_TRUE(crc16_modbus_check(f, sizeof f));
}

static void test_check_corrupted_payload(void)
{
    const uint8_t f[] = {0x01, 0x03, 0x00, 0x01, 0x00, 0x0A, 0xC5, 0xCD};
    TEST_ASSERT_FALSE(crc16_modbus_check(f, sizeof f));
}

static void test_check_swapped_crc_bytes(void)
{
    const uint8_t f[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0xCD, 0xC5};
    TEST_ASSERT_FALSE(crc16_modbus_check(f, sizeof f));
}

static void test_check_null_and_short(void)
{
    const uint8_t f[] = {0x01, 0xBF, 0x40};
    TEST_ASSERT_FALSE(crc16_modbus_check(NULL, 8));
    TEST_ASSERT_FALSE(crc16_modbus_check(f, 0));
    TEST_ASSERT_FALSE(crc16_modbus_check(f, 1));
    TEST_ASSERT_FALSE(crc16_modbus_check(f, 2));
}

static void test_check_minimum_length_three(void)
{
    uint8_t f[3] = {0x00, 0, 0};
    crc16_modbus_append(f, 1);
    TEST_ASSERT_TRUE(crc16_modbus_check(f, 3));
}

static void test_append_then_check_roundtrip(void)
{
    uint8_t f[40];
    for (size_t n = 1; n <= 38; n++) {
        for (size_t i = 0; i < n; i++) {
            f[i] = (uint8_t)(n * 13u + i * 7u);
        }
        crc16_modbus_append(f, n);
        TEST_ASSERT_TRUE(crc16_modbus_check(f, n + 2));
        f[n / 2] ^= 0x01u;
        TEST_ASSERT_FALSE(crc16_modbus_check(f, n + 2));
    }
}

static void test_crc_over_frame_with_crc_is_zero(void)
{
    uint8_t f[8] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0, 0};
    crc16_modbus_append(f, 6);
    TEST_ASSERT_EQUAL_HEX16(0x0000, crc16_modbus(f, 8));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_check_value_123456789);
    RUN_TEST(test_zero_len_returns_ffff);
    RUN_TEST(test_null_data_returns_ffff);
    RUN_TEST(test_single_zero_byte);
    RUN_TEST(test_known_read_holding_frame);
    RUN_TEST(test_all_byte_values);
    RUN_TEST(test_matches_bitwise_reference_for_prefixes);
    RUN_TEST(test_append_low_byte_first);
    RUN_TEST(test_append_touches_only_two_bytes);
    RUN_TEST(test_append_null_does_nothing);
    RUN_TEST(test_check_valid_frame);
    RUN_TEST(test_check_corrupted_payload);
    RUN_TEST(test_check_swapped_crc_bytes);
    RUN_TEST(test_check_null_and_short);
    RUN_TEST(test_check_minimum_length_three);
    RUN_TEST(test_append_then_check_roundtrip);
    RUN_TEST(test_crc_over_frame_with_crc_is_zero);
    return UNITY_END();
}
