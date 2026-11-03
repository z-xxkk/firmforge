# RMT 测脉宽（频率与占空比）

用 RMT 接收通道采集输入引脚上的方波，算出平均频率和占空比。

## 硬件

- 输入引脚：`FF_PIN_PWM_IN`（当前接线：ESP32-C3 SuperMini 上是 GPIO7）。

## 接口（`include/pulse_meter.h`，不能修改）

- `esp_err_t pulse_meter_init(void)`：创建并启用 RMT 接收通道；
- `esp_err_t pulse_meter_measure(uint32_t timeout_ms, pulse_stat_t *out)`：采集至少 32 个完整周期，填写平均频率、平均占空比（高电平占比，千分比）和参与平均的周期数；
- `void pulse_meter_deinit(void)`：释放资源，之后可以再次初始化。

## 要求

1. 通道分辨率 10 MHz；
2. 支持 1 kHz–100 kHz 的输入；
3. "电平保持多久算结束"设 3 ms，"短于多少算毛刺"设 1000 ns；
4. 输入是连续方波，接收不会自己结束，要用分段接收收够数据后停止；
5. 接收回调运行在中断里，只拷贝数据、通知任务；
6. 计算时丢掉第一个（可能不完整的）符号；频率和占空比都四舍五入；
7. `timeout_ms` 内没收够 32 个完整周期（例如没有信号）返回 `ESP_ERR_TIMEOUT`；参数非法返回 `ESP_ERR_INVALID_ARG`。
