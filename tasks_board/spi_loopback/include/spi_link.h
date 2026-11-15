#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t spi_link_init(uint32_t clock_hz);
esp_err_t spi_link_xfer(const uint8_t *tx, uint8_t *rx, size_t len);
void spi_link_deinit(void);
