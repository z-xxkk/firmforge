# UART1 回环收发与按行读取

用 ESP32-S3 的 UART1 收发数据，支持带超时的按行读取。TX 与 RX 之间有一根回环线，发出去的数据会被自己收到。

## 硬件

- TX：`FF_PIN_UART1_TX`；RX：`FF_PIN_UART1_RX`；不用 RTS/CTS；
- 8 位数据、无校验、1 位停止位。

## 接口（`include/link.h`，不能修改）

- `esp_err_t link_init(uint32_t baud)`；
- `int link_write(const uint8_t *data, size_t len)`：返回写入的字节数，失败返回 -1；
- `int link_read_line(char *buf, size_t cap, uint32_t timeout_ms)`：读一行（以 `\n` 结束），`buf` 里存不含换行的内容并以 `\0` 结尾，返回长度；`timeout_ms` 毫秒内没读到完整的一行返回 -1；
- `void link_deinit(void)`：释放驱动，之后可以再次初始化。

## 要求

1. 超时参数单位是毫秒；
2. 一行超过 `cap - 1` 字节时，返回前 `cap - 1` 字节，并丢掉这一行剩下的部分直到换行符；
3. 参数非法（`buf` 为空、`cap` 为 0）时 `link_read_line` 返回 -1；
4. 出错返回 `esp_err_t` 错误码，不要 `abort`。
