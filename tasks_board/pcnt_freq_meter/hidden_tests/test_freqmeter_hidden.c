/* 隐藏测试：只有评测流程能读。测试用 LEDC 在 FF_PIN_PWM_OUT 输出已知频率，经回环线进 FF_PIN_PWM_IN。 */
#include <stdlib.h>
#include "unity.h"
#include "driver/ledc.h"
#include "hw_config.h"
#include "freqmeter.h"

static void gen_start(uint32_t hz)
{
    uint32_t res = ledc_find_suitable_duty_resolution(80000000, hz);
    if (res > 14) res = 14;
    ledc_timer_config_t t = {.speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = res, .timer_num = LEDC_TIMER_3,
                             .freq_hz = hz, .clk_cfg = LEDC_AUTO_CLK};
    TEST_ASSERT_EQUAL(ESP_OK, ledc_timer_config(&t));
    ledc_channel_config_t c = {.gpio_num = FF_PIN_PWM_OUT, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_7,
                               .timer_sel = LEDC_TIMER_3, .duty = (1u << res) / 2, .hpoint = 0};
    TEST_ASSERT_EQUAL(ESP_OK, ledc_channel_config(&c));
}

static void gen_stop(void)
{
    ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_7, 0);
}

static void expect_close(uint32_t want, uint32_t got)
{
    uint32_t tol = want / 100 + 1;           /* 1% */
    TEST_ASSERT_UINT32_WITHIN(tol, want, got);
}

static void check(uint32_t hz, uint32_t gate)
{
    uint32_t got = 0;
    gen_start(hz);
    TEST_ASSERT_EQUAL(ESP_OK, freqmeter_init());
    TEST_ASSERT_EQUAL(ESP_OK, freqmeter_measure(gate, &got));
    expect_close(hz, got);
    freqmeter_deinit();
    gen_stop();
}

TEST_CASE("1 kHz，闸门 100 ms 与 1000 ms", "[freqmeter]")      { check(1000, 100); check(1000, 1000); }
TEST_CASE("10 kHz，闸门 100 ms 与 1000 ms", "[freqmeter]")     { check(10000, 100); check(10000, 1000); }
TEST_CASE("100 kHz，闸门 1000 ms（计数超过 16 位）", "[freqmeter]") { check(100000, 1000); }
TEST_CASE("100 kHz，闸门 100 ms", "[freqmeter]")                 { check(100000, 100); }

TEST_CASE("闸门时间超范围或指针为空返回参数错误", "[freqmeter]")
{
    uint32_t hz;
    TEST_ASSERT_EQUAL(ESP_OK, freqmeter_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, freqmeter_measure(5, &hz));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, freqmeter_measure(1001, &hz));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, freqmeter_measure(100, NULL));
    freqmeter_deinit();
}
