# 第三批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-24。两道题都已在 Docker 中自检通过：标准答案全部通过，8 份错卷全部被隐藏测试抓到。规则按 `batch3_rules.md`（已审核）实现，这一批**没有新增 ★ 规则**。

## 题 6：W25Q Flash 驱动 w25q_drv（19 个测试）

隐藏测试带一颗"假 Flash"（`hidden_tests/mock_hal_spi.c`），按 W25Q64JV 手册的行为模拟：
- 8 MB 存储，初始全为 FFh；
- 没有写使能时忽略页编程和擦除；编程或擦除后写使能自动清除；
- 页编程超过本页剩余长度时地址回绕到本页开头；只能把 1 变成 0；
- 编程或擦除后"忙"若干次状态查询，忙的时候忽略除读状态以外的指令；
- 记录每一次 SPI 调用，可以让第 N 次调用失败。

| 测试 | 检查什么 |
|---|---|
| test_init_accepts_both_device_ids | 第一条指令是 9Fh 读 3 字节；EF 40 17 和 EF 70 17 都接受 |
| test_init_rejects_other_ids | 4 种不对的身份都返回"芯片不对"，之后读数据被拒绝、不碰总线 |
| test_init_bus_error | SPI 失败 → 总线错误，之后未就绪；dev 为 NULL → 参数错误 |
| test_read_single_transfer_big_endian_address | 读数据只用**一次** SPI 调用，指令 03h，地址高字节在前，数据正确 |
| test_read_zero_length_no_bus | 长度 0 直接成功，不碰总线 |
| test_read_range_checks | 读最后 4 字节可以；越界、地址 + 长度溢出都返回参数错误且不碰总线 |
| test_write_within_page | 页内写 16 字节：一次页编程，数据正确，后面的字节没被动 |
| test_write_across_pages_is_split | 从 0x10F0 写 300 字节：拆成 16 + 256 + 28 三次页编程，每次之前都有写使能，数据完全正确 |
| test_write_full_aligned_page_single_program | 对齐的整页 256 字节只用一次页编程 |
| test_write_large_unaligned | 不对齐地写 2000 字节（跨 9 页），数据正确，前后字节没被动 |
| test_write_waits_until_not_busy | 芯片每次忙 20 次查询：驱动一直等到不忙，没有任何指令被芯片忽略 |
| test_write_timeout_when_always_busy | 芯片一直忙 → 返回超时，不再继续 |
| test_write_does_not_erase | 写入不会自动擦除；在 F0h 上写 0Fh 结果是 00h（手册规定只能清位） |
| test_write_zero_length_and_range | 长度 0 成功；越界返回参数错误；都不碰总线 |
| test_bus_error_mid_write_stops | 写到一半 SPI 失败 → 立即停止 |
| test_erase_sector | 写使能 → 20h + 地址（高字节在前）→ 等不忙；只擦除这 4KB，前后扇区不受影响 |
| test_erase_then_write_after_busy | 擦除后紧接着写入，数据正确（擦除也必须等不忙） |
| test_erase_unaligned_or_out_of_range | 地址没对齐 4KB、越界 → 参数错误，不自动对齐；最后一个扇区可以擦 |
| test_null_arguments | 各种 NULL 返回参数错误，不碰总线 |

错卷：不按页拆分（3 个测试抓到）、只发一次写使能（3 个）、不等忙标志（4 个）、地址低字节在前（5 个）。

## 题 7：Modbus RTU 从机 modbus_slave（18 个测试）

| 测试 | 检查什么 |
|---|---|
| test_init_address_and_args | 地址 1、247 可以；0、248、255 不行；寄存器为空、个数 0、NULL 不行 |
| test_failed_init_never_replies | 初始化失败后，任何请求都不回复 |
| test_read_spec_example | 规范 6.3 节的例子：读 0x6B 起 3 个寄存器，响应 `03 06 02 2B 00 00 00 64` + CRC |
| test_read_max_125_registers | 一次读满 125 个，字节数 250，最后一个寄存器正确 |
| test_read_quantity_out_of_range | 数量 0、126 → 异常 03 |
| test_read_address_range_spec_example | 规范第 7 节的例子：100 个寄存器，起始 96 数量 4 可以，数量 5 → 异常 02 |
| test_quantity_checked_before_address | 地址和数量都不对时报 03，不报 02（规范图 13 的检查顺序） |
| test_wrong_length_is_illegal_value | 03、06 请求长度不是 8 字节 → 异常 03 |
| test_write_single_spec_example | 规范 6.6 节的例子：写寄存器 1 为 3，响应与请求完全相同 |
| test_write_last_and_out_of_range | 能写最后一个寄存器；地址 100 → 异常 02，不写入 |
| test_unsupported_function | 功能码 01、04、10、2B → 异常 01（功能码加 0x80） |
| test_bad_crc_ignored | CRC 错、CRC 两字节对调 → 不回复、不写入 |
| test_other_address_ignored | 发给别的从站、发给保留地址 248 → 不回复、不写入 |
| test_broadcast_write_executes_without_reply | 广播写：执行写入，不回复，响应缓冲区一个字节都不动 |
| test_broadcast_other_cases_silent | 广播读、广播不支持的功能码、广播写非法地址：都不回复，寄存器不变 |
| test_frame_length_limits | 3 字节的帧、257 字节的帧（即使 CRC 正确）都丢弃 |
| test_response_buffer_too_small | 响应放不下 → 异常 04；连异常也放不下 → 不回复；写请求放不下时**不执行写入** |
| test_null_arguments | 各种 NULL 不回复、不崩溃 |

所有回复的 CRC 都检查了"低字节在前"。

错卷：先查地址后查数量（2 个测试抓到，正好是检查顺序那条）、对广播回复（2 个）、响应 CRC 高字节在前（8 个）、地址范围差一（2 个）。

## 资源额度（实测 × 2）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| w25q_drv | 482 / 0 / 320 | 964 / 0 / 640 |
| modbus_slave | 466 / 0 / 56 | 932 / 0 / 112 |

w25q 的栈比较大，是因为 SPI 接口只有一个发送缓冲区，页编程要把"指令 + 地址 + 256 字节数据"拼进一个 260 字节的局部数组。
