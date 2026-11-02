/* 隐藏测试：只有评测流程能读（CLAUDE.md 硬性规则 5）。从 FF_PIN_PWM_OUT 打脉冲，经回环线进 FF_PIN_PWM_IN。 */
#include "unity.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "ff_test.h"
#include "edge_counter.h"

static void out_init(void)
{
    gpio_config_t io = {.pin_bit_mask = 1ULL << FF_PIN_PWM_OUT, .mode = GPIO_MODE_OUTPUT};
    TEST_ASSERT_EQUAL(ESP_OK, gpio_config(&io));
    gpio_set_level(FF_PIN_PWM_OUT, 0);
    vTaskDelay(pdMS_TO_TICKS(5));
}

static void pulses(int n, int half_period_us)
{
    for (int i = 0; i < n; i++) {
        gpio_set_level(FF_PIN_PWM_OUT, 1);
        esp_rom_delay_us(half_period_us);
        gpio_set_level(FF_PIN_PWM_OUT, 0);
        esp_rom_delay_us(half_period_us);
    }
    vTaskDelay(pdMS_TO_TICKS(5));
}

TEST_CASE("1 kHz 下 1000 个脉冲正好数出 1000", "[edge_counter]")
{
    out_init();
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    TEST_ASSERT_EQUAL_UINT32(0, edge_counter_get());
    pulses(1000, 500);
    TEST_ASSERT_EQUAL_UINT32(1000, edge_counter_get());
    edge_counter_deinit();
}

TEST_CASE("10 kHz 下 2000 个脉冲不丢", "[edge_counter]")
{
    out_init();
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    pulses(2000, 50);
    TEST_ASSERT_EQUAL_UINT32(2000, edge_counter_get());
    edge_counter_deinit();
}

TEST_CASE("清零后从 0 重新数", "[edge_counter]")
{
    out_init();
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    pulses(10, 500);
    edge_counter_reset();
    TEST_ASSERT_EQUAL_UINT32(0, edge_counter_get());
    pulses(7, 500);
    TEST_ASSERT_EQUAL_UINT32(7, edge_counter_get());
    edge_counter_deinit();
}

TEST_CASE("中断服务已被别人装过时初始化仍然成功", "[edge_counter]")
{
    out_init();
    esp_err_t pre = gpio_install_isr_service(0);
    TEST_ASSERT_TRUE(pre == ESP_OK || pre == ESP_ERR_INVALID_STATE);
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    pulses(5, 500);
    TEST_ASSERT_EQUAL_UINT32(5, edge_counter_get());
    edge_counter_deinit();
    if (pre == ESP_OK) {
        gpio_uninstall_isr_service();
    }
}

TEST_CASE("反初始化后可以再次初始化", "[edge_counter]")
{
    out_init();
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    edge_counter_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, edge_counter_init());
    pulses(3, 500);
    TEST_ASSERT_EQUAL_UINT32(3, edge_counter_get());
    edge_counter_deinit();
}
