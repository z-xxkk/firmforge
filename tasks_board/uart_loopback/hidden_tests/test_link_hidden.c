/* 隐藏测试：只有评测流程能读。TX（GPIO10）经回环线接 RX（GPIO11）。 */
#include <string.h>
#include "unity.h"
#include "esp_timer.h"
#include "ff_test.h"
#include "link.h"

#define UART1_CLKDIV_REG 0x60010014u     /* SVD：UART1 基址 0x60010000 + CLKDIV 偏移 0x14 */

TEST_CASE("回环发送一行能原样读回", "[link]")
{
    char buf[64];
    TEST_ASSERT_EQUAL(ESP_OK, link_init(115200));
    TEST_ASSERT_EQUAL(6, link_write((const uint8_t *)"hello\n", 6));
    TEST_ASSERT_EQUAL(5, link_read_line(buf, sizeof buf, 500));
    TEST_ASSERT_EQUAL_STRING("hello", buf);
    link_deinit();
}

TEST_CASE("没有数据时按毫秒超时", "[link]")
{
    char buf[16];
    TEST_ASSERT_EQUAL(ESP_OK, link_init(115200));
    int64_t t0 = esp_timer_get_time();
    TEST_ASSERT_EQUAL(-1, link_read_line(buf, sizeof buf, 200));
    int64_t ms = (esp_timer_get_time() - t0) / 1000;
    TEST_ASSERT_TRUE_MESSAGE(ms >= 190 && ms <= 320, "200 ms 超时的实际等待时间不对");
    link_deinit();
}

TEST_CASE("超长行截断并丢掉剩余部分", "[link]")
{
    char buf[6];
    TEST_ASSERT_EQUAL(ESP_OK, link_init(115200));
    link_write((const uint8_t *)"abcdefghij\nxy\n", 14);
    TEST_ASSERT_EQUAL(5, link_read_line(buf, sizeof buf, 500));
    TEST_ASSERT_EQUAL_STRING("abcde", buf);
    TEST_ASSERT_EQUAL(2, link_read_line(buf, sizeof buf, 500));
    TEST_ASSERT_EQUAL_STRING("xy", buf);
    link_deinit();
}

TEST_CASE("波特率分频寄存器与 115200 相符", "[link]")
{
    TEST_ASSERT_EQUAL(ESP_OK, link_init(115200));
    uint32_t div = ff_reg_read(UART1_CLKDIV_REG) & 0xFFF;          /* 整数部分 */
    ff_log("UART1 CLKDIV 整数部分 = %lu", (unsigned long)div);
    TEST_ASSERT_UINT32_WITHIN(2, 80000000u / 115200u, div);        /* 时钟源 80 MHz（默认 APB） */
    link_deinit();
}

TEST_CASE("参数非法与反复初始化", "[link]")
{
    char buf[8];
    TEST_ASSERT_EQUAL(ESP_OK, link_init(115200));
    TEST_ASSERT_EQUAL(-1, link_read_line(NULL, 8, 10));
    TEST_ASSERT_EQUAL(-1, link_read_line(buf, 0, 10));
    link_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, link_init(9600));
    link_write((const uint8_t *)"ok\n", 3);
    TEST_ASSERT_EQUAL(2, link_read_line(buf, sizeof buf, 500));
    link_deinit();
}
