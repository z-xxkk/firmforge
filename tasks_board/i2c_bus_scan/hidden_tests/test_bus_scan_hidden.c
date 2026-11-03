/* 隐藏测试：只有评测流程能读。总线上接着 MAX30102（0x57）和 MPU6050（0x68）。 */
#include <string.h>
#include "unity.h"
#include "esp_timer.h"
#include "hw_config.h"
#include "ff_test.h"
#include "bus_scan.h"

#include "sdkconfig.h"
/* I2C0 控制寄存器（CTR 偏移 0x4，MS_MODE 在 bit4），地址按芯片取自各自的 SVD */
#if CONFIG_IDF_TARGET_ESP32
#define I2C0_CTR_REG 0x3FF53004u
#else
#define I2C0_CTR_REG 0x60013004u
#endif

static i2c_master_bus_handle_t bus_new(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = FF_PIN_I2C0_SDA, .scl_io_num = FF_PIN_I2C0_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_new_master_bus(&cfg, &bus));
    return bus;
}

TEST_CASE("扫描结果正好是 0x57 和 0x68", "[bus_scan]")
{
    i2c_master_bus_handle_t bus = bus_new();
    uint8_t found[8] = {0};
    size_t n = 0;
    int64_t t0 = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, bus_scan(bus, found, sizeof found, &n));
    int64_t ms = (esp_timer_get_time() - t0) / 1000;
    ff_log("扫描用时 %lld ms", (long long)ms);
    TEST_ASSERT_EQUAL_UINT32(2, n);
    TEST_ASSERT_EQUAL_HEX8(0x57, found[0]);
    TEST_ASSERT_EQUAL_HEX8(0x68, found[1]);
    TEST_ASSERT_TRUE_MESSAGE(ms < 2000, "扫描超过 2 秒");
    TEST_ASSERT_EQUAL(1, (ff_reg_read(I2C0_CTR_REG) >> 4) & 1);         /* 主机模式 */
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));                 /* 总线仍属于调用方，能正常删除 */
}

TEST_CASE("cap 小于实际个数：n 是实际个数，不越界写", "[bus_scan]")
{
    i2c_master_bus_handle_t bus = bus_new();
    uint8_t buf[4];
    memset(buf, 0xAA, sizeof buf);
    size_t n = 0;
    TEST_ASSERT_EQUAL(ESP_OK, bus_scan(bus, buf, 1, &n));
    TEST_ASSERT_EQUAL_UINT32(2, n);
    TEST_ASSERT_EQUAL_HEX8(0x57, buf[0]);
    TEST_ASSERT_EQUAL_HEX8(0xAA, buf[1]);          /* 哨兵字节没被改 */
    TEST_ASSERT_EQUAL_HEX8(0xAA, buf[2]);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("只要个数时 cap 可以为 0", "[bus_scan]")
{
    i2c_master_bus_handle_t bus = bus_new();
    size_t n = 0;
    TEST_ASSERT_EQUAL(ESP_OK, bus_scan(bus, NULL, 0, &n));
    TEST_ASSERT_EQUAL_UINT32(2, n);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("参数非法", "[bus_scan]")
{
    i2c_master_bus_handle_t bus = bus_new();
    uint8_t f[2];
    size_t n;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, bus_scan(NULL, f, 2, &n));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, bus_scan(bus, f, 2, NULL));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, bus_scan(bus, NULL, 2, &n));
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}
