#include <stdatomic.h>
#include "driver/gpio.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hw_config.h"
#include "mpu_stream.h"

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
/* INT_PIN_CFG：INT_LEVEL=0（高有效）、INT_OPEN=0（推挽）、LATCH_INT_EN=1、INT_RD_CLEAR=1（RM p.27） */
#define INT_PIN_CFG_VAL  0x30

static i2c_master_dev_handle_t s_dev;
static QueueHandle_t s_q;
static TaskHandle_t s_task;
static volatile bool s_stopping;
static atomic_uint_fast32_t s_dropped;
static bool s_isr_installed_by_us;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(s_dev, b, sizeof b, XFER_TIMEOUT_MS);
}

static esp_err_t rd(uint8_t reg, uint8_t *buf, size_t len)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, buf, len, XFER_TIMEOUT_MS);
}

static void IRAM_ATTR on_int(void *arg)
{
    uint8_t b[14];
    rd(REG_ACCEL_XOUT_H, b, sizeof b);          /* 错误：中断里直接读 I2C */
    BaseType_t woken = pdFALSE;
    vTaskNotifyGiveFromISR(s_task, &woken);
    portYIELD_FROM_ISR(woken);
}

static void reader(void *arg)
{
    uint8_t b[14];
    while (!s_stopping) {
        if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500)) == 0 || s_stopping) {
            continue;
        }
        if (rd(REG_ACCEL_XOUT_H, b, sizeof b) != ESP_OK) {     /* 读寄存器同时清除锁存的中断 */
            continue;
        }
        mpu_sample_t s;
        for (int i = 0; i < 3; i++) {
            s.accel[i] = (int16_t)((b[2 * i] << 8) | b[2 * i + 1]);
            s.gyro[i] = (int16_t)((b[8 + 2 * i] << 8) | b[9 + 2 * i]);
        }
        s.temp = (int16_t)((b[6] << 8) | b[7]);
        if (xQueueSend(s_q, &s, 0) != pdTRUE) {
            atomic_fetch_add(&s_dropped, 1);
        }
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
    s_q = NULL;
}

esp_err_t mpu_stream_start(i2c_master_bus_handle_t bus, uint32_t rate_hz, QueueHandle_t q)
{
    if (bus == NULL || q == NULL || rate_hz < 10 || rate_hz > 200) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_dev != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = FF_IMU_ADDR, .scl_speed_hz = 400000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &dc, &s_dev);
    if (err != ESP_OK) {
        s_dev = NULL;
        return err;
    }
    uint8_t who = 0;
    if ((err = rd(REG_WHO_AM_I, &who, 1)) == ESP_OK && (who != 0x68 && who != FF_IMU_WHO_AM_I)) {
        err = ESP_ERR_NOT_FOUND;
    }
    /* DLPF 开着（DLPF_CFG = 3）时陀螺输出率 1 kHz：采样率 = 1000 / (1 + SMPLRT_DIV)（RM p.12） */
    uint8_t div = (uint8_t)(1000 / rate_hz - 1);
    if (err == ESP_OK &&
        ((err = wr(REG_PWR_MGMT_1, 0x01)) != ESP_OK || (err = wr(REG_CONFIG, 0x03)) != ESP_OK ||
         (err = wr(REG_SMPLRT_DIV, div)) != ESP_OK || (err = wr(REG_ACCEL_CONFIG, 0x00)) != ESP_OK ||
         (err = wr(REG_GYRO_CONFIG, 0x00)) != ESP_OK || (err = wr(REG_INT_PIN_CFG, INT_PIN_CFG_VAL)) != ESP_OK)) {
        /* err 已设置 */
    }
    if (err != ESP_OK) {
        cleanup();
        return err;
    }
    s_q = q;
    s_stopping = false;
    atomic_store(&s_dropped, 0);
    if (xTaskCreate(reader, "mpu_stream", 4096, NULL, 10, &s_task) != pdPASS) {
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
        (err = rd(REG_ACCEL_XOUT_H, junk, sizeof junk)) != ESP_OK ||     /* 先清掉可能已锁存的中断 */
        (err = wr(REG_INT_ENABLE, 0x01)) != ESP_OK) {                     /* 只开数据就绪中断 */
        cleanup();
        return err;
    }
    return ESP_OK;
}

esp_err_t mpu_stream_stop(void)
{
    if (s_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    cleanup();
    return ESP_OK;
}

uint32_t mpu_stream_dropped(void)
{
    return (uint32_t)atomic_load(&s_dropped);
}
