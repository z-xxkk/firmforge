# I2C 总线扫描

扫描一条 I2C 总线，找出所有应答的器件地址。

## 接口（`include/bus_scan.h`，不能修改）

- `esp_err_t bus_scan(i2c_master_bus_handle_t bus, uint8_t *found, size_t cap, size_t *n)`
  - `bus`：调用方已经创建好的 I2C 主机总线；
  - `found`：调用方提供的数组，按地址从小到大写入应答的 7 位地址，最多写 `cap` 个；
  - `*n`：实际应答的地址个数（可能大于 `cap`）；
  - `bus` 或 `n` 为空、或 `cap > 0` 但 `found` 为空，返回 `ESP_ERR_INVALID_ARG`。

## 要求

1. 用 ESP-IDF v6.1 的新版 I2C 主机接口；不能用旧版接口（`driver/i2c.h` 里的 `i2c_driver_install`、`i2c_cmd_link_create` 等）；
2. 扫描 0x08–0x77，跳过 I2C 规范保留的地址；
3. 每个地址探测的超时不超过 50 ms，没有器件的地址不能让扫描卡住；
4. 不能创建或删除总线（总线属于调用方）。
