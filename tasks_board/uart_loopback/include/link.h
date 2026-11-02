#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t link_init(uint32_t baud);
int link_write(const uint8_t *data, size_t len);
int link_read_line(char *buf, size_t cap, uint32_t timeout_ms);
void link_deinit(void);
