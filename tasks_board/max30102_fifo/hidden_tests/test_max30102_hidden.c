/* 隐藏测试：只有评测流程能读。间隔不规则地读，让 FIFO 读写指针有机会回绕。 */
#include "unity.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "ff_test.h"
#include "max30102.h"

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_probe;

static void setup(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = FF_PIN_I2C0_SDA, .scl_io_num = FF_PIN_I2C0_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7, .flags.enable_internal_pullup = true,
    };
    TEST_ASSERT_EQUAL(ESP_OK, i2c_new_master_bus(&cfg, &s_bus));
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_PPG_ADDR, .scl_speed_hz = 100000};
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

TEST_CASE("寄存器配置符合要求", "[max30102]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, ppg_init(s_bus, 100));
    ff_log("MODE=0x%02x SPO2=0x%02x FIFO=0x%02x LED1=0x%02x LED2=0x%02x", reg(0x09), reg(0x0A), reg(0x08), reg(0x0C), reg(0x0D));
    TEST_ASSERT_EQUAL_HEX8(0x15, reg(0xFF));
    TEST_ASSERT_EQUAL_HEX8(0x03, reg(0x09) & 0x07);             /* SpO2 模式 */
    TEST_ASSERT_EQUAL_HEX8(0x27, reg(0x0A) & 0x7F);             /* 量程 01、100 sps（001）、LED_PW 11 */
    TEST_ASSERT_EQUAL_HEX8(0x10, reg(0x08) & 0xF0);             /* SMP_AVE = 0，回绕 */
    TEST_ASSERT_EQUAL_HEX8(0x24, reg(0x0C));
    TEST_ASSERT_EQUAL_HEX8(0x24, reg(0x0D));
    ppg_deinit();
    teardown();
}

TEST_CASE("100 sps：间隔不规则地读 3 秒，样本数正确、不丢不重", "[max30102]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, ppg_init(s_bus, 100));
    static ppg_sample_t buf[32];
    vTaskDelay(pdMS_TO_TICKS(50));
    while (ppg_read(buf, 32) > 0) {
    }
    int total = 0;
    const int waits_ms[] = {37, 210, 90, 150, 30, 240, 120, 60};
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; esp_timer_get_time() - t0 < 3000000; i++) {
        vTaskDelay(pdMS_TO_TICKS(waits_ms[i % 8]));
        int n = ppg_read(buf, 32);
        TEST_ASSERT_TRUE(n >= 0);
        for (int k = 0; k < n; k++) {
            TEST_ASSERT_TRUE(buf[k].red <= 0x3FFFF && buf[k].ir <= 0x3FFFF);
        }
        total += n;
    }
    int64_t t1 = esp_timer_get_time();
    int want = (int)((t1 - t0) / 10000);
    ff_log("3 秒取到 %d 个样本，应为约 %d 个", total, want);
    TEST_ASSERT_INT_WITHIN(want / 20 + 2, want, total);         /* ±5% */
    ppg_deinit();
    teardown();
}

TEST_CASE("cap 限制每次取的个数", "[max30102]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_OK, ppg_init(s_bus, 400));
    vTaskDelay(pdMS_TO_TICKS(50));
    ppg_sample_t two[2];
    TEST_ASSERT_EQUAL(2, ppg_read(two, 2));
    ppg_deinit();
    teardown();
}

TEST_CASE("参数非法与反复初始化", "[max30102]")
{
    setup();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, ppg_init(s_bus, 150));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, ppg_init(NULL, 100));
    TEST_ASSERT_EQUAL(-1, ppg_read(NULL, 4));
    TEST_ASSERT_EQUAL(ESP_OK, ppg_init(s_bus, 50));
    ppg_deinit();
    TEST_ASSERT_EQUAL(ESP_OK, ppg_init(s_bus, 200));
    ppg_deinit();
    teardown();
}
