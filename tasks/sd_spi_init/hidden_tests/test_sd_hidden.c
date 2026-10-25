/* sd_spi_init 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "mock_sd.h"
#include "sd_card.h"
#include "unity.h"

static sd_card_t card;
static uint8_t buf[512];

void setUp(void)
{
    memset(&card, 0xA5, sizeof card);
    memset(buf, 0, sizeof buf);
}

void tearDown(void) {}

static unsigned count_cmd(uint8_t index)
{
    unsigned n = 0;
    for (unsigned i = 0; i < mock_log_count; i++) {
        n += mock_log[i].index == index;
    }
    return n;
}

static const mock_cmd_t *find_cmd(uint8_t index)
{
    for (unsigned i = 0; i < mock_log_count; i++) {
        if (mock_log[i].index == index) {
            return &mock_log[i];
        }
    }
    return NULL;
}

static void assert_all_crc_ok(void)
{
    for (unsigned i = 0; i < mock_log_count; i++) {
        TEST_ASSERT_TRUE_MESSAGE(mock_log[i].crc_ok, "有命令的 CRC7 不对");
    }
}

static void test_crc7_reference_values(void)
{
    const uint8_t c0[5] = {0x40, 0, 0, 0, 0};
    const uint8_t c8[5] = {0x48, 0, 0, 0x01, 0xAA};
    TEST_ASSERT_EQUAL_HEX8(0x95, (uint8_t)((sd_crc7(c0, 5) << 1) | 1u));
    TEST_ASSERT_EQUAL_HEX8(0x87, (uint8_t)((sd_crc7(c8, 5) << 1) | 1u));
    TEST_ASSERT_EQUAL_HEX8(0x00, sd_crc7(NULL, 3));
}

static void test_init_v2_high_capacity(void)
{
    mock_card(CARD_V2_HC);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_V2_HC, sd_type(&card));
    TEST_ASSERT_TRUE(mock_clocks_before_first_select >= 10u);
    TEST_ASSERT_EQUAL_UINT8(0, mock_log[0].index);
    TEST_ASSERT_EQUAL_UINT8(8, mock_log[1].index);
    TEST_ASSERT_EQUAL_HEX32(0x000001AA, mock_log[1].arg);
    const mock_cmd_t *a41 = find_cmd(41);
    TEST_ASSERT_NOT_NULL(a41);
    TEST_ASSERT_EQUAL_HEX32(0x40000000, a41->arg);
    for (unsigned i = 0; i < mock_log_count; i++) {
        if (mock_log[i].index == 41) {
            TEST_ASSERT_EQUAL_UINT8_MESSAGE(55, mock_log[i - 1].index, "ACMD41 之前要先发 CMD55");
        }
    }
    TEST_ASSERT_EQUAL_UINT(4, count_cmd(41));                 /* 忙 3 次，第 4 次就绪 */
    TEST_ASSERT_EQUAL_UINT(1, count_cmd(58));
    TEST_ASSERT_EQUAL_UINT(0, count_cmd(16));                 /* 大容量卡不设块长度 */
    assert_all_crc_ok();
    TEST_ASSERT_EQUAL_UINT(0, mock_cmd_while_busy);
}

static void test_init_v2_standard_capacity(void)
{
    mock_card(CARD_V2_SC);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_V2_SC, sd_type(&card));
    const mock_cmd_t *c16 = find_cmd(16);
    TEST_ASSERT_NOT_NULL(c16);
    TEST_ASSERT_EQUAL_UINT32(512, c16->arg);
    assert_all_crc_ok();
}

static void test_init_v1_card(void)
{
    mock_card(CARD_V1);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_V1, sd_type(&card));
    TEST_ASSERT_EQUAL_HEX32(0x00000000, find_cmd(41)->arg);  /* 1.x 版卡不带 HCS */
    TEST_ASSERT_EQUAL_UINT(0, count_cmd(58));
    TEST_ASSERT_EQUAL_UINT(1, count_cmd(16));
    assert_all_crc_ok();
}

static void test_mmc_unsupported(void)
{
    mock_card(CARD_MMC);
    TEST_ASSERT_EQUAL_INT(SD_ERR_UNSUPPORTED, sd_init(&card));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_NONE, sd_type(&card));
    unsigned xfers = mock_xfers;
    TEST_ASSERT_EQUAL_INT(SD_ERR_PARAM, sd_read_block(&card, 0, buf));
    TEST_ASSERT_EQUAL_UINT(xfers, mock_xfers);
}

static void test_bad_voltage(void)
{
    mock_card(CARD_BAD_VOLTAGE);
    TEST_ASSERT_EQUAL_INT(SD_ERR_VOLTAGE, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(0, count_cmd(41));
}

static void test_no_card(void)
{
    mock_card(CARD_NONE);
    TEST_ASSERT_EQUAL_INT(SD_ERR_NO_CARD, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(SD_CMD0_TRIES, count_cmd(0));
    TEST_ASSERT_EQUAL_UINT(SD_CMD0_TRIES, mock_log_count);
}

static void test_cmd0_retry(void)
{
    mock_card(CARD_V2_HC);
    mock_set_cmd0_silent(3);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(4, count_cmd(0));
}

static void test_response_delays(void)
{
    for (unsigned d = 0; d <= 7; d++) {
        mock_card(CARD_V2_SC);
        mock_set_resp_delay(d);
        TEST_ASSERT_EQUAL_INT_MESSAGE(SD_OK, sd_init(&card), "回应晚到几个字节就判失败了");
        TEST_ASSERT_EQUAL_INT(SD_OK, sd_read_block(&card, 1, buf));
    }
    mock_card(CARD_V2_SC);
    mock_set_resp_delay(9);
    TEST_ASSERT_EQUAL_INT(SD_ERR_NO_CARD, sd_init(&card));
}

static void test_acmd41_slow_and_timeout(void)
{
    mock_card(CARD_V2_HC);
    mock_set_busy_acmd41(500);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(501, count_cmd(41));
    mock_card(CARD_V2_HC);
    mock_set_busy_acmd41(5000);
    TEST_ASSERT_EQUAL_INT(SD_ERR_TIMEOUT, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(SD_ACMD41_TRIES, count_cmd(41));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_NONE, sd_type(&card));
}

static void test_chip_select_around_each_command(void)
{
    mock_card(CARD_V2_SC);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_UINT(mock_log_count, mock_selects);      /* 每条命令选中一次 */
    TEST_ASSERT_FALSE(mock_selected);                           /* 结束后片选已释放 */
    TEST_ASSERT_TRUE(mock_bytes_while_deselected >= 10u + mock_log_count);   /* 每条命令后补 8 个时钟 */
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_read_block(&card, 2, buf));
    TEST_ASSERT_FALSE(mock_selected);
}

static void test_read_block_high_capacity_uses_block_number(void)
{
    mock_card(CARD_V2_HC);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_read_block(&card, 7, buf));
    TEST_ASSERT_EQUAL_UINT32(7, mock_log[mock_log_count - 1].arg);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(mock_block(7), buf, 512);
    TEST_ASSERT_TRUE(mock_log[mock_log_count - 1].crc_ok);
}

static void test_read_block_standard_capacity_uses_byte_address(void)
{
    card_kind_t kinds[2] = {CARD_V2_SC, CARD_V1};
    for (int k = 0; k < 2; k++) {
        mock_card(kinds[k]);
        TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
        TEST_ASSERT_EQUAL_INT(SD_OK, sd_read_block(&card, 5, buf));
        TEST_ASSERT_EQUAL_UINT32(5u * 512u, mock_log[mock_log_count - 1].arg);
        TEST_ASSERT_EQUAL_UINT8_ARRAY(mock_block(5), buf, 512);
    }
}

static void test_read_errors(void)
{
    mock_card(CARD_V2_HC);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    mock_set_error_token(0x08);
    TEST_ASSERT_EQUAL_INT(SD_ERR_READ, sd_read_block(&card, 1, buf));
    TEST_ASSERT_FALSE(mock_selected);
    mock_set_error_token(0);
    mock_set_token_delay(2000);                                  /* 一直等不到数据 */
    TEST_ASSERT_EQUAL_INT(SD_ERR_READ, sd_read_block(&card, 1, buf));
    TEST_ASSERT_FALSE(mock_selected);
    mock_set_token_delay(900);                                   /* 慢，但在上限之内 */
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_read_block(&card, 1, buf));
    TEST_ASSERT_EQUAL_INT(SD_ERR_CMD, sd_read_block(&card, 1000, buf));   /* 卡回地址越界 */
}

static void test_null_and_not_ready(void)
{
    TEST_ASSERT_EQUAL_INT(SD_ERR_PARAM, sd_init(NULL));
    TEST_ASSERT_EQUAL_INT(SD_TYPE_NONE, sd_type(NULL));
    mock_card(CARD_V2_HC);
    TEST_ASSERT_EQUAL_INT(SD_ERR_PARAM, sd_read_block(NULL, 0, buf));
    TEST_ASSERT_EQUAL_UINT(0, mock_xfers);
    TEST_ASSERT_EQUAL_INT(SD_OK, sd_init(&card));
    unsigned xfers = mock_xfers;
    TEST_ASSERT_EQUAL_INT(SD_ERR_PARAM, sd_read_block(&card, 0, NULL));
    TEST_ASSERT_EQUAL_UINT(xfers, mock_xfers);
    mock_card(CARD_NONE);
    TEST_ASSERT_EQUAL_INT(SD_ERR_NO_CARD, sd_init(&card));       /* 失败后变回未就绪 */
    TEST_ASSERT_EQUAL_INT(SD_ERR_PARAM, sd_read_block(&card, 0, buf));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc7_reference_values);
    RUN_TEST(test_init_v2_high_capacity);
    RUN_TEST(test_init_v2_standard_capacity);
    RUN_TEST(test_init_v1_card);
    RUN_TEST(test_mmc_unsupported);
    RUN_TEST(test_bad_voltage);
    RUN_TEST(test_no_card);
    RUN_TEST(test_cmd0_retry);
    RUN_TEST(test_response_delays);
    RUN_TEST(test_acmd41_slow_and_timeout);
    RUN_TEST(test_chip_select_around_each_command);
    RUN_TEST(test_read_block_high_capacity_uses_block_number);
    RUN_TEST(test_read_block_standard_capacity_uses_byte_address);
    RUN_TEST(test_read_errors);
    RUN_TEST(test_null_and_not_ready);
    return UNITY_END();
}
