/* 隐藏测试：只有评测流程能读。FF_PIN_ADC_DRIVE 经跳线接 FF_PIN_ADC_IN，测试输出高低电平当被测电压；
   独立核对：被测模块释放 ADC 以后，测试自己用同一套驱动读一次，两个读数要接近。 */
#include "unity.h"
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "ff_test.h"
#include "volt.h"

/* 实测（2026-11-15，本板，标准答案）：输出低 6 mV，输出高（3.3 V，超出 12 dB 档量程）3005 mV；两次独立读数一致 */
#define LOW_MAX_MV   150
#define HIGH_MIN_MV  2300
#define HIGH_MAX_MV  3400

static void drive(int level)
{
    gpio_reset_pin(FF_PIN_ADC_DRIVE);
    gpio_set_direction(FF_PIN_ADC_DRIVE, GPIO_MODE_OUTPUT);
    gpio_set_level(FF_PIN_ADC_DRIVE, level);
    vTaskDelay(pdMS_TO_TICKS(5));
}

static int reference_mv(void)
{
    adc_unit_t unit;
    adc_channel_t ch;
    TEST_ASSERT_EQUAL(ESP_OK, adc_oneshot_io_to_channel(FF_PIN_ADC_IN, &unit, &ch));
    adc_oneshot_unit_handle_t u;
    adc_oneshot_unit_init_cfg_t ucfg = {.unit_id = unit};
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, adc_oneshot_new_unit(&ucfg, &u), "测试自己建不了 ADC 单元：被测模块没释放干净");
    adc_oneshot_chan_cfg_t ccfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    TEST_ASSERT_EQUAL(ESP_OK, adc_oneshot_config_channel(u, ch, &ccfg));
    adc_cali_handle_t cali;
    adc_cali_curve_fitting_config_t kcfg = {.unit_id = unit, .chan = ch, .atten = ADC_ATTEN_DB_12,
                                            .bitwidth = ADC_BITWIDTH_DEFAULT};
    TEST_ASSERT_EQUAL(ESP_OK, adc_cali_create_scheme_curve_fitting(&kcfg, &cali));
    int sum = 0, v = 0;
    for (int i = 0; i < 16; i++) {
        TEST_ASSERT_EQUAL(ESP_OK, adc_oneshot_get_calibrated_result(u, cali, ch, &v));
        sum += v;
    }
    adc_cali_delete_scheme_curve_fitting(cali);
    adc_oneshot_del_unit(u);
    return sum / 16;
}

TEST_CASE("没有初始化时读返回 ESP_ERR_INVALID_STATE", "[volt]")
{
    int mv = 0;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, volt_read_mv(&mv));
    volt_deinit();
}

TEST_CASE("输入接地时读数接近 0 mV", "[volt]")
{
    int mv = -1;
    drive(0);
    TEST_ASSERT_EQUAL(ESP_OK, volt_init());
    TEST_ASSERT_EQUAL(ESP_OK, volt_read_mv(&mv));
    volt_deinit();
    ff_log("输入低：%d mV", mv);
    TEST_ASSERT_TRUE_MESSAGE(mv >= 0 && mv <= LOW_MAX_MV, "输入接地时读数不接近 0 mV");
}

TEST_CASE("输入接 3.3 V 时读数在 12 dB 档满量程附近（单位毫伏）", "[volt]")
{
    int mv = -1;
    drive(1);
    TEST_ASSERT_EQUAL(ESP_OK, volt_init());
    TEST_ASSERT_EQUAL(ESP_OK, volt_read_mv(&mv));
    volt_deinit();
    ff_log("输入高：%d mV", mv);
    TEST_ASSERT_TRUE_MESSAGE(mv >= HIGH_MIN_MV && mv <= HIGH_MAX_MV, "输入 3.3 V 时读数不在满量程附近：单位、衰减或校准不对");
}

TEST_CASE("和测试自己读的结果相差不超过 100 mV", "[volt]")
{
    int mv = -1;
    drive(1);
    TEST_ASSERT_EQUAL(ESP_OK, volt_init());
    TEST_ASSERT_EQUAL(ESP_OK, volt_read_mv(&mv));
    volt_deinit();
    int ref = reference_mv();
    ff_log("被测模块 %d mV，测试自己读 %d mV", mv, ref);
    TEST_ASSERT_INT_WITHIN(100, ref, mv);
}

TEST_CASE("重复初始化返回 ESP_ERR_INVALID_STATE，反初始化后可以再初始化，空指针返回 ESP_ERR_INVALID_ARG", "[volt]")
{
    int mv = 0;
    drive(0);
    TEST_ASSERT_EQUAL(ESP_OK, volt_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, volt_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, volt_read_mv(NULL));
    volt_deinit();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, volt_read_mv(&mv));
    TEST_ASSERT_EQUAL(ESP_OK, volt_init());
    TEST_ASSERT_EQUAL(ESP_OK, volt_read_mv(&mv));
    volt_deinit();
    reference_mv();                                      /* 释放干净了，测试自己能建 */
}
