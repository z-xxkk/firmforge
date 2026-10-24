/* w25q_drv 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "w25q.h"
#include "mock_hal_spi.h"

static w25q_t dev;
static uint8_t buf[2048];
static uint8_t pattern[2048];

void setUp(void)
{
    mock_reset(3);
    memset(&dev, 0xA5, sizeof dev);
    for (unsigned i = 0; i < sizeof pattern; i++) {
        pattern[i] = (uint8_t)(i * 7u + 3u);
    }
}

void tearDown(void) {}

static void init_ok(void)
{
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_init(&dev));
    mock_log_count = 0;
}

/* 检查日志：每次真正执行的页编程都不跨页，且前一条指令是写使能 */
static void assert_programs_well_formed(void)
{
    for (unsigned i = 0; i < mock_log_count; i++) {
        if (mock_log[i].cmd != 0x02) {
            continue;
        }
        TEST_ASSERT_FALSE_MESSAGE(mock_log[i].ignored, "页编程被芯片忽略");
        TEST_ASSERT_TRUE(i > 0);
        TEST_ASSERT_EQUAL_HEX8(0x06, mock_log[i - 1].cmd);
        uint32_t off = mock_log[i].addr & 0xFFu;
        TEST_ASSERT_TRUE_MESSAGE(off + mock_log[i].data_len <= 256u, "页编程跨页");
    }
}

static void test_init_accepts_both_device_ids(void)
{
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_init(&dev));
    TEST_ASSERT_EQUAL_HEX8(0x9F, mock_log[0].cmd);
    TEST_ASSERT_EQUAL_UINT(3, mock_log[0].rx_len);
    mock_set_id(0xEF, 0x70, 0x17);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_init(&dev));
}

static void test_init_rejects_other_ids(void)
{
    static const uint8_t bad[4][3] = {{0xEF, 0x40, 0x18}, {0xC2, 0x20, 0x17}, {0xEF, 0x60, 0x17}, {0xFF, 0xFF, 0xFF}};
    for (int i = 0; i < 4; i++) {
        mock_set_id(bad[i][0], bad[i][1], bad[i][2]);
        TEST_ASSERT_EQUAL_INT(W25Q_ERR_ID, w25q_init(&dev));
        mock_log_count = 0;
        TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(&dev, 0, buf, 4));
        TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
    }
}

static void test_init_bus_error(void)
{
    mock_fail_on(1);
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_BUS, w25q_init(&dev));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_erase_sector(&dev, 0));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_init(NULL));
}

static void test_read_single_transfer_big_endian_address(void)
{
    init_ok();
    memcpy(&mock_mem[0x123456], pattern, 10);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_read(&dev, 0x123456, buf, 10));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, buf, 10);
    TEST_ASSERT_EQUAL_UINT(1, mock_log_count);
    TEST_ASSERT_EQUAL_HEX8(0x03, mock_log[0].cmd);
    TEST_ASSERT_EQUAL_HEX32(0x123456, mock_log[0].addr);
    TEST_ASSERT_EQUAL_UINT(10, mock_log[0].rx_len);
}

static void test_read_zero_length_no_bus(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_read(&dev, 100, buf, 0));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

static void test_read_range_checks(void)
{
    init_ok();
    memcpy(&mock_mem[MOCK_CAPACITY - 4], pattern, 4);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_read(&dev, MOCK_CAPACITY - 4, buf, 4));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, buf, 4);
    mock_log_count = 0;
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(&dev, MOCK_CAPACITY - 4, buf, 8));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(&dev, MOCK_CAPACITY, buf, 1));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(&dev, 0xFFFFFFFFu, buf, 2));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

static void test_write_within_page(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x1000, pattern, 16));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x1000], 16);
    TEST_ASSERT_EQUAL_HEX8(0xFF, mock_mem[0x1010]);
    TEST_ASSERT_EQUAL_UINT(1, mock_count_cmd(0x02, true));
    assert_programs_well_formed();
}

static void test_write_across_pages_is_split(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x10F0, pattern, 300));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x10F0], 300);
    TEST_ASSERT_EQUAL_UINT(3, mock_count_cmd(0x02, true));      /* 16 + 256 + 28 */
    assert_programs_well_formed();
}

static void test_write_full_aligned_page_single_program(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x2000, pattern, 256));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x2000], 256);
    TEST_ASSERT_EQUAL_UINT(1, mock_count_cmd(0x02, true));
}

static void test_write_large_unaligned(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x7FF83, pattern, 2000));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x7FF83], 2000);
    TEST_ASSERT_EQUAL_HEX8(0xFF, mock_mem[0x7FF82]);
    TEST_ASSERT_EQUAL_HEX8(0xFF, mock_mem[0x7FF83 + 2000]);
    assert_programs_well_formed();
}

static void test_write_waits_until_not_busy(void)
{
    mock_reset(20);
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x30F8, pattern, 40));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x30F8], 40);
    TEST_ASSERT_EQUAL_UINT(0, mock_count_cmd(0x02, true) - mock_count_cmd(0x02, false));   /* 没有被忽略的编程 */
    TEST_ASSERT_TRUE(mock_count_cmd(0x05, true) >= 2u * 21u);
}

static void test_write_timeout_when_always_busy(void)
{
    mock_reset(W25Q_MAX_BUSY_POLLS + 10u);
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_TIMEOUT, w25q_write(&dev, 0, pattern, 4));
    TEST_ASSERT_EQUAL_UINT(1, mock_count_cmd(0x02, true));
}

static void test_write_does_not_erase(void)
{
    init_ok();
    mock_mem[0x500] = 0xF0;
    const uint8_t v = 0x0F;
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x500, &v, 1));
    TEST_ASSERT_EQUAL_HEX8(0x00, mock_mem[0x500]);              /* 1 → 0 只能清位 */
    TEST_ASSERT_EQUAL_UINT(0, mock_count_cmd(0x20, true));
}

static void test_write_zero_length_and_range(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0, pattern, 0));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_write(&dev, MOCK_CAPACITY - 1, pattern, 2));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

static void test_bus_error_mid_write_stops(void)
{
    init_ok();
    mock_fail_on(3);          /* init 用了 1 次；写使能是第 2 次，页编程是第 3 次 */
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_BUS, w25q_write(&dev, 0x10F0, pattern, 300));
    TEST_ASSERT_EQUAL_UINT(2, mock_log_count);
}

static void test_erase_sector(void)
{
    init_ok();
    memset(&mock_mem[0x3000], 0x00, 3 * 4096);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_erase_sector(&dev, 0x4000));
    for (unsigned i = 0; i < 4096; i++) {
        TEST_ASSERT_EQUAL_HEX8(0xFF, mock_mem[0x4000 + i]);
    }
    TEST_ASSERT_EQUAL_HEX8(0x00, mock_mem[0x3FFF]);
    TEST_ASSERT_EQUAL_HEX8(0x00, mock_mem[0x5000]);
    TEST_ASSERT_EQUAL_HEX8(0x06, mock_log[0].cmd);
    TEST_ASSERT_EQUAL_HEX8(0x20, mock_log[1].cmd);
    TEST_ASSERT_FALSE(mock_log[1].ignored);
    TEST_ASSERT_EQUAL_HEX32(0x4000, mock_log[1].addr);
    TEST_ASSERT_EQUAL_HEX8(0x05, mock_log[mock_log_count - 1].cmd);   /* 最后在等不忙 */
}

static void test_erase_then_write_after_busy(void)
{
    init_ok();
    memset(&mock_mem[0x6000], 0x00, 4096);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_erase_sector(&dev, 0x6000));
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_write(&dev, 0x6000, pattern, 64));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(pattern, &mock_mem[0x6000], 64);
}

static void test_erase_unaligned_or_out_of_range(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_erase_sector(&dev, 0x4001));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_erase_sector(&dev, 0x4800));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_erase_sector(&dev, MOCK_CAPACITY));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
    TEST_ASSERT_EQUAL_INT(W25Q_OK, w25q_erase_sector(&dev, MOCK_CAPACITY - 4096));
}

static void test_null_arguments(void)
{
    init_ok();
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(NULL, 0, buf, 1));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_read(&dev, 0, NULL, 1));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_write(NULL, 0, pattern, 1));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_write(&dev, 0, NULL, 1));
    TEST_ASSERT_EQUAL_INT(W25Q_ERR_PARAM, w25q_erase_sector(NULL, 0));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_accepts_both_device_ids);
    RUN_TEST(test_init_rejects_other_ids);
    RUN_TEST(test_init_bus_error);
    RUN_TEST(test_read_single_transfer_big_endian_address);
    RUN_TEST(test_read_zero_length_no_bus);
    RUN_TEST(test_read_range_checks);
    RUN_TEST(test_write_within_page);
    RUN_TEST(test_write_across_pages_is_split);
    RUN_TEST(test_write_full_aligned_page_single_program);
    RUN_TEST(test_write_large_unaligned);
    RUN_TEST(test_write_waits_until_not_busy);
    RUN_TEST(test_write_timeout_when_always_busy);
    RUN_TEST(test_write_does_not_erase);
    RUN_TEST(test_write_zero_length_and_range);
    RUN_TEST(test_bus_error_mid_write_stops);
    RUN_TEST(test_erase_sector);
    RUN_TEST(test_erase_then_write_after_busy);
    RUN_TEST(test_erase_unaligned_or_out_of_range);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
