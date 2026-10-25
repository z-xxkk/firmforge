# 第五批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-25。三道题都已在 Docker 中自检通过：标准答案全部通过，12 份错卷全部被隐藏测试抓到（下面写的"被几个测试抓到"是实际跑出来的数）。规则按 `batch5_rules.md`（已审核）实现。

**规则落地时补充的细节（请一起确认）**
- ISO-TP：块大小和最小间隔以**第一帧"继续发送"流控**为准（规则原文是"第一帧流控"；"等待"流控里的这两个值标准规定无意义，所以不采用）。单帧比接收缓冲区大时忽略。最后一个连续帧的字节数不够时也报"格式错"。
- SD 卡：CMD55 的回应已经带错误位时，不再发 ACMD41。等回应正好读 8 个字节：回应晚到 0–7 个字节都能收到。
- AT 指令：信息文本前缀后面必须紧跟冒号（`+CSQX:` 不算 `+CSQ` 的回答）；回显按大小写完全相同比较；没有命令时收到的残留 `+CME ERROR` 不改错误码。

---

## 题 14：ISO-TP 传输层 isotp（24 个测试）

隐藏测试自己写了一个对端（造单帧、首帧、连续帧，检查流控），并让被测的发送方和接收方互相收发。

| 测试 | 检查什么 |
|---|---|
| test_rx_single_frames | 单帧 7 字节；不补满 8 字节的单帧也接受 |
| test_rx_bad_single_frames_ignored | 长度 0、长度超过帧、长度 8 的单帧都忽略 |
| test_rx_multi_frame_sizes | 8、13、14、62、63、118、4095 字节都收对；块大小 0 时只回一帧流控，流控内容和填充正确 |
| test_rx_sequence_wraps_to_zero | 17 个连续帧：序号 15 之后是 0 |
| test_rx_block_size_flow_control | 块大小 3：每 3 帧回一次流控，最后一帧之后不回；正好在块边界收完也不回 |
| test_rx_wrong_sequence | 跳号 → "序号错"，之后的连续帧忽略 |
| test_rx_overflow | 比缓冲区大 → 回"溢出"流控；正好放得下可以收 |
| test_rx_bad_first_frames_ignored | 首帧不足 8 字节、总长度小于 8、32 位扩展格式都忽略 |
| test_rx_interrupted_by_new_frames | 接收中被单帧、首帧打断：计数正确，旧消息放弃，新消息照常收 |
| test_rx_ignores_flow_control_and_unknown | 接收端忽略流控帧和不认识的帧类型 |
| test_rx_short_consecutive_frames | 中间的连续帧不足 8 字节 → "格式错"；最后一帧不补满可以；最后一帧数据不够 → "格式错" |
| test_rx_timeout | 接收中超时放弃；空闲时超时没事 |
| test_rx_null_arguments | 各种 NULL、dlc 为 0 或 9 |
| test_tx_spec_example | 需求文档的例子：30 字节、块大小 2、间隔 5 毫秒，每一帧逐字节核对，等待时间也核对 |
| test_tx_single_frames_padded | 1、7 字节的单帧补 CC |
| test_tx_max_length_no_block_limit | 4095 字节、块大小 0：一口气发完，序号多次回绕，拼回的数据正确 |
| test_tx_stmin_decoding | 最小间隔 9 种取值的换算（含保留值按 127 毫秒） |
| test_tx_params_from_first_cts_only | 之后的流控改了块大小也不采用 |
| test_tx_wait_then_continue | 两次"等待"之后"继续发送" |
| test_tx_overflow_and_bad_flow_control | "溢出"、状态 3、流控不足 3 字节；出错后可以重新开始 |
| test_tx_ignores_unexpected_frames | 空闲时、正在发送时的流控和不是流控的帧都忽略 |
| test_tx_timeout | 只有等流控时超时才算 |
| test_tx_start_rejections | 长度 0、4096、NULL、上一条没发完 |
| test_loopback_with_block_size | 被测发送方 ↔ 被测接收方，块大小 4、间隔 F3h（300 微秒），1 到 4095 字节 |

错卷：序号 15 之后回到 1（2 个）、不按块大小停下（2 个）、保留间隔当 0（1 个）、收到首帧不回流控（5 个）。

## 题 15：SD 卡 SPI 模式初始化 sd_spi_init（15 个测试）

隐藏测试带一张"假 SD 卡"，按 SPI 时序逐字节模拟：可以设成 2.0 大容量卡、2.0 标准容量卡、1.x 版卡、MMC 卡、电压不支持、没插卡；回应可以晚到几个字节；ACMD41 可以要等很多次才就绪；大容量卡在主机不带 HCS 位时永远不就绪（和真卡一样）；CMD0、CMD8 的 CRC 不对时回 CRC 错；每条命令都检查 CRC7，并记录片选的拉低拉高。

| 测试 | 检查什么 |
|---|---|
| test_crc7_reference_values | CMD0 → 95h、CMD8 → 87h |
| test_init_v2_high_capacity | 命令顺序 CMD0、CMD8（参数 1AAh）、CMD55+ACMD41（带 HCS，忙 3 次第 4 次就绪）、CMD58；不发 CMD16；上电前至少 10 字节时钟；所有 CRC 正确 |
| test_init_v2_standard_capacity | 发 CMD16（512），类型是标准容量 |
| test_init_v1_card | ACMD41 不带 HCS，不发 CMD58，发 CMD16 |
| test_mmc_unsupported | MMC 卡 → "不支持"，之后读块被拒绝且不碰总线 |
| test_bad_voltage | 回显不对 → "电压不支持"，不再初始化 |
| test_no_card | 没插卡 → "没有卡"，正好试 10 次 CMD0 |
| test_cmd0_retry | 前 3 次 CMD0 没回应，第 4 次成功 |
| test_response_delays | 回应晚到 0–7 个字节都能初始化和读块；晚到 9 个 → 判没有卡 |
| test_acmd41_slow_and_timeout | 忙 500 次能等到；忙 5000 次 → 正好发 1000 次后超时 |
| test_chip_select_around_each_command | 每条命令选中一次、结束后释放、每条命令后补时钟；读块后也释放 |
| test_read_block_high_capacity_uses_block_number | 大容量卡读第 7 块：参数是 7，数据正确 |
| test_read_block_standard_capacity_uses_byte_address | 标准容量卡和 1.x 版卡读第 5 块：参数是 5 × 512 |
| test_read_errors | 错误标志 → "读出错"；一直等不到数据 → "读出错"；等 900 字节能读到；卡回地址越界 → "命令错"；每次都释放片选 |
| test_null_and_not_ready | 各种 NULL；初始化失败后变回未就绪 |

错卷：CMD8 的 CRC 写错（12 个）、大容量卡用字节地址（2 个）、ACMD41 不带 HCS（6 个）、只等 1 个字节回应（13 个）。

## 题 16：AT 指令响应解析 at_parser（16 个测试）

隐藏测试模拟一个 4G 模组的输出。

| 测试 | 检查什么 |
|---|---|
| test_csq_with_echo | 带回显的 AT+CSQ：回显忽略，信息文本、OK |
| test_without_echo_and_lf_only | 只用 LF 换行的模组 |
| test_bytes_fed_one_by_one_across_calls | 一行被拆成很多碎片喂进来 |
| test_creg_is_info_during_query_and_urc_otherwise | 空闲时 +CREG 是主动上报，AT+CREG? 进行中是信息文本 |
| test_urc_interleaved_during_command | 命令中间夹着 RING 和 +CMTI 新短信通知 |
| test_prefix_must_be_followed_by_colon | `+CSQX:` 不算 +CSQ 的回答 |
| test_info_without_prefix | AT+CGSN 回的一串数字、ATI 的多行信息；夹着的 +CREG 是上报 |
| test_error_and_cme_error | +CME ERROR 数字形式（10）和文字形式（−1）、ERROR |
| test_dial_result_codes_are_final | NO CARRIER、BUSY、NO ANSWER、NO DIALTONE 都结束命令 |
| test_stale_result_codes_ignored_when_idle | 空闲时的 OK、ERROR、+CME ERROR、NO CARRIER、普通文字都忽略，错误码不变 |
| test_timeout_then_late_ok | 超时结束命令；超时后才到的回答当上报、OK 忽略；之后可以发新命令 |
| test_line_too_long | 200 字符的行 → "行太长"，之后照常；正好 128 字符不算太长 |
| test_echo_must_match_exactly | 大小写不同的不是回显 |
| test_begin_rejections | NULL、空命令、命令太长、前缀太长（16 个可以，18 个不行）、上一条没结束 |
| test_parse_csq | 合法的三种写法；9 种不合法的写法；失败时不改输出 |
| test_rssi_to_dbm | 0 → −113、1 → −111、30 → −53、31 → −51；99、−1、32 不行 |

错卷：先判主动上报（5 个）、不认回显（3 个）、+CME ERROR 不结束命令（2 个）、rssi 换算差一档（1 个）。

## 资源额度（实测 × 2）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| isotp | 872 / 0 / 24 | 1744 / 0 / 48 |
| sd_spi_init | 662 / 0 / 80 | 1324 / 0 / 160 |
| at_parser | 963 / 0 / 24 | 1926 / 0 / 48 |
