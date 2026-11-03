/* 隐藏测试：只有评测流程能读。测试用 LEDC 在 FF_PIN_PWM_OUT 输出已知频率和占空比，经回环线进 FF_PIN_PWM_IN。 */
#include "unity.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include "hw_config.h"
#include "ff_test.h"
#include "pulse_meter.h"

static void gen_start(uint32_t hz, uint32_t permille)
{
    uint32_t res = ledc_find_suitable_duty_resolution(80000000, hz);
    if (res > 14) res = 14;
    ledc_timer_config_t t = {.speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = res, .timer_num = LEDC_TIMER_3,
                             .freq_hz = hz, .clk_cfg = LEDC_AUTO_CLK};
    TEST_ASSERT_EQUAL(ESP_OK, ledc_timer_config(&t));
    ledc_channel_config_t c = {.gpio_num = FF_PIN_PWM_OUT, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_5,
                               .timer_sel = LEDC_TIMER_3, .duty = (uint32_t)(((uint64_t)permille << res) / 1000), .hpoint = 0};
    TEST_ASSERT_EQUAL(ESP_OK, ledc_channel_config(&c));
}

static void gen_stop(void)
{
    ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_5, 0);
}

static void check(uint32_t hz, uint32_t permille)
{
    pulse_stat_t st = {0};
    gen_start(hz, permille);
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_init());
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_measure(500, &st));
    ff_log("输入 %lu Hz / %lu‰，测得 %lu Hz / %lu‰（%lu 个周期）", (unsigned long)hz, (unsigned long)permille,
           (unsigned long)st.hz, (unsigned long)st.duty_permille, (unsigned long)st.periods);
    TEST_ASSERT_UINT32_WITHIN(hz / 100 + 1, hz, st.hz);
    TEST_ASSERT_UINT32_WITHIN(20, permille, st.duty_permille);
    TEST_ASSERT_TRUE(st.periods >= 32);
    pulse_meter_deinit();
    gen_stop();
}

TEST_CASE("1 kHz 占空比 250‰", "[pulse_meter]")   { check(1000, 250); }
TEST_CASE("10 kHz 占空比 500‰", "[pulse_meter]")  { check(10000, 500); }
TEST_CASE("100 kHz 占空比 750‰", "[pulse_meter]") { check(100000, 750); }

TEST_CASE("没有信号时按时返回超时", "[pulse_meter]")
{
    gpio_config_t io = {.pin_bit_mask = 1ULL << FF_PIN_PWM_OUT, .mode = GPIO_MODE_OUTPUT};
    TEST_ASSERT_EQUAL(ESP_OK, gpio_config(&io));
    gpio_set_level(FF_PIN_PWM_OUT, 0);
    pulse_stat_t st;
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_init());
    int64_t t0 = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_ERR_TIMEOUT, pulse_meter_measure(200, &st));
    int64_t ms = (esp_timer_get_time() - t0) / 1000;
    TEST_ASSERT_TRUE_MESSAGE(ms >= 190 && ms <= 320, "200 ms 超时的实际等待时间不对");
    pulse_meter_deinit();
}

TEST_CASE("连续测量两次、反初始化后再初始化", "[pulse_meter]")
{
    pulse_stat_t st;
    gen_start(5000, 500);
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_init());
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_measure(500, &st));
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_measure(500, &st));
    TEST_ASSERT_UINT32_WITHIN(51, 5000, st.hz);
    pulse_meter_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_init());
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_measure(500, &st));
    pulse_meter_deinit();
    gen_stop();
}

TEST_CASE("参数非法", "[pulse_meter]")
{
    pulse_stat_t st;
    TEST_ASSERT_EQUAL(ESP_OK, pulse_meter_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, pulse_meter_measure(100, NULL));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, pulse_meter_measure(0, &st));
    pulse_meter_deinit();
}
