/* 变异体：时钟固定 1 MHz */
#include <string.h>
#include "driver/spi_master.h"
#include "hw_config.h"
#include "spi_link.h"

#define MAX_LEN 256

static spi_device_handle_t s_dev;
static bool s_bus;

esp_err_t spi_link_init(uint32_t clock_hz)
{
    if (s_dev != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (clock_hz < 100000 || clock_hz > 10000000) {
        return ESP_ERR_INVALID_ARG;
    }
    spi_bus_config_t bus = {
        .mosi_io_num = FF_PIN_SPI2_MOSI, .miso_io_num = FF_PIN_SPI2_MISO, .sclk_io_num = FF_PIN_SPI2_SCLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = MAX_LEN,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        return err;
    }
    s_bus = true;
    spi_device_interface_config_t dev = {
        .mode = 0, .clock_speed_hz = 1000000, .spics_io_num = -1, .queue_size = 1,
    };
    if ((err = spi_bus_add_device(SPI2_HOST, &dev, &s_dev)) != ESP_OK) {
        s_dev = NULL;
        spi_bus_free(SPI2_HOST);
        s_bus = false;
        return err;
    }
    return ESP_OK;
}

esp_err_t spi_link_xfer(const uint8_t *tx, uint8_t *rx, size_t len)
{
    if (s_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (tx == NULL || len == 0 || len > MAX_LEN) {
        return ESP_ERR_INVALID_ARG;
    }
    spi_transaction_t t = {
        .length = len * 8,                 /* 单位是位 */
        .tx_buffer = tx,
        .rx_buffer = rx,
    };
    return spi_device_polling_transmit(s_dev, &t);
}

void spi_link_deinit(void)
{
    if (s_dev != NULL) {
        spi_bus_remove_device(s_dev);
        s_dev = NULL;
    }
    if (s_bus) {
        spi_bus_free(SPI2_HOST);
        s_bus = false;
    }
}
