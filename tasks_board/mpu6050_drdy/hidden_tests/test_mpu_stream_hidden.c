/* 隐藏测试：只有评测流程能读。按实际经过的时间算应有的组数。 */
#include "unity.h"
#include "esp_timer.h"
#include "hw_config.h"
#include "ff_test.h"
#include "mpu_stream.h"

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
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(s_bus));
}

static uint8_t reg(uint8_t r)
{
    uint8_t v = 0;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit_receive(s_probe, &r, 1, &v, 1, 50));
    return v;
}

static void check_rate(uint32_t hz)
{
    QueueHandle_t q = xQueueCreate(600, sizeof(mpu_sample_t));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_start(s_bus, hz, q));
    vTaskDelay(pdMS_TO_TICKS(100));
    xQueueReset(q);
    int64_t t0 = esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(2000));
    int got = (int)uxQueueMessagesWaiting(q);
    int64_t t1 = esp_timer_get_time();
    int want = (int)((t1 - t0) * (int64_t)hz / 1000000);
    ff_log("%lu Hz：2 秒收到 %d 组，应为约 %d 组；SMPLRT_DIV=%u", (unsigned long)hz, got, want, reg(0x19));
    TEST_ASSERT_INT_WITHIN(want / 20 + 1, want, got);                 /* ±5% */
    mpu_sample_t s;
    TEST_ASSERT_EQUAL(pdTRUE, xQueueReceive(q, &s, 0));
    TEST_ASSERT_TRUE(s.accel[0] != 0 || s.accel[1] != 0 || s.accel[2] != 0);
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_stop());
    vQueueDelete(q);
}

TEST_CASE("100 Hz：2 秒约 200 组", "[mpu_stream]")
{
    setup();
    check_rate(100);
    teardown();
}

TEST_CASE("50 Hz：2 秒约 100 组", "[mpu_stream]")
{
    setup();
    check_rate(50);
    teardown();
}

TEST_CASE("寄存器配置", "[mpu_stream]")
{
    setup();
    QueueHandle_t q = xQueueCreate(64, sizeof(mpu_sample_t));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_start(s_bus, 100, q));
    TEST_ASSERT_EQUAL(9, reg(0x19));                    /* 1000 / (1 + 9) = 100 Hz */
    TEST_ASSERT_EQUAL(1, reg(0x38) & 0x01);             /* DATA_RDY_EN */
    TEST_ASSERT_EQUAL(0, (reg(0x6B) >> 6) & 1);         /* 已唤醒 */
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_stop());
    vQueueDelete(q);
    teardown();
}

TEST_CASE("停止后不再有数据，可以再次启动", "[mpu_stream]")
{
    setup();
    QueueHandle_t q = xQueueCreate(600, sizeof(mpu_sample_t));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_start(s_bus, 100, q));
    vTaskDelay(pdMS_TO_TICKS(200));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_stop());
    int n = (int)uxQueueMessagesWaiting(q);
    vTaskDelay(pdMS_TO_TICKS(200));
    TEST_ASSERT_EQUAL(n, (int)uxQueueMessagesWaiting(q));
    TEST_ASSERT_EQUAL(0, reg(0x38) & 0x01);             /* 器件的中断输出已关 */
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_start(s_bus, 100, q));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, mpu_stream_start(s_bus, 100, q));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_stop());
    vQueueDelete(q);
    teardown();
}

TEST_CASE("队列满时丢弃并计数，不阻塞", "[mpu_stream]")
{
    setup();
    QueueHandle_t q = xQueueCreate(2, sizeof(mpu_sample_t));
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_start(s_bus, 100, q));
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t d = mpu_stream_dropped();
    ff_log("丢弃 %lu 组", (unsigned long)d);
    TEST_ASSERT_TRUE(d > 80);
    TEST_ASSERT_EQUAL(ESP_OK, mpu_stream_stop());
    vQueueDelete(q);
    teardown();
}

TEST_CASE("参数非法", "[mpu_stream]")
{
    setup();
    QueueHandle_t q = xQueueCreate(4, sizeof(mpu_sample_t));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, mpu_stream_start(s_bus, 5, q));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, mpu_stream_start(s_bus, 201, q));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, mpu_stream_start(s_bus, 100, NULL));
    vQueueDelete(q);
    teardown();
}
