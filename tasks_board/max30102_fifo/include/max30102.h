#pragma once
#include <stddef.h>
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"

typedef struct { uint32_t red, ir; } ppg_sample_t;

esp_err_t ppg_init(i2c_master_bus_handle_t bus, uint16_t rate_sps);   /* 50、100、200、400 */
int ppg_read(ppg_sample_t *buf, size_t cap);
void ppg_deinit(void);
