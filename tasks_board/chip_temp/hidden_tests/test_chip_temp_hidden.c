/* 隐藏测试：只有评测流程能读。独立核对：被测模块释放传感器以后，测试自己装一个读，两个读数要接近。 */
#include <math.h>
#include "unity.h"
#include "driver/temperature_sensor.h"
#include "ff_test.h"
#include "chip_temp.h"

static float reference_read(void)
{
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    temperature_sensor_handle_t ts = NULL;
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, temperature_sensor_install(&cfg, &ts), "测试自己装不上传感器：被测模块没释放干净");
    TEST_ASSERT_EQUAL(ESP_OK, temperature_sensor_enable(ts));
    float sum = 0, t = 0;
    for (int i = 0; i < 5; i++) {
        TEST_ASSERT_EQUAL(ESP_OK, temperature_sensor_get_celsius(ts, &t));
        sum += t;
    }
    temperature_sensor_disable(ts);
    temperature_sensor_uninstall(ts);
    return sum / 5;
}

TEST_CASE("没有初始化时读返回 ESP_ERR_INVALID_STATE", "[chip_temp]")
{
    float t = 0;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, chip_temp_read(&t));
    chip_temp_deinit();                                 /* 没初始化时调用也不出错 */
}

TEST_CASE("读数是合理的摄氏度，和测试自己读的相差不超过 2 度", "[chip_temp]")
{
    float t = 0;
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_init());
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_read(&t));
    chip_temp_deinit();
    float ref = reference_read();
    ff_log("被测模块读到 %.2f °C，测试自己读到 %.2f °C", t, ref);
    TEST_ASSERT_TRUE_MESSAGE(t > 5 && t < 75, "温度不在合理范围（单位要是摄氏度）");
    TEST_ASSERT_FLOAT_WITHIN(2.0f, ref, t);
}

TEST_CASE("连续读 5 次读数稳定", "[chip_temp]")
{
    float lo = 1000, hi = -1000, t = 0;
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_init());
    for (int i = 0; i < 5; i++) {
        TEST_ASSERT_EQUAL(ESP_OK, chip_temp_read(&t));
        lo = fminf(lo, t);
        hi = fmaxf(hi, t);
    }
    chip_temp_deinit();
    ff_log("5 次读数 %.2f–%.2f °C", lo, hi);
    TEST_ASSERT_TRUE_MESSAGE(hi - lo < 2.0f, "连续读数波动超过 2 度");
}

TEST_CASE("重复初始化返回 ESP_ERR_INVALID_STATE，反初始化后可以再初始化", "[chip_temp]")
{
    float t = 0;
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, chip_temp_init());
    chip_temp_deinit();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, chip_temp_read(&t));
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_init());
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_read(&t));
    chip_temp_deinit();
    reference_read();                                   /* 释放干净了，测试自己能装上 */
}

TEST_CASE("空指针返回 ESP_ERR_INVALID_ARG", "[chip_temp]")
{
    TEST_ASSERT_EQUAL(ESP_OK, chip_temp_init());
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, chip_temp_read(NULL));
    chip_temp_deinit();
}
