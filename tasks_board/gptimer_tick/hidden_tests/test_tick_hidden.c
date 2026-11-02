/* 隐藏测试：只有评测流程能读。按实际经过的时间算应有的次数，避免 FreeRTOS 节拍（10 ms）带来的误差。 */
#include <stdlib.h>
#include "unity.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tick.h"

static void check_rate(uint32_t hz)
{
    TEST_ASSERT_EQUAL(ESP_OK, tick_start(hz));
    vTaskDelay(pdMS_TO_TICKS(50));
    uint32_t c0 = tick_count();
    int64_t t0 = esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t c1 = tick_count();
    int64_t t1 = esp_timer_get_time();
    int64_t want = (t1 - t0) * (int64_t)hz / 1000000;
    TEST_ASSERT_INT_WITHIN(2, (int)want, (int)(c1 - c0));
    TEST_ASSERT_EQUAL(ESP_OK, tick_stop());
}

TEST_CASE("1 kHz：1 秒内次数误差不超过 2", "[tick]")  { check_rate(1000); }
TEST_CASE("10 kHz：1 秒内次数误差不超过 2", "[tick]") { check_rate(10000); }
TEST_CASE("10 Hz：1 秒内次数误差不超过 2", "[tick]")  { check_rate(10); }

TEST_CASE("停止后计数不再增加，可以再次启动", "[tick]")
{
    TEST_ASSERT_EQUAL(ESP_OK, tick_start(1000));
    vTaskDelay(pdMS_TO_TICKS(100));
    TEST_ASSERT_EQUAL(ESP_OK, tick_stop());
    uint32_t c = tick_count();
    vTaskDelay(pdMS_TO_TICKS(100));
    TEST_ASSERT_EQUAL_UINT32(c, tick_count());
    TEST_ASSERT_EQUAL(ESP_OK, tick_start(100));
    TEST_ASSERT_EQUAL_UINT32(0, tick_count());
    TEST_ASSERT_EQUAL(ESP_OK, tick_stop());
}

TEST_CASE("参数与状态检查", "[tick]")
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, tick_start(0));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, tick_start(10001));
    TEST_ASSERT_EQUAL(ESP_OK, tick_start(100));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, tick_start(100));
    TEST_ASSERT_EQUAL(ESP_OK, tick_stop());
}
