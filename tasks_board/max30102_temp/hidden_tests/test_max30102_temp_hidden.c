/* 隐藏测试：只有评测流程能读。零下温度在室温板子上测不到，所以单独测换算函数。 */
#include <math.h>
#include "unity.h"
#include "hw_config.h"
#include "ff_test.h"
#include "max30102_temp.h"

static i2c_master_bus_handle_t bus_new(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = FF_PIN_I2C0_SDA, .scl_io_num = FF_PIN_I2C0_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_new_master_bus(&cfg, &bus));
    return bus;
}

TEST_CASE("换算：零下与小数部分", "[max30102_temp]")
{
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 25.0f, ppg_temp_convert(0x19, 0x0));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 25.5625f, ppg_temp_convert(0x19, 0x9));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -1.0f, ppg_temp_convert(0xFF, 0x0));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, -24.5f, ppg_temp_convert(0xE7, 0x8));
    TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.9375f, ppg_temp_convert(0x00, 0xF));
}

TEST_CASE("板上读出的温度合理，且是 1/16 °C 的整数倍", "[max30102_temp]")
{
    i2c_master_bus_handle_t bus = bus_new();
    float t = 0, t2 = 0;
    TEST_ASSERT_EQUAL(ESP_OK, ppg_temp_read(bus, &t));
    TEST_ASSERT_EQUAL(ESP_OK, ppg_temp_read(bus, &t2));          /* 连续读两次 */
    ff_log("温度 %.4f / %.4f C", t, t2);
    TEST_ASSERT_TRUE(t > 15.0f && t < 45.0f);
    TEST_ASSERT_FLOAT_WITHIN(0.001f, roundf(t * 16.0f), t * 16.0f);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("读温度不改动其他配置", "[max30102_temp]")
{
    i2c_master_bus_handle_t bus = bus_new();
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_PPG_ADDR, .scl_speed_hz = 100000};
    i2c_master_dev_handle_t dev;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_bus_add_device(bus, &dc, &dev));
    uint8_t set_led[2] = {0x0C, 0x5A}, r = 0x0C, v = 0;           /* LED1_PA 设成特殊值 */
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit(dev, set_led, 2, 50));
    float t;
    TEST_ASSERT_EQUAL(ESP_OK, ppg_temp_read(bus, &t));
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit_receive(dev, &r, 1, &v, 1, 50));
    TEST_ASSERT_EQUAL_HEX8(0x5A, v);                              /* 复位会把它清成 0 */
    i2c_master_bus_rm_device(dev);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("参数非法", "[max30102_temp]")
{
    float t;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, ppg_temp_read(NULL, &t));
    i2c_master_bus_handle_t bus = bus_new();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, ppg_temp_read(bus, NULL));
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}
