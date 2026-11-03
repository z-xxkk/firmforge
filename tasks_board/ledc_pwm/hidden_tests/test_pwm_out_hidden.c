/* 隐藏测试：只有评测流程能读。FF_PIN_PWM_OUT 经回环线进 FF_PIN_PWM_IN：
   频率用 GPIO 上升沿中断计数测量（ESP32-C3 没有 PCNT，DECISIONS 第 87 条），占空比用均匀采样估计。 */
#include <stdatomic.h>
#include "unity.h"
#include "driver/gpio.h"
#include "esp_attr.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "pwm_out.h"

static atomic_uint s_edges;

static void IRAM_ATTR on_edge(void *arg)
{
    (void)arg;
    atomic_fetch_add_explicit(&s_edges, 1, memory_order_relaxed);
}

static uint32_t measure_hz(void)
{
    gpio_config_t io = {.pin_bit_mask = 1ULL << FF_PIN_PWM_IN, .mode = GPIO_MODE_INPUT, .intr_type = GPIO_INTR_POSEDGE};
    TEST_ASSERT_EQUAL(ESP_OK, gpio_config(&io));
    esp_err_t e = gpio_install_isr_service(0);
    TEST_ASSERT_TRUE(e == ESP_OK || e == ESP_ERR_INVALID_STATE);
    TEST_ASSERT_EQUAL(ESP_OK, gpio_isr_handler_add(FF_PIN_PWM_IN, on_edge, NULL));
    atomic_store(&s_edges, 0);
    int64_t t0 = esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(500));
    uint32_t n = atomic_load(&s_edges);
    int64_t t1 = esp_timer_get_time();
    gpio_isr_handler_remove(FF_PIN_PWM_IN);
    gpio_set_intr_type(FF_PIN_PWM_IN, GPIO_INTR_DISABLE);
    if (e == ESP_OK) {
        gpio_uninstall_isr_service();
    }
    return (uint32_t)((int64_t)n * 1000000 / (t1 - t0));
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
TEST_CASE("20 kHz 占空比 500‰（13 位分辨率已超出时钟能力）", "[pwm_out]") { check(20000, 500); }

TEST_CASE("100 kHz 能配置成功（分辨率必须降低）", "[pwm_out]")
{
    TEST_ASSERT_EQUAL(ESP_OK, pwm_init(100000, 500));
    pwm_deinit();
}

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
