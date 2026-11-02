/* 隐藏测试：只有评测流程能读。测试在同一总线上挂自己的设备，独立读寄存器，不依赖被测代码自己报告的值。
   前提：板子静止放置。 */
#include <math.h>
#include <stdlib.h>
#include "unity.h"
#include "hw_config.h"
#include "ff_test.h"
#include "mpu6050.h"

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_probe;

static void setup(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = FF_PIN_I2C0_SDA, .scl_io_num = FF_PIN_I2C0_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    TEST_ASSERT_EQUAL(ESP_OK, i2c_new_master_bus(&cfg, &s_bus));
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_IMU_ADDR, .scl_speed_hz = 100000};
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_bus_add_device(s_bus, &dc, &s_probe));
}

static void teardown(void)
{
    i2c_master_bus_rm_device(s_probe);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(s_bus));       /* 驱动只删了自己的设备，总线还能正常删除 */
}

static uint8_t reg(uint8_t r)
{
    uint8_t v = 0;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit_receive(s_probe, &r, 1, &v, 1, 50));
    return v;
}

TEST_CASE("初始化后器件已唤醒，量程和低通配置正确", "[mpu6050]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, mpu_init(s_bus, MPU_ACCEL_8G, MPU_GYRO_1000));
    uint8_t pwr = reg(0x6B);
    ff_log("PWR_MGMT_1=0x%02x CONFIG=0x%02x ACCEL=0x%02x GYRO=0x%02x", pwr, reg(0x1A), reg(0x1C), reg(0x1B));
    TEST_ASSERT_EQUAL(0, (pwr >> 6) & 1);               /* SLEEP = 0 */
    TEST_ASSERT_EQUAL(1, pwr & 0x07);                   /* CLKSEL = 1 */
    TEST_ASSERT_EQUAL(3, reg(0x1A) & 0x07);             /* DLPF_CFG = 3 */
    TEST_ASSERT_EQUAL_HEX8(0x10, reg(0x1C) & 0x18);     /* AFS_SEL = 2（±8 g）在 bit4–3 */
    TEST_ASSERT_EQUAL_HEX8(0x10, reg(0x1B) & 0x18);     /* FS_SEL = 2（±1000 °/s） */
    mpu_deinit();
    teardown();
}

TEST_CASE("静止时加速度约 1 g、角速度接近 0、温度合理", "[mpu6050]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, mpu_init(s_bus, MPU_ACCEL_8G, MPU_GYRO_1000));
    mpu_data_t d;
    TEST_ASSERT_EQUAL(ESP_OK, mpu_read(&d));
    float g = sqrtf(d.accel_g[0] * d.accel_g[0] + d.accel_g[1] * d.accel_g[1] + d.accel_g[2] * d.accel_g[2]);
    ff_log("|a|=%.3f g  gyro=(%.2f, %.2f, %.2f) dps  temp=%.2f C", g, d.gyro_dps[0], d.gyro_dps[1], d.gyro_dps[2], d.temp_c);
    TEST_ASSERT_FLOAT_WITHIN(0.15f, 1.0f, g);
    for (int i = 0; i < 3; i++) {
        TEST_ASSERT_FLOAT_WITHIN(10.0f, 0.0f, d.gyro_dps[i]);
    }
    TEST_ASSERT_TRUE(d.temp_c > 0.0f && d.temp_c < 60.0f);
    mpu_deinit();
    teardown();
}

TEST_CASE("原始值与测试自己连续读的结果一致", "[mpu6050]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, mpu_init(s_bus, MPU_ACCEL_2G, MPU_GYRO_250));
    mpu_raw_t r;
    TEST_ASSERT_EQUAL(ESP_OK, mpu_read_raw(&r));
    uint8_t start = 0x3B, b[14];
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit_receive(s_probe, &start, 1, b, sizeof b, 50));
    for (int i = 0; i < 3; i++) {
        int16_t mine = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
        TEST_ASSERT_INT_WITHIN(800, mine, r.accel[i]);              /* 两次读之间的噪声 */
    }
    mpu_deinit();
    teardown();
}

TEST_CASE("参数非法与反复初始化", "[mpu6050]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, mpu_init(s_bus, (mpu_accel_range_t)4, MPU_GYRO_250));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, mpu_init(NULL, MPU_ACCEL_2G, MPU_GYRO_250));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_init(s_bus, MPU_ACCEL_2G, MPU_GYRO_250));
    mpu_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, mpu_init(s_bus, MPU_ACCEL_2G, MPU_GYRO_250));
    mpu_deinit();
    teardown();
}
