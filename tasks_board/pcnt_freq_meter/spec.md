# 脉冲计数器测频率

用 ESP32-S3 的脉冲计数器（PCNT）测量输入引脚上方波的频率。

## 硬件

- 输入引脚：`FF_PIN_PWM_IN`（当前接线 GPIO13）。

## 接口（`include/freqmeter.h`，不能修改）

- `esp_err_t freqmeter_init(void)`：配置计数器并开始工作；
- `esp_err_t freqmeter_measure(uint32_t gate_ms, uint32_t *hz)`：在 `gate_ms` 毫秒的闸门时间内数上升沿，换算成频率（Hz）写入 `*hz`；
- `void freqmeter_deinit(void)`：释放资源，之后可以再次初始化。

## 要求

1. 只数上升沿；
2. 要支持 1 Hz–100 kHz、闸门时间 10–1000 ms 的任意组合；注意 ESP32-S3 的计数器只有 16 位；
3. 频率 = 闸门内的上升沿个数 × 1000 / 闸门毫秒数，四舍五入；闸门时间以实际经过的时间为准；
4. `gate_ms` 不在 10–1000 之间或 `hz` 为空，返回 `ESP_ERR_INVALID_ARG`；
5. 测量误差 ≤ 1%（输入频率 ≥ 1 kHz 时）。
