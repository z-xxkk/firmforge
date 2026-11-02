#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "logger.h"

#define LOG_UART         UART_NUM_1
#define REG_SMPLRT_DIV   0x19
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_INT_PIN_CFG  0x37
#define REG_INT_ENABLE   0x38
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B
#define REG_WHO_AM_I     0x75
#define XFER_TIMEOUT_MS  50
#define FRAME_LEN        19

static i2c_master_dev_handle_t s_dev;
static TaskHandle_t s_task;
static volatile bool s_stopping;
static bool s_uart_on, s_isr_installed_by_us;
static uint8_t s_seq;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(s_dev, b, sizeof b, XFER_TIMEOUT_MS);
}

static esp_err_t rd(uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, buf, len, XFER_TIMEOUT_MS);
}

static uint16_t crc16_ccitt_false(const uint8_t *p, size_t n)
{
    uint16_t crc = 0xFFFF;
    while (n--) {
        crc ^= (uint16_t)(*p++) << 8;
        for (int i = 0; i < 8; i++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static void put16(uint8_t *p, int16_t v)
{
    p[0] = (uint8_t)((uint16_t)v & 0xFF);           /* 小端 */
    p[1] = (uint8_t)((uint16_t)v >> 8);
}

static void IRAM_ATTR on_int(void *arg)
{
    BaseType_t woken = pdFALSE;
    vTaskNotifyGiveFromISR(s_task, &woken);
    portYIELD_FROM_ISR(woken);
}

static void worker(void *arg)
{
    uint8_t b[14], f[FRAME_LEN];
    while (!s_stopping) {
        if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500)) == 0 || s_stopping) {
            continue;
        }
        if (rd(REG_ACCEL_XOUT_H, b, sizeof b) != ESP_OK) {
            continue;
        }
        int16_t v[7];               /* 加速度 XYZ、陀螺 XYZ、温度 */
        for (int i = 0; i < 3; i++) {
            v[i] = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
            v[3 + i] = (int16_t)((b[8 + 2 * i] << 8) | b[9 + 2 * i]);
        }
        v[6] = (int16_t)((b[6] << 8) | b[7]);
        f[0] = 0xA5;
        f[1] = 0x5A;
        f[2] = s_seq++;
        for (int i = 0; i < 7; i++) {
            put16(&f[3 + 2 * i], v[i]);
        }
        uint16_t crc = crc16_ccitt_false(&f[2], 15);     /* 覆盖字节 2–16 */
        f[17] = (uint8_t)(crc & 0xFF);
        f[18] = (uint8_t)(crc >> 8);
        uart_write_bytes(LOG_UART, f, sizeof f);
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

static void cleanup(void)
{
    if (s_dev != NULL) {
        wr(REG_INT_ENABLE, 0x00);
    }
    gpio_isr_handler_remove(FF_PIN_IMU_INT);
    gpio_set_intr_type(FF_PIN_IMU_INT, GPIO_INTR_DISABLE);
    if (s_isr_installed_by_us) {
        gpio_uninstall_isr_service();
        s_isr_installed_by_us = false;
    }
    if (s_task != NULL) {
        s_stopping = true;
        xTaskNotifyGive(s_task);
        for (int i = 0; i < 100 && s_task != NULL; i++) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
    if (s_dev != NULL) {
        i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
    if (s_uart_on) {
        uart_driver_delete(LOG_UART);
        s_uart_on = false;
    }
}

esp_err_t logger_start(i2c_master_bus_handle_t bus, uint32_t rate_hz)
{
    if (bus == NULL || rate_hz < 10 || rate_hz > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_dev != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    uart_config_t uc = {
        .baud_rate = 115200, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(LOG_UART, 1024, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        return err;
    }
    s_uart_on = true;
    if ((err = uart_param_config(LOG_UART, &uc)) != ESP_OK ||
        (err = uart_set_pin(LOG_UART, FF_PIN_UART1_TX, FF_PIN_UART1_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE)) != ESP_OK) {
        cleanup();
        return err;
    }
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_IMU_ADDR, .scl_speed_hz = 400000,
    };
    if ((err = i2c_master_bus_add_device(bus, &dc, &s_dev)) != ESP_OK) {
        s_dev = NULL;
        cleanup();
        return err;
    }
    uint8_t who = 0;
    if ((err = rd(REG_WHO_AM_I, &who, 1)) == ESP_OK && who != 0x68) {
        err = ESP_ERR_NOT_FOUND;
    }
    uint8_t div = (uint8_t)(1000 / rate_hz - 1);          /* DLPF 开着：陀螺输出率 1 kHz */
    if (err == ESP_OK &&
        ((err = wr(REG_PWR_MGMT_1, 0x01)) != ESP_OK || (err = wr(REG_CONFIG, 0x03)) != ESP_OK ||
         (err = wr(REG_SMPLRT_DIV, div)) != ESP_OK || (err = wr(REG_ACCEL_CONFIG, 0x00)) != ESP_OK ||
         (err = wr(REG_GYRO_CONFIG, 0x00)) != ESP_OK || (err = wr(REG_INT_PIN_CFG, 0x30)) != ESP_OK)) {
        /* err 已设置 */
    }
    if (err != ESP_OK) {
        cleanup();
        return err;
    }
    s_stopping = false;
    s_seq = 0;
    if (xTaskCreate(worker, "imu_logger", 4096, NULL, 10, &s_task) != pdPASS) {
        cleanup();
        return ESP_ERR_NO_MEM;
    }
    gpio_config_t io = {.pin_bit_mask = 1ULL << FF_PIN_IMU_INT, .mode = GPIO_MODE_INPUT,
                        .pull_down_en = GPIO_PULLDOWN_ENABLE, .intr_type = GPIO_INTR_POSEDGE};
    if ((err = gpio_config(&io)) != ESP_OK) {
        cleanup();
        return err;
    }
    err = gpio_install_isr_service(0);
    if (err == ESP_OK) {
        s_isr_installed_by_us = true;
    } else if (err != ESP_ERR_INVALID_STATE) {
        cleanup();
        return err;
    }
    uint8_t junk[14];
    if ((err = gpio_isr_handler_add(FF_PIN_IMU_INT, on_int, NULL)) != ESP_OK ||
        (err = rd(REG_ACCEL_XOUT_H, junk, sizeof junk)) != ESP_OK ||
        (err = wr(REG_INT_ENABLE, 0x01)) != ESP_OK) {
        cleanup();
        return err;
    }
    return ESP_OK;
}

esp_err_t logger_stop(void)
{
    if (s_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    cleanup();
    return ESP_OK;
}
