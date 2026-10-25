/* onewire_ds18b20 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "mock_onewire.h"
#include "onewire_ds18b20.h"
#include "unity.h"

static ow_rom_t found[16];
static size_t nfound;

void setUp(void)
{
    mock_reset_bus();
    memset(found, 0xA5, sizeof found);
    nfound = 999;
}

void tearDown(void) {}

static int add(uint8_t family, uint32_t lo, uint16_t hi)
{
    uint8_t rom[8];
    mock_make_rom(family, lo, hi, rom);
    return mock_add_device(rom);
}

static ow_rom_t rom_of(uint8_t family, uint32_t lo, uint16_t hi)
{
    ow_rom_t r;
    mock_make_rom(family, lo, hi, r.rom);
    return r;
}

static bool contains(const ow_rom_t *list, size_t n, const ow_rom_t *r)
{
    for (size_t i = 0; i < n; i++) {
        if (memcmp(list[i].rom, r->rom, 8) == 0) {
            return true;
        }
    }
    return false;
}

static void test_crc8_reference_values(void)
{
    TEST_ASSERT_EQUAL_HEX8(0xA1, ow_crc8((const uint8_t *)"123456789", 9));
    ow_rom_t r = rom_of(0x28, 0x12345678u, 0x9ABC);
    TEST_ASSERT_EQUAL_HEX8(0x00, ow_crc8(r.rom, 8));            /* 连同 CRC 一起算结果为 0 */
    TEST_ASSERT_EQUAL_HEX8(0x00, ow_crc8(NULL, 0));
}

static void test_search_single_device(void)
{
    add(0x28, 0x00000001u, 0x0000);
    TEST_ASSERT_EQUAL_INT(OW_OK, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(1, nfound);
    ow_rom_t want = rom_of(0x28, 0x00000001u, 0x0000);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(want.rom, found[0].rom, 8);
    TEST_ASSERT_EQUAL_UINT(0, mock_protocol_errors);
    TEST_ASSERT_EQUAL_HEX8(0xF0, mock_last_rom_cmd);
}

static void check_search_finds(const ow_rom_t *want, size_t n)
{
    TEST_ASSERT_EQUAL_INT(OW_OK, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(n, nfound);
    for (size_t i = 0; i < n; i++) {
        TEST_ASSERT_TRUE_MESSAGE(contains(found, nfound, &want[i]), "漏找了设备");
        for (size_t j = i + 1; j < n; j++) {
            TEST_ASSERT_TRUE_MESSAGE(memcmp(found[i].rom, found[j].rom, 8) != 0, "同一个设备找到两次");
        }
    }
    TEST_ASSERT_EQUAL_UINT(0, mock_protocol_errors);
    TEST_ASSERT_EQUAL_UINT(n, mock_searches);                     /* 每个设备正好一轮搜索 */
}

static void test_search_three_devices(void)
{
    ow_rom_t want[3] = {rom_of(0x28, 0x11111111u, 0x0001), rom_of(0x28, 0x22222222u, 0x0002),
                        rom_of(0x28, 0x33333333u, 0x0003)};
    for (int i = 0; i < 3; i++) {
        mock_add_device(want[i].rom);
    }
    check_search_finds(want, 3);
}

static void test_search_eight_devices_differing_late(void)
{
    ow_rom_t want[8];
    for (int i = 0; i < 8; i++) {                                 /* 只在序列号的最高几位不同 */
        want[i] = rom_of(0x28, 0xCAFEBABEu, (uint16_t)(0x1000u * (unsigned)i + 0x0123u));
        mock_add_device(want[i].rom);
    }
    check_search_finds(want, 8);
}

static void test_search_mixed_families(void)
{
    ow_rom_t want[4] = {rom_of(0x28, 0x01020304u, 0x0506), rom_of(0x10, 0x01020304u, 0x0506),
                        rom_of(0x28, 0x01020305u, 0x0506), rom_of(0x22, 0xFFFFFFFFu, 0xFFFF)};
    for (int i = 0; i < 4; i++) {
        mock_add_device(want[i].rom);
    }
    check_search_finds(want, 4);
}

static void test_search_no_device(void)
{
    TEST_ASSERT_EQUAL_INT(OW_ERR_NO_DEVICE, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(0, nfound);
}

static void test_search_bad_rom_crc(void)
{
    uint8_t rom[8];
    mock_make_rom(0x28, 0x55AA55AAu, 0x1234, rom);
    rom[7] ^= 0x01u;
    mock_add_device(rom);
    TEST_ASSERT_EQUAL_INT(OW_ERR_CRC, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(0, nfound);
}

static void test_search_device_vanishes(void)
{
    add(0x28, 0x1u, 0);
    add(0x28, 0x2u, 0);
    mock_remove_all_at_search_bit(20);
    TEST_ASSERT_EQUAL_INT(OW_ERR_BUS, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(0, nfound);
}

static void test_search_array_full(void)
{
    ow_rom_t want[3] = {rom_of(0x28, 0xA0u, 0), rom_of(0x28, 0xB0u, 0), rom_of(0x28, 0xC0u, 0)};
    for (int i = 0; i < 3; i++) {
        mock_add_device(want[i].rom);
    }
    memset(found, 0xEE, sizeof found);
    TEST_ASSERT_EQUAL_INT(OW_MORE, ow_search_all(found, 2, &nfound));
    TEST_ASSERT_EQUAL_UINT(2, nfound);
    TEST_ASSERT_TRUE(contains(want, 3, &found[0]));
    TEST_ASSERT_TRUE(contains(want, 3, &found[1]));
    uint8_t untouched[8];
    memset(untouched, 0xEE, 8);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(untouched, found[2].rom, 8);     /* 不写数组之外 */
    TEST_ASSERT_EQUAL_INT(OW_OK, ow_search_all(found, 3, &nfound)); /* 正好放得下 */
    TEST_ASSERT_EQUAL_UINT(3, nfound);
}

static void test_convert_all(void)
{
    add(0x28, 1, 0);
    add(0x28, 2, 0);
    mock_set_convert_polls(50);
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_convert_all());
    TEST_ASSERT_EQUAL_HEX8(0xCC, mock_last_rom_cmd);
    TEST_ASSERT_EQUAL_HEX8(0x44, mock_last_function);
    TEST_ASSERT_EQUAL_UINT(1, mock_resets);
    TEST_ASSERT_EQUAL_UINT(0, mock_protocol_errors);
}

static void test_convert_timeout_and_no_device(void)
{
    TEST_ASSERT_EQUAL_INT(OW_ERR_NO_DEVICE, ds18b20_convert_all());
    add(0x28, 1, 0);
    mock_set_convert_polls(0xFFFFFFFFu);
    TEST_ASSERT_EQUAL_INT(OW_ERR_TIMEOUT, ds18b20_convert_all());
    TEST_ASSERT_TRUE(mock_bus_ops >= DS18B20_MAX_CONVERT_POLLS);
}

static int32_t read_after_convert(uint16_t raw, uint8_t config)
{
    mock_reset_bus();
    int d = add(0x28, 0x777u, 0x42);
    mock_set_temp(d, raw);
    mock_set_config(d, config);
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_convert_all());
    ow_rom_t r = rom_of(0x28, 0x777u, 0x42);
    int32_t t = 123456;
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_read_temp(&r, &t));
    TEST_ASSERT_EQUAL_HEX8(0x55, mock_last_rom_cmd);
    TEST_ASSERT_EQUAL_HEX8(0xBE, mock_last_function);
    TEST_ASSERT_EQUAL_UINT(0, mock_protocol_errors);
    return t;
}

static void test_temperature_table_12bit(void)
{
    /* 数据手册表 1 的全部例子；单位 0.01 °C，向零取整 */
    TEST_ASSERT_EQUAL_INT32(12500, read_after_convert(0x07D0, 0x7F));
    TEST_ASSERT_EQUAL_INT32(8500, read_after_convert(0x0550, 0x7F));
    TEST_ASSERT_EQUAL_INT32(2506, read_after_convert(0x0191, 0x7F));
    TEST_ASSERT_EQUAL_INT32(1012, read_after_convert(0x00A2, 0x7F));
    TEST_ASSERT_EQUAL_INT32(50, read_after_convert(0x0008, 0x7F));
    TEST_ASSERT_EQUAL_INT32(0, read_after_convert(0x0000, 0x7F));
    TEST_ASSERT_EQUAL_INT32(-50, read_after_convert(0xFFF8, 0x7F));
    TEST_ASSERT_EQUAL_INT32(-1012, read_after_convert(0xFF5E, 0x7F));
    TEST_ASSERT_EQUAL_INT32(-2506, read_after_convert(0xFE6F, 0x7F));
    TEST_ASSERT_EQUAL_INT32(-5500, read_after_convert(0xFC90, 0x7F));
}

static void test_lower_resolutions_mask_undefined_bits(void)
{
    TEST_ASSERT_EQUAL_INT32(2500, read_after_convert(0x0197, 0x1F));   /* 9 位：最低 3 位无意义 */
    TEST_ASSERT_EQUAL_INT32(2500, read_after_convert(0x0193, 0x3F));   /* 10 位：最低 2 位 */
    TEST_ASSERT_EQUAL_INT32(2512, read_after_convert(0x0193, 0x5F));   /* 11 位：最低 1 位 */
    TEST_ASSERT_EQUAL_INT32(-1050, read_after_convert(0xFF5F, 0x1F));  /* 负数：FF58h = −10.5 °C */
}

static void test_power_on_value_returned(void)
{
    int d = add(0x28, 0x9u, 0);
    (void)d;
    ow_rom_t r = rom_of(0x28, 0x9u, 0);
    int32_t t = 0;
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_read_temp(&r, &t));          /* 没转换过：上电值 85 °C */
    TEST_ASSERT_EQUAL_INT32(8500, t);
}

static void test_match_rom_selects_device(void)
{
    int a = add(0x28, 0xA1u, 0x1);
    int b = add(0x28, 0xB2u, 0x2);
    int c = add(0x28, 0xC3u, 0x3);
    mock_set_temp(a, 0x0010);       /* 1.00 °C */
    mock_set_temp(b, 0x0020);       /* 2.00 °C */
    mock_set_temp(c, 0xFFF0);       /* −1.00 °C */
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_convert_all());
    TEST_ASSERT_EQUAL_INT(OW_OK, ow_search_all(found, 16, &nfound));
    TEST_ASSERT_EQUAL_UINT(3, nfound);
    int32_t sum = 0;
    for (size_t i = 0; i < nfound; i++) {
        int32_t t = 0;
        TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_read_temp(&found[i], &t));
        sum += t;
    }
    TEST_ASSERT_EQUAL_INT32(200, sum);
    ow_rom_t rb = rom_of(0x28, 0xB2u, 0x2);
    int32_t t = 0;
    TEST_ASSERT_EQUAL_INT(OW_OK, ds18b20_read_temp(&rb, &t));
    TEST_ASSERT_EQUAL_INT32(200, t);
    TEST_ASSERT_EQUAL_UINT(0, mock_protocol_errors);
}

static void test_scratchpad_crc_error_keeps_output(void)
{
    int d = add(0x28, 0x5u, 0);
    mock_set_scratch_crc_error(d, true);
    ow_rom_t r = rom_of(0x28, 0x5u, 0);
    int32_t t = 4242;
    TEST_ASSERT_EQUAL_INT(OW_ERR_CRC, ds18b20_read_temp(&r, &t));
    TEST_ASSERT_EQUAL_INT32(4242, t);
}

static void test_not_ds18b20_family(void)
{
    add(0x10, 0x5u, 0);
    ow_rom_t r = rom_of(0x10, 0x5u, 0);
    int32_t t = 7;
    TEST_ASSERT_EQUAL_INT(OW_ERR_NOT_DS18B20, ds18b20_read_temp(&r, &t));
    TEST_ASSERT_EQUAL_UINT(0, mock_bus_ops + mock_resets);            /* 不碰总线 */
    TEST_ASSERT_EQUAL_INT32(7, t);
}

static void test_read_absent_bus(void)
{
    ow_rom_t r = rom_of(0x28, 0x5u, 0);
    int32_t t = 7;
    TEST_ASSERT_EQUAL_INT(OW_ERR_NO_DEVICE, ds18b20_read_temp(&r, &t));
    TEST_ASSERT_EQUAL_INT32(7, t);
}

static void test_null_arguments(void)
{
    add(0x28, 1, 0);
    ow_rom_t r = rom_of(0x28, 1, 0);
    int32_t t;
    TEST_ASSERT_EQUAL_INT(OW_ERR_PARAM, ow_search_all(NULL, 4, &nfound));
    TEST_ASSERT_EQUAL_INT(OW_ERR_PARAM, ow_search_all(found, 4, NULL));
    TEST_ASSERT_EQUAL_INT(OW_ERR_PARAM, ow_search_all(found, 0, &nfound));
    TEST_ASSERT_EQUAL_INT(OW_ERR_PARAM, ds18b20_read_temp(NULL, &t));
    TEST_ASSERT_EQUAL_INT(OW_ERR_PARAM, ds18b20_read_temp(&r, NULL));
    TEST_ASSERT_EQUAL_UINT(0, mock_bus_ops + mock_resets);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc8_reference_values);
    RUN_TEST(test_search_single_device);
    RUN_TEST(test_search_three_devices);
    RUN_TEST(test_search_eight_devices_differing_late);
    RUN_TEST(test_search_mixed_families);
    RUN_TEST(test_search_no_device);
    RUN_TEST(test_search_bad_rom_crc);
    RUN_TEST(test_search_device_vanishes);
    RUN_TEST(test_search_array_full);
    RUN_TEST(test_convert_all);
    RUN_TEST(test_convert_timeout_and_no_device);
    RUN_TEST(test_temperature_table_12bit);
    RUN_TEST(test_lower_resolutions_mask_undefined_bits);
    RUN_TEST(test_power_on_value_returned);
    RUN_TEST(test_match_rom_selects_device);
    RUN_TEST(test_scratchpad_crc_error_keeps_output);
    RUN_TEST(test_not_ds18b20_family);
    RUN_TEST(test_read_absent_bus);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
