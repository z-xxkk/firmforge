/* 变异体：不把 ESP_ERR_NVS_NOT_FOUND 换成 ESP_ERR_NOT_FOUND */
#include <string.h>
#include "nvs.h"
#include "nvs_flash.h"
#include "kv.h"

#define KV_NS "ff_cfg"

static nvs_handle_t s_h;
static bool s_open;

static bool key_ok(const char *key)
{
    size_t n = key ? strlen(key) : 0;
    return n >= 1 && n <= NVS_KEY_NAME_MAX_SIZE - 1;
}

static esp_err_t map(esp_err_t err)
{
    if (err == ESP_ERR_NVS_INVALID_LENGTH) {
        return ESP_ERR_INVALID_SIZE;
    }
    return err;
}

esp_err_t kv_init(void)
{
    if (s_open) {
        return ESP_OK;
    }
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        if ((err = nvs_flash_erase()) != ESP_OK) {
            return err;
        }
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        return err;
    }
    if ((err = nvs_open(KV_NS, NVS_READWRITE, &s_h)) != ESP_OK) {
        return err;
    }
    s_open = true;
    return ESP_OK;
}

esp_err_t kv_set_u32(const char *key, uint32_t value)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!key_ok(key)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = nvs_set_u32(s_h, key, value);
    return err == ESP_OK ? nvs_commit(s_h) : map(err);
}

esp_err_t kv_get_u32(const char *key, uint32_t *out)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!key_ok(key) || out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    return map(nvs_get_u32(s_h, key, out));
}

esp_err_t kv_set_str(const char *key, const char *value)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!key_ok(key) || value == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = nvs_set_str(s_h, key, value);
    return err == ESP_OK ? nvs_commit(s_h) : map(err);
}

esp_err_t kv_get_str(const char *key, char *buf, size_t cap)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!key_ok(key) || buf == NULL || cap == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t need = 0;
    esp_err_t err = nvs_get_str(s_h, key, NULL, &need);      /* 先问长度（含结尾的 \0） */
    if (err != ESP_OK) {
        return map(err);
    }
    if (need > cap) {
        return ESP_ERR_INVALID_SIZE;
    }
    return map(nvs_get_str(s_h, key, buf, &need));
}

esp_err_t kv_erase(const char *key)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!key_ok(key)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = nvs_erase_key(s_h, key);
    return err == ESP_OK ? nvs_commit(s_h) : map(err);
}

esp_err_t kv_erase_all(void)
{
    if (!s_open) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = nvs_erase_all(s_h);
    return err == ESP_OK ? nvs_commit(s_h) : map(err);
}

void kv_deinit(void)
{
    if (s_open) {
        nvs_close(s_h);
        s_open = false;
    }
}
