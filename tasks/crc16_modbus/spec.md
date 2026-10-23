# CRC-16/MODBUS 校验 crc16_modbus

实现 `include/crc16_modbus.h` 中声明的全部函数，用于 Modbus RTU 帧的 CRC 计算、附加和校验。

## 算法参数

- 初值 0xFFFF；
- 反射多项式 0xA001（即 0x8005 的位反转），按字节低位先处理；
- 无最终异或。
- 标准校验值：ASCII 字符串 `"123456789"`（9 字节）的 CRC 为 **0x4B37**。

## 约束

- 不能使用动态内存和 printf 系列函数。
- 可以逐位计算，也可以查表。查表时表**必须是 const**（放在 Flash），模块的静态 RAM 预算为 0 字节。

## 函数行为

| 函数 | 正常行为 | 异常/边界 |
|---|---|---|
| `crc16_modbus(data, len)` | 返回 data[0..len-1] 的 CRC | len 为 0 或 data 为 NULL：返回 0xFFFF |
| `crc16_modbus_append(frame, len)` | 计算 frame[0..len-1] 的 CRC，在 frame[len] 写**低字节**、frame[len+1] 写**高字节**；调用方保证 frame 至少有 len+2 字节；不修改其他字节 | frame 为 NULL：什么都不做 |
| `crc16_modbus_check(frame, total_len)` | total_len 包含末尾 2 字节 CRC；前 total_len−2 字节的 CRC 与末尾两字节（低字节在前）一致时返回 true | frame 为 NULL 或 total_len < 3：返回 false |

示例：帧 `01 03 00 00 00 0A` 的 CRC 为 0xCDC5，附加后为 `01 03 00 00 00 0A C5 CD`。
