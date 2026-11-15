# NVS 掉电保存配置

用 ESP32-C3 片内 Flash 的 NVS 分区保存一组配置（数字和字符串），断电或重启以后还能读回来。

## 接口（`include/kv.h`，不能修改）

- `esp_err_t kv_init(void)`：初始化 NVS 并打开命名空间 `ff_cfg`；已经初始化过时直接返回 `ESP_OK`；
- `esp_err_t kv_set_u32(const char *key, uint32_t value)` / `esp_err_t kv_get_u32(const char *key, uint32_t *out)`；
- `esp_err_t kv_set_str(const char *key, const char *value)` / `esp_err_t kv_get_str(const char *key, char *buf, size_t cap)`；
- `esp_err_t kv_erase(const char *key)`：删除一个键；
- `esp_err_t kv_erase_all(void)`：清空命名空间 `ff_cfg` 里的所有键；
- `void kv_deinit(void)`：关闭命名空间，之后可以再次 `kv_init`。

## 要求

1. 每次写入或删除后立即提交，重启（软重启、掉电）后数据还在；
2. NVS 分区没有空页或版本不对时，擦除整个分区后重新初始化；
3. 键名长度 1–15 个字符，否则返回 `ESP_ERR_INVALID_ARG`；指针为空也返回 `ESP_ERR_INVALID_ARG`；
4. 读或删除不存在的键返回 `ESP_ERR_NOT_FOUND`（不是 NVS 自己的错误码）；
5. `kv_get_str` 的 `cap` 放不下整个字符串（含结尾的 `\0`）时返回 `ESP_ERR_INVALID_SIZE`；
6. 没有 `kv_init` 就调用其他函数（`kv_deinit` 除外）返回 `ESP_ERR_INVALID_STATE`；
7. 出错返回 `esp_err_t` 错误码，不要 `abort`。
