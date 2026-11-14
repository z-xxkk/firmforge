# GPIO 上升沿计数

在 ESP32-C3 上数输入引脚的**上升沿**个数。

## 硬件

- 输入引脚：`FF_PIN_PWM_IN`（`hw_config.h` 里的宏）。

## 接口（`include/edge_counter.h`，不能修改）

- `esp_err_t edge_counter_init(void)`：配置输入引脚并开始计数，计数从 0 开始；
- `uint32_t edge_counter_get(void)`：返回从初始化或上次清零以来的上升沿个数；
- `void edge_counter_reset(void)`：计数清零；
- `void edge_counter_deinit(void)`：停止计数、释放资源；之后可以再次 `edge_counter_init`。

## 要求

1. 只数上升沿，下降沿不计；
2. 用 GPIO 中断实现。系统里可能已经有别的模块装好了 GPIO 中断服务，这种情况下初始化也必须成功；
3. 计数在中断和任务之间共享，要保证读到的值正确；
4. 要能正确数 10 kHz 以内的脉冲；
5. 中断处理函数里只做计数，不要打印日志或调用会阻塞的函数；
6. 出错返回 `esp_err_t` 错误码，不要 `abort`。
