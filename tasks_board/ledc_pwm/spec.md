# LEDC 输出 PWM

用 ESP32-S3 的 LEDC 外设在输出引脚上产生指定频率和占空比的 PWM 方波。

## 硬件

- 输出引脚：`FF_PIN_PWM_OUT`（当前接线 GPIO12）。

## 接口（`include/pwm_out.h`，不能修改）

- `esp_err_t pwm_init(uint32_t freq_hz, uint32_t duty_permille)`：频率 100 Hz–100 kHz，占空比 0–1000‰（千分比）；
- `esp_err_t pwm_set_duty(uint32_t duty_permille)`：修改占空比，立即生效；
- `void pwm_deinit(void)`：停止输出、释放资源，之后可以再次初始化。

## 要求

1. 占空比分辨率要按频率选择，保证在整个频率范围内都能配置成功；
2. 占空比寄存器值 = 千分比 × 2^分辨率 / 1000；
3. 参数超范围返回 `ESP_ERR_INVALID_ARG`；
4. 频率误差 ≤ 1%，占空比误差 ≤ 2%。
