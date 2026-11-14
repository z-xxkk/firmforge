/* 隐藏测试：只有评测流程能读。TX 经回环线进 RX，测试从 UART1 读回帧并解析。前提：板子静止。 */
#include <math.h>
#include <string.h>
#include "unity.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "hw_config.h"
#include "ff_test.h"
#include "logger.h"

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

static uint16_t crc(const uint8_t *p, size_t n)
{
    uint16_t c = 0xFFFF;
    while (n--) {
        c ^= (uint16_t)(*p++) << 8;
        for (int i = 0; i < 8; i++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
    }
    return c;
}

static uint8_t s_rx[4096];

TEST_CASE("50 Hz：2 秒约 100 帧，CRC 全对，序号连续，加速度约 1 g", "[logger]")
{
    i2c_master_bus_handle_t bus = bus_new();
    TEST_ASSERT_EQUAL(ESP_OK, logger_start(bus, 50));
    vTaskDelay(pdMS_TO_TICKS(100));
    uart_flush_input(UART_NUM_1);
    int len = 0;
    int64_t t0 = esp_timer_get_time();
    while (esp_timer_get_time() - t0 < 2000000 && len < (int)sizeof s_rx) {
        int n = uart_read_bytes(UART_NUM_1, s_rx + len, sizeof s_rx - len, pdMS_TO_TICKS(20));
        if (n > 0) len += n;
    }
    int64_t t1 = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, logger_stop());
    int frames = 0, bad_crc = 0, seq_breaks = 0, last_seq = -1;
    float g = 0;
    int16_t last[3] = {0, 0, 0};
    for (int i = 0; i + 19 <= len;) {
        if (s_rx[i] != 0xA5 || s_rx[i + 1] != 0x5A) { i++; continue; }
        const uint8_t *f = &s_rx[i];
        uint16_t want = (uint16_t)(f[17] | (f[18] << 8));
        if (crc(&f[2], 15) != want) { bad_crc++; i++; continue; }
        if (last_seq >= 0 && f[2] != (uint8_t)(last_seq + 1)) seq_breaks++;
        last_seq = f[2];
        int16_t ax = (int16_t)(f[3] | (f[4] << 8)), ay = (int16_t)(f[5] | (f[6] << 8)), az = (int16_t)(f[7] | (f[8] << 8));
        g = sqrtf((float)ax * ax + (float)ay * ay + (float)az * az) / 16384.0f;
        last[0] = ax, last[1] = ay, last[2] = az;
        frames++;
        i += 19;
    }
    int want_frames = (int)((t1 - t0) * 50 / 1000000);
    ff_log("收到 %d 字节、%d 帧（应约 %d），CRC 错 %d，序号断 %d，|a|=%.3f g", len, frames, want_frames, bad_crc, seq_breaks, g);
    TEST_ASSERT_INT_WITHIN(want_frames / 20 + 2, want_frames, frames);
    TEST_ASSERT_EQUAL(0, bad_crc);
    TEST_ASSERT_EQUAL(0, seq_breaks);
    TEST_ASSERT_FLOAT_WITHIN(0.15f, 1.0f, g);
    /* 每个轴都要和测试自己直接读到的一致（板子静止）：只看 |a|≈1 g 时，字节序写反的帧碰巧也能落在范围里
       （2026-11-14 板子换了摆放角度后 big_endian 变异体算出 0.917 g，没被抓住） */
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_IMU_ADDR, .scl_speed_hz = 100000};
    i2c_master_dev_handle_t probe;
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_bus_add_device(bus, &dc, &probe));
    uint8_t start = 0x3B, b[6];
    TEST_ASSERT_EQUAL(ESP_OK, i2c_master_transmit_receive(probe, &start, 1, b, sizeof b, 50));
    for (int k = 0; k < 3; k++) {
        int16_t mine = (int16_t)((b[2 * k] << 8) | b[2 * k + 1]);
        ff_log("轴 %d：帧里 %d，直接读 %d", k, last[k], mine);
        TEST_ASSERT_INT_WITHIN(1500, mine, last[k]);
    }
    i2c_master_bus_rm_device(probe);
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("停止后可以再次启动，重复启动返回状态错误", "[logger]")
{
    i2c_master_bus_handle_t bus = bus_new();
    TEST_ASSERT_EQUAL(ESP_OK, logger_start(bus, 20));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, logger_start(bus, 20));
    TEST_ASSERT_EQUAL(ESP_OK, logger_stop());
    TEST_ASSERT_EQUAL(ESP_OK, logger_start(bus, 20));
    TEST_ASSERT_EQUAL(ESP_OK, logger_stop());
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}

TEST_CASE("参数非法", "[logger]")
{
    i2c_master_bus_handle_t bus = bus_new();
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, logger_start(NULL, 50));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, logger_start(bus, 5));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, logger_start(bus, 101));
    TEST_ASSERT_EQUAL(ESP_OK, i2c_del_master_bus(bus));
}
