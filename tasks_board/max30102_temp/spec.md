# MAX30102 片内温度

读 MAX30102 芯片内部的温度传感器。

## 硬件

- 器件地址 `FF_PPG_ADDR`（0x57）；I2C 总线由调用方创建好传入，时钟不超过 400 kHz。

## 接口（`include/max30102_temp.h`，不能修改）

- `esp_err_t ppg_temp_read(i2c_master_bus_handle_t bus, float *celsius)`：启动一次测温，等测量完成，读出温度；
- `float ppg_temp_convert(uint8_t tint, uint8_t tfrac)`：把温度整数寄存器和小数寄存器的值换算成 °C。

## 要求

1. 按数据手册：启动测温、等测量完成（最多等 100 ms，超时返回 `ESP_ERR_TIMEOUT`）、读整数和小数两个寄存器；
2. 整数部分是二进制补码（有符号），小数部分的单位按数据手册；
3. 只读温度：不能复位器件，不能改动测温以外的配置（其他模块可能同时在用这个器件）；
4. 函数结束时只删除自己挂上的设备，不删除总线；
5. `bus` 或 `celsius` 为空返回 `ESP_ERR_INVALID_ARG`。
