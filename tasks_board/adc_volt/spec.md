# ADC 读电压（带校准）

用 ESP32-C3 的 ADC1 单次读取模式测量一个引脚上的电压，经过校准后以毫伏返回。

## 硬件

- 被测电压接在 `FF_PIN_ADC_IN`（ADC1 的一个通道）；
- 板子上有一根线把 `FF_PIN_ADC_DRIVE` 接到 `FF_PIN_ADC_IN`，测试会用它输出高、低电平；**你的模块不要动 `FF_PIN_ADC_DRIVE`**。

## 接口（`include/volt.h`，不能修改）

- `esp_err_t volt_init(void)`：创建 ADC1 单次读取单元、配置 `FF_PIN_ADC_IN` 对应的通道、创建校准；
- `esp_err_t volt_read_mv(int *mv)`：读 16 次取平均，返回校准后的电压，单位毫伏（mV）；
- `void volt_deinit(void)`：删除校准和 ADC 单元，之后可以再次 `volt_init`。

## 要求

1. 衰减用 12 dB（量程最大），位宽用默认；
2. 必须经过芯片的校准（曲线拟合）换算成毫伏，不能直接按原始读数比例估算；
3. 通道由引脚号换算，不要写死通道号；
4. 已经初始化过再调 `volt_init` 返回 `ESP_ERR_INVALID_STATE`；没初始化就读返回 `ESP_ERR_INVALID_STATE`；`mv` 为空返回 `ESP_ERR_INVALID_ARG`；
5. `volt_deinit` 在没初始化时调用也不出错；出错返回 `esp_err_t` 错误码，不要 `abort`。
