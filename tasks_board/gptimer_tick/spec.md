# 通用定时器周期回调

用 ESP32-C3 的通用定时器（GPTimer）产生固定频率的周期回调，回调里计数。

## 接口（`include/tick.h`，不能修改）

- `esp_err_t tick_start(uint32_t hz)`：以 `hz` 次/秒触发回调（1 Hz–10 kHz），计数从 0 开始；
- `uint32_t tick_count(void)`：从启动以来回调的次数；
- `esp_err_t tick_stop(void)`：停止并释放定时器，之后可以再次启动。

## 要求

1. 定时器计数分辨率 1 MHz；
2. 周期要准确：1 秒内的回调次数与 `hz` 相差不超过 2；
3. 回调运行在中断上下文，只做计数，不能调用会阻塞的函数；
4. `hz` 超范围返回 `ESP_ERR_INVALID_ARG`；已经启动时再启动返回 `ESP_ERR_INVALID_STATE`。
