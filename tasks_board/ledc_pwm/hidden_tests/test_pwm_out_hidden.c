/* 隐藏测试：只有评测流程能读。用 PCNT 在 FF_PIN_PWM_IN 测频率，用均匀采样估计高电平比例。 */
#include "unity.h"
#include "driver/gpio.h"
#include "driver/pulse_cnt.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "pwm_out.h"

static uint32_t measure_hz(void)
{
    pcnt_unit_handle_t u;
    pcnt_channel_handle_t ch;
    pcnt_unit_config_t ucfg = {.low_limit = -1, .high_limit = 30000, .flags.accum_count = 1};
    TEST_ASSERT_EQUAL(ESP_OK, pcnt_new_unit(&ucfg, &u));
    pcnt_chan_config_t ccfg = {.edge_gpio_num = FF_PIN_PWM_IN, .level_gpio_num = -1};
    TEST_ASSERT_EQUAL(ESP_OK, pcnt_new_channel(u, &ccfg, &ch));
    pcnt_channel_set_edge_action(ch, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_HOLD);
    pcnt_unit_add_watch_point(u, 30000);
    pcnt_unit_enable(u);
    pcnt_unit_clear_count(u);
    pcnt_unit_start(u);
    int64_t t0 = esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(500));
    int count = 0;
    pcnt_unit_get_count(u, &count);
    int64_t t1 = esp_timer_get_time();
    pcnt_unit_stop(u);
    pcnt_unit_disable(u);
    pcnt_del_channel(ch);
    pcnt_unit_remove_watch_point(u, 30000);
    pcnt_del_unit(u);
    return (uint32_t)((int64_t)count * 1000000 / (t1 - t0));
}

static uint32_t measure_duty_permille(void)
{
    gpio_set_direction(FF_PIN_PWM_IN, GPIO_MODE_INPUT);
    uint32_t high = 0, n = 40000;
    for (uint32_t i = 0; i < n; i++) {
        high += gpio_get_level(FF_PIN_PWM_IN);
        esp_rom_delay_us(1 + (i * 7) % 13);       /* 间隔不规则，避免和 PWM 周期同步 */
    }
    return high * 1000 / n;
}

static void check(uint32_t hz, uint32_t duty)
{
    TEST_ASSERT_EQUAL(ESP_OK, pwm_init(hz, duty));
    vTaskDelay(pdMS_TO_TICKS(20));
    TEST_ASSERT_UINT32_WITHIN(hz / 100 + 1, hz, measure_hz());
    TEST_ASSERT_UINT32_WITHIN(20, duty, measure_duty_permille());
    pwm_deinit();
}

TEST_CASE("1 kHz 占空比 250‰", "[pwm_out]")   { check(1000, 250); }
TEST_CASE("10 kHz 占空比 500‰", "[pwm_out]")  { check(10000, 500); }
TEST_CASE("100 kHz 占空比 500‰（分辨率必须降低）", "[pwm_out]") { check(100000, 500); }

TEST_CASE("改占空比立即生效", "[pwm_out]")
{
    TEST_ASSERT_EQUAL(ESP_OK, pwm_init(1000, 200));
    vTaskDelay(pdMS_TO_TICKS(20));
    TEST_ASSERT_EQUAL(ESP_OK, pwm_set_duty(800));
    vTaskDelay(pdMS_TO_TICKS(20));
    TEST_ASSERT_UINT32_WITHIN(20, 800, measure_duty_permille());
    pwm_deinit();
}

TEST_CASE("参数超范围返回参数错误", "[pwm_out]")
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, pwm_init(50, 500));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, pwm_init(200000, 500));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, pwm_init(1000, 1001));
}
