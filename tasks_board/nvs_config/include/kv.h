#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t kv_init(void);
esp_err_t kv_set_u32(const char *key, uint32_t value);
esp_err_t kv_get_u32(const char *key, uint32_t *out);
esp_err_t kv_set_str(const char *key, const char *value);
esp_err_t kv_get_str(const char *key, char *buf, size_t cap);
esp_err_t kv_erase(const char *key);
esp_err_t kv_erase_all(void);
void kv_deinit(void);
