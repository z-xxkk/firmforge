# 片内温度传感器

读 ESP32-C3 芯片自带温度传感器的温度（芯片自己的温度，通常比室温高几度）。

## 接口（`include/chip_temp.h`，不能修改）

- `esp_err_t chip_temp_init(void)`：安装并启用温度传感器；
- `esp_err_t chip_temp_read(float *celsius)`：读一次温度，单位摄氏度（°C），连续采 5 次取平均；
- `void chip_temp_deinit(void)`：停用并卸载传感器，之后可以再次 `chip_temp_init`。

## 要求

1. 测量范围用 -10–80 °C 这一档（这一档误差最小）；
2. 已经初始化过再调 `chip_temp_init` 返回 `ESP_ERR_INVALID_STATE`；
3. 没有初始化就 `chip_temp_read` 返回 `ESP_ERR_INVALID_STATE`；`celsius` 为空返回 `ESP_ERR_INVALID_ARG`；
4. `chip_temp_deinit` 在没初始化时调用也不出错；
5. 出错返回 `esp_err_t` 错误码，不要 `abort`。
