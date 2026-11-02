# MAX30102 配置与 FIFO 读取

配置 MAX30102（心率血氧传感器）为 SpO2 模式（红光 + 红外），按读写指针从 FIFO 里取样本。

## 硬件

- 器件地址 `FF_PPG_ADDR`（0x57）；I2C 总线由调用方创建好传入，时钟不超过 400 kHz。

## 接口（`include/max30102.h`，不能修改）

- `esp_err_t ppg_init(i2c_master_bus_handle_t bus, uint16_t rate_sps)`：采样率 50、100、200、400 之一；
- `int ppg_read(ppg_sample_t *buf, size_t cap)`：取出 FIFO 里现有的样本，最多 `cap` 个，返回取到的个数，出错返回 -1；
- `void ppg_deinit(void)`：释放（只删自己挂上的设备），之后可以再次初始化。

## 要求

1. 初始化：确认 PART_ID；软复位并等复位完成（最多 100 ms，超时返回 `ESP_ERR_TIMEOUT`）；
2. FIFO：不做样本平均，满了回绕；配置完把写指针、溢出计数、读指针清零；
3. SpO2 模式；采样率按数据手册的编码表；LED 脉宽 411 µs（18 位精度）；ADC 量程 4096 nA；两个 LED 电流寄存器都设为 0x24；
4. 可读样本数按读写指针计算，要处理指针回绕；
5. 每个样本 6 字节：红光 3 字节、红外 3 字节，高字节在前；数据格式按数据手册（注意对齐方式）；
6. 参数非法返回 `ESP_ERR_INVALID_ARG`（`ppg_read` 返回 -1）。
