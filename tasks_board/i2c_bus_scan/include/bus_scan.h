#pragma once
#include <stddef.h>
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"

esp_err_t bus_scan(i2c_master_bus_handle_t bus, uint8_t *found, size_t cap, size_t *n);
