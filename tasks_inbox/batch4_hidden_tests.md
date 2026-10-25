# 第四批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-25。三道题都已在 Docker 中自检通过：标准答案全部通过，12 份错卷全部被隐藏测试抓到。规则按 `batch4_rules.md`（已审核）实现。

**规则落地时补充的两处细节（请一起确认）**：
- XMODEM：握手阶段发 C 的那几次超时不计入"连续错误 10 次"；切到校验和模式之后、收到第一个块之前的超时照常计数。
- DS18B20：搜索出错时，`found` 为出错之前已经找到的个数；第一轮复位没有应答报"没有设备"，之后某一轮复位没有应答报"总线错误"（设备中途被拔掉）。

---

## 题 11：XMODEM 接收端 xmodem_rx（21 个测试）

隐藏测试自己扮演发送方，按原文逐字节造块（CRC 用另一种按位算法独立实现，先核对 "123456789" → 31C3h）。

| 测试 | 检查什么 |
|---|---|
| test_crc_reference_value | 测试自己的 CRC 算法正确（31C3h） |
| test_spec_example_flow_crc | 需求文档里的例子：CRC 模式 3 个块，一次校验错重发、一次 ACK 丢失重发，最后 EOT；数据和长度都对 |
| test_crc_is_high_byte_first | CRC 两字节对调后判为校验错 |
| test_fallback_to_checksum_after_four_c | 原文图 11：发 4 次 C 没人理，改回 NAK，之后按校验和模式收块 |
| test_handshake_timeouts_not_counted | 握手阶段的超时不计错误；切换之后再超时 10 次才中止 |
| test_bad_complement_nak | 块号与反码对不上 → NAK，不收数据 |
| test_duplicate_block_acked_and_discarded | 上一块的重复 → ACK、丢掉（内容不同也丢） |
| test_sync_loss_cancels | 跳块 → CAN、"失步"；之后什么输入都不再响应 |
| test_block_zero_first_is_sync_loss | 还没收到块就来块号 0 → 失步 |
| test_block_number_wraps_to_zero | 连收 257 块：块号 FFh 之后是 00h，再之后是 01h；回绕后的重复块也能认出 |
| test_mixed_1k_and_128_blocks | 1024 字节块与 128 字节块混着来 |
| test_two_cans_cancel_one_can_ignored | 单个 CAN 被忽略，连续两个才算取消 |
| test_can_and_soh_inside_data_are_data | 数据里的 18h、01h 照常当数据 |
| test_garbage_between_blocks_ignored | 块与块之间的杂波字节不回复 |
| test_timeout_inside_block_discards_it | 块收到一半超时 → NAK，整块重发后能正常收下 |
| test_ten_consecutive_errors_abort | 连续第 10 次错误回 CAN，状态"错误太多" |
| test_errors_reset_after_good_block | 好块之后错误次数清零（每块前错 9 次也不会中止） |
| test_overflow_cancels_and_keeps_data | 缓冲区放不下 → CAN、"缓冲区不够"，已收数据保留 |
| test_exact_fit_is_not_overflow | 正好放满不算溢出 |
| test_init_and_null_arguments | 各种 NULL、容量 0 |
| test_done_ignores_further_input | 完成后不再响应 |

错卷（实际跑出来被几个测试抓到）：块号回绕到 01h（1 个）、CRC 低字节在前（18 个）、重复块回 NAK（3 个）、一个 CAN 就取消（1 个）。

## 题 12：MAVLink 解析器 mavlink_parser（18 个测试）

测试用的消息表取自官方消息定义：HEARTBEAT（0，9 字节，50）、SYS_STATUS（1，31，124）、ATTITUDE（30，28，39）、COMMAND_LONG（76，33，152），这些 CRC_EXTRA 都是我按官方算法从 XML 算出来的，与公开值一致。另加一个 24 位消息号 300 和一个重复项。

| 测试 | 检查什么 |
|---|---|
| test_reference_values | 测试自己的 CRC（另一种按位写法）："123456789" → 6F91h |
| test_v1_heartbeat_spec_example | 需求文档里的 v1 HEARTBEAT 字节串 → 各字段正确 |
| test_v2_heartbeat_spec_example | 需求文档里的 v2 HEARTBEAT 字节串 |
| test_garbage_before_frame_skipped | 帧前的垃圾字节被跳过 |
| test_bad_crc_keeps_last_message | 校验错计数；"最近一条好消息"不被坏帧清掉 |
| test_crc_extra_is_required | 不加 CRC_EXTRA 的帧判为校验错；重复项用第一项 |
| test_crc_low_byte_first | 校验两字节对调 → 校验错 |
| test_v2_truncated_payload_zero_filled | 截断的负载末尾补 0（上一条消息末尾非 0，确认不会残留） |
| test_v2_signed_frame | 带签名帧（签名里故意放了 FEh）解析正确，之后的帧照常 |
| test_unknown_incompat_flag_dropped | 不认识的不兼容标志 → 丢弃并计数 |
| test_unknown_message_dropped | 未知消息号丢弃；24 位消息号 300 正常 |
| test_length_rules | v1 长度不对、v2 太长、v2 长度 0 都判长度不对 |
| test_resync_finds_frame_inside_bad_frame | 坏校验的 COMMAND_LONG 负载里藏着一帧 HEARTBEAT：同一次调用就交出这帧 |
| test_resync_after_stray_start_byte | 线路上一个杂散的 FEh 之后跟着真帧：真帧最终被找回 |
| test_sequence_loss_counting | 序号跨过 255→0 的丢包统计；另一个来源互不影响 |
| test_at_most_eight_sources_tracked | 10 个来源只统计前 8 个 |
| test_many_frames_back_to_back | 50 帧 v1/v2 交替首尾相连，全部收到、不误报丢包 |
| test_null_and_init | 各种 NULL、空消息表 |

错卷（实际跑出来被几个测试抓到）：漏掉 CRC_EXTRA（13 个）、校验高字节在前（14 个）、截断负载不补 0（1 个）、坏帧整个跳过（2 个）。

## 题 13：1-Wire 温度传感器 onewire_ds18b20（19 个测试）

隐藏测试带一条"假 1-Wire 总线"：可以挂多个假设备，读位时按真实总线做"线与"；搜索时不匹配的设备退出，直到下次复位；转换要读到若干个 0 才完成；暂存器 CRC 自动算（可以故意弄错）；驱动做了不符合协议的操作（比如该写的时候读）会被记下来，测试要求为 0。

| 测试 | 检查什么 |
|---|---|
| test_crc8_reference_values | "123456789" → A1h；ROM 号连同 CRC 算出 0 |
| test_search_single_device | 一个设备：找到、ROM 号正确、命令是 F0h、没有协议错误 |
| test_search_three_devices | 三个设备全找到，不重复，正好 3 轮搜索 |
| test_search_eight_devices_differing_late | 8 个设备只在序列号最高几位不同 |
| test_search_mixed_families | 家族码 28h、10h、22h 混挂 |
| test_search_no_device | 没有设备 → "没有设备"，找到 0 个 |
| test_search_bad_rom_crc | ROM 号 CRC 错 → "CRC 错" |
| test_search_device_vanishes | 搜到一半设备全部消失（读到 1、1）→ "总线错误" |
| test_search_array_full | 数组放 2 个、实际 3 个 → "还有更多"，不写数组之外；放 3 个时正常 |
| test_convert_all | 跳过 ROM + 44h，等到转换完成；只复位 1 次 |
| test_convert_timeout_and_no_device | 没有设备；转换永不完成 → 超时 |
| test_temperature_table_12bit | 数据手册表 1 的全部 10 个例子（+125 到 −55 °C） |
| test_lower_resolutions_mask_undefined_bits | 9、10、11 位分辨率清掉无意义的低位（含负温度） |
| test_power_on_value_returned | 没转换过读到 85 °C，照常返回 |
| test_match_rom_selects_device | 3 个设备温度不同：按 ROM 号逐个读，读到的是各自的温度 |
| test_scratchpad_crc_error_keeps_output | 暂存器 CRC 错 → "CRC 错"，输出不变 |
| test_not_ds18b20_family | 家族码 10h → "不是 DS18B20"，不碰总线 |
| test_read_absent_bus | 总线上没有设备 → "没有设备" |
| test_null_arguments | 各种 NULL、max 为 0，不碰总线 |

错卷（实际跑出来被几个测试抓到）：分歧点比较写反（4 个）、高位先发（12 个）、温度当无符号数（3 个）、不校验暂存器 CRC（1 个）。

## 资源额度（实测 × 2）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| xmodem_rx | 550 / 0 / 20 | 1100 / 0 / 40 |
| mavlink_parser | 812 / 0 / 72 | 1624 / 0 / 144 |
| onewire_ds18b20 | 584 / 0 / 72 | 1168 / 0 / 144 |

XMODEM 和 MAVLink 的接收缓冲区放在调用方提供的结构体里（不是静态变量），所以模块本身的静态内存为 0。
