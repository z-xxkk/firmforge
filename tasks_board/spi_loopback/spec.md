# SPI 主机全双工收发（回环）

用 ESP32-C3 的 SPI2 作为主机全双工收发。板子上 MOSI 和 MISO 之间有一根回环线，发出去的数据会同时被收回来。

## 硬件

- MOSI：`FF_PIN_SPI2_MOSI`；MISO：`FF_PIN_SPI2_MISO`；SCLK：`FF_PIN_SPI2_SCLK`；不用片选（CS）；
- 模式 0（CPOL = 0、CPHA = 0），高位先发。

## 接口（`include/spi_link.h`，不能修改）

- `esp_err_t spi_link_init(uint32_t clock_hz)`：初始化 SPI2 总线（DMA 由驱动自动分配）并加一个设备，时钟按 `clock_hz`；
- `esp_err_t spi_link_xfer(const uint8_t *tx, uint8_t *rx, size_t len)`：全双工收发 `len` 个字节，`rx` 可以为空（只发不收）；
- `void spi_link_deinit(void)`：移除设备、释放总线，之后可以再次初始化。

## 要求

1. `clock_hz` 允许 100 kHz–10 MHz，超出返回 `ESP_ERR_INVALID_ARG`；
2. 一次最多 256 字节；`len` 为 0 或超过 256、`tx` 为空都返回 `ESP_ERR_INVALID_ARG`；
3. 已经初始化过再调 `spi_link_init` 返回 `ESP_ERR_INVALID_STATE`；没初始化就收发返回 `ESP_ERR_INVALID_STATE`；
4. `spi_link_deinit` 在没初始化时调用也不出错；出错返回 `esp_err_t` 错误码，不要 `abort`。
