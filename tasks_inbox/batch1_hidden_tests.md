# 第一批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-24。三道题都已在 Docker 中自检通过：标准答案全部通过，每份错卷都被隐藏测试抓到。

写代码时补充了几条第一批规则清单里没写到的细节，标为 **★新**，请重点确认。这些规则都已写进对应的需求文档（spec.md），Agent 能看到。

## 题 1：中值滤波 median_filter（16 个测试）

| 测试 | 检查什么 |
|---|---|
| test_init_is_empty | 刚初始化：个数为 0，取结果返回失败且不改动输出变量 |
| test_single_sample | 只有 1 个读数时结果就是它本身 |
| test_two_samples_average | 10、20 → 15（偶数个取平均） |
| test_even_average_truncates_toward_zero | -3、0 → -1；3、4 → 3（向零取整） |
| test_three_samples | 9、1、5 → 5 |
| test_four_samples_average_of_middle | 1、9、3、7 → 5（中间两个 3 和 7 的平均） |
| test_five_samples | 50、10、40、20、30 → 30 |
| test_spec_example | 需求文档里那张例子表逐行对得上 |
| test_window_drops_oldest | 窗口满了以后丢的是**最早**的读数 |
| test_spike_rejected | 100、101 之间突然来个 30000，结果不受影响 |
| test_duplicates | 有重复读数时结果正确 |
| test_extremes_no_overflow | 32767 + 32767、-32768 + 32767 这类极端值不溢出 |
| test_get_matches_last_push_and_keeps_window | "取结果"等于最近一次推入的结果，而且不改动窗口 |
| test_reset_clears | 清空后一切从头开始 |
| test_null_arguments | 各种 NULL 不崩溃；★新：输出位置为 NULL 时推入失败，**窗口不变**（这个读数不算数） |
| test_long_sequence_matches_reference | 300 个伪随机读数，每一步都和"直接排序求中位数"的结果一致 |

错卷：原地排序（2 个测试抓到）、窗口只有 4 个（4 个测试）、偶数取较大值（8 个测试）。

## 题 2：软件定时器 soft_timer（17 个测试）

| 测试 | 检查什么 |
|---|---|
| test_init_all_stopped | 刚初始化 8 个都停着，跑 100 个 tick 什么都不响 |
| test_oneshot_fires_on_period_tick_then_stops | 周期 3 的单次：第 3 次 tick 响，之后停止、不再响 |
| test_periodic_fires_every_period_without_drift | 周期 3 的循环：连续 60 个 tick，恰好在 3、6、9…… 响 |
| test_period_one_fires_every_tick | 周期 1 的循环每个 tick 都响 |
| test_simultaneous_expiry_reported_together | 0 号和 7 号同时到期，一次报出 0x81 |
| test_interleaved_periods | 周期 2 和周期 3 交错，第 6、12 次同时响 |
| test_remaining_counts_down | ★新："还剩几次"刚启动时等于周期，每个 tick 减 1，到期前一刻为 1 |
| test_periodic_remaining_after_reload | 循环定时器响过之后，"还剩几次"回到完整周期 |
| test_stop_prevents_expiry | 停止后不再响；停止已停止的定时器返回成功 |
| test_restart_running_timer_counts_from_now | 剩 2 个 tick 时重启，从重启时刻重新数满 5 个 |
| test_restart_with_new_period_and_type | 运行中改成别的周期和类型，按新设置走 |
| test_restart_after_oneshot_fired | 单次响过后可以再次启动 |
| test_zero_period_rejected_without_side_effect | 周期 0 被拒绝；★新：对正在运行的定时器用周期 0 重启失败时，**原来的定时器照常运行** |
| test_invalid_id_rejected | 编号 8、255 被拒绝，也不影响别的定时器 |
| test_large_period | 周期 10 万：第 99999 次不响、第 100000 次响 |
| test_max_period_accepted | ★新：最大周期 4294967295 可以接受 |
| test_null_arguments | 各种 NULL 不崩溃 |

错卷：晚一个 tick（多数测试抓到）、循环重装多一个 tick（5 个测试）、单次不停止（1 个测试）。

## 题 3：串口收包 uart_frame（17 个测试）

| 测试 | 检查什么 |
|---|---|
| test_good_frame_ok_only_on_last_byte | 好帧只在最后一个字节返回"收到好帧"，之前都是"没完" |
| test_known_frame_bytes | 校验码按"长度 + 数据"计算，低字节在前 |
| test_empty_payload | 长度为 0 的空帧合法 |
| test_max_payload_64 | 长度正好 64 的帧能收到 |
| test_noise_before_header_ignored | 帧头之前的杂乱字节被忽略 |
| test_back_to_back_frames | 两帧紧挨着（粘包）都能收到，各自数据正确 |
| test_payload_containing_header_byte | 数据里有 0xAA 也不会被误认为帧头 |
| test_bad_crc_then_good_frame | 校验错的帧报"坏帧"，紧接着的好帧照常收到 |
| test_swapped_crc_rejected | 校验码两个字节顺序颠倒判为坏帧 |
| test_length_over_64_rejected_immediately | 长度 65 在收到长度字节时立刻报坏帧 |
| test_noise_header_followed_by_real_frame | 噪声 0xAA 紧挨着真帧：先报一次坏帧，真帧仍能收到（重新同步） |
| test_real_frame_hidden_inside_bad_frame | 真帧头藏在坏帧里面：坏帧报错后，在已收字节里找到真帧并收齐 |
| test_last_good_frame_survives_errors | ★新：收到坏帧不会清掉"最近一帧好帧"的数据 |
| test_payload_before_any_frame | 还没收到好帧时取数据：长度 0、数据指针为空 |
| test_init_discards_partial_and_old_frame | 重新初始化会丢掉收了一半的帧和旧数据 |
| test_long_stream_with_noise | 40 帧随机长度（0–64）的数据夹杂噪声，全部按顺序收到、一次错都不报 |
| test_null_arguments | 各种 NULL 不崩溃 |

还有一条 ★新 规则写进了需求，但没有单独的测试：重新同步时，如果在已收字节里**直接凑成了一帧完整好帧**，这次调用返回"收到好帧"（交付好帧优先于报告错误）。这种情况很罕见。

错卷：校验漏算长度字节（多数测试抓到）、长度 64 误判（1 个测试）、不重新同步（2 个测试，正好是新规则那两条）、出错后不复位（4 个测试）。

## 资源额度（按 DECISIONS 第 19、20、27 条：实测 × 2，栈不低于 32）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| median_filter | 224 / 0 / 48 | 448 / 0 / 96 |
| soft_timer | 182 / 0 / 8 | 364 / 0 / 32 |
| uart_frame | 304 / 0 / 48 | 608 / 0 / 96 |
