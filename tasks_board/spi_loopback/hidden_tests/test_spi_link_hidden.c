/* 隐藏测试：只有评测流程能读。MOSI 经跳线接 MISO：发什么收什么；用传输用时核对时钟频率。 */
#include <string.h>
#include "unity.h"
#include "esp_timer.h"
#include "ff_test.h"
#include "spi_link.h"

static uint8_t s_tx[256], s_rx[256];

static void fill(void)
{
    for (int i = 0; i < 256; i++) {
        s_tx[i] = (uint8_t)(i * 7 + 3);
    }
    memset(s_rx, 0, sizeof s_rx);
}

static int64_t timed_xfer(size_t len)
{
    int64_t t0 = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx, s_rx, len));
    return esp_timer_get_time() - t0;
}

TEST_CASE("没有初始化时收发返回 ESP_ERR_INVALID_STATE", "[spi_link]")
{
    uint8_t b = 0;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, spi_link_xfer(&b, NULL, 1));
    spi_link_deinit();
}

TEST_CASE("1 MHz 收发 256 字节原样收回", "[spi_link]")
{
    fill();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(1000000));
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx, s_rx, 256));
    spi_link_deinit();
    TEST_ASSERT_EQUAL_UINT8_ARRAY(s_tx, s_rx, 256);
}

TEST_CASE("短数据和只发不收", "[spi_link]")
{
    fill();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(2000000));
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx, s_rx, 1));
    TEST_ASSERT_EQUAL_UINT8(s_tx[0], s_rx[0]);
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx + 10, s_rx, 3));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(s_tx + 10, s_rx, 3);
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx, NULL, 16));
    spi_link_deinit();
}

TEST_CASE("时钟按参数：256 字节在 100 kHz 和 1 MHz 下的用时", "[spi_link]")
{
    fill();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(100000));
    int64_t slow = timed_xfer(256);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(s_tx, s_rx, 256);
    spi_link_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(1000000));
    int64_t fast = timed_xfer(256);
    spi_link_deinit();
    ff_log("256 字节：100 kHz 用时 %lld us，1 MHz 用时 %lld us", (long long)slow, (long long)fast);
    /* 理论值 20480 us 和 2048 us，加上驱动开销；实测（2026-11-15，本板，标准答案）20552 us 和 2092 us */
    TEST_ASSERT_TRUE_MESSAGE(slow >= 19000 && slow <= 26000, "100 kHz 下 256 字节用时不对：没按参数设时钟");
    TEST_ASSERT_TRUE_MESSAGE(fast >= 1900 && fast <= 4000, "1 MHz 下 256 字节用时不对");
}

TEST_CASE("非法参数和重复初始化，反初始化后可以再初始化", "[spi_link]")
{
    fill();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, spi_link_init(50000));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, spi_link_init(20000000));
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(1000000));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, spi_link_init(1000000));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, spi_link_xfer(s_tx, s_rx, 0));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, spi_link_xfer(s_tx, s_rx, 257));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, spi_link_xfer(NULL, s_rx, 4));
    spi_link_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_init(1000000));
    TEST_ASSERT_EQUAL(ESP_OK, spi_link_xfer(s_tx, s_rx, 8));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(s_tx, s_rx, 8);
    spi_link_deinit();
}
