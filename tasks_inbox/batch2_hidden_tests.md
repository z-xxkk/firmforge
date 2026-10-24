# 第二批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-24。两道题都已在 Docker 中自检通过：标准答案全部通过，7 份错卷全部被隐藏测试抓到。规则按 `batch2_rules.md`（已审核）实现，这一批**没有新增 ★ 规则**。

## 题 4：按键事件 button_events（17 个测试）

下面"tick"从 1 数起；例如"1100000"表示前 2 个 tick 按下、之后松开。

| 测试 | 检查什么 |
|---|---|
| test_spec_example_single_click | 需求文档里的例子：窗口 3、阈值 5，"1100000" 在第 6 个 tick 报单击 |
| test_single_click_waits_full_window | 松开后要等满整个窗口才报单击，之前一个事件都不报 |
| test_double_click_reported_on_second_release | 第二次松开的那个 tick 立刻报双击 |
| test_double_click_at_last_tick_of_window | 在窗口最后一个 tick 再按下，仍算连击 |
| test_second_press_after_window_is_new_click | 窗口过了才再按下：先报一次单击，新的一轮再报一次单击 |
| test_window_counted_from_release_not_press | 窗口从**松开**开始计时（按住 3 个 tick 后松开，再隔 3 个 tick 按下仍算双击） |
| test_long_press_start_when_reaching_threshold | 按住时长**达到**阈值的那个 tick 报长按开始，之后一直按着不再报 |
| test_long_press_end_on_release_and_no_click | 长按松开报长按结束，之后**不报单击** |
| test_press_just_below_threshold_is_click | 按住时长比阈值少 1 个 tick，算单击 |
| test_second_press_becomes_long_drops_click | 第二次按下变成长按：报长按开始 / 结束，前面那次单击丢弃 |
| test_third_press_starts_new_round | 双击之后的第三次按下算新一轮，后面报单击 |
| test_idle_never_reports | 一直不按，200 个 tick 不报任何事件 |
| test_defaults_when_zero | 参数给 0 时用默认值：按住 799 个 tick 后松开，松开后第 400 个 tick 报单击；按住满 800 个 tick 报长按 |
| test_press_ticks_one | 阈值为 1 时，按下的第 1 个 tick 就报长按开始，松开报长按结束 |
| test_repeated_double_clicks | 连续 5 轮双击都能正确识别 |
| test_reinit_resets_state | 重新初始化会丢掉"正在等双击"的状态 |
| test_null_arguments | NULL 不崩溃，返回"无事件" |

错卷：窗口从按下开始算（8 个测试抓到）、长按松开后多报单击（2 个测试）、单击晚一个 tick（6 个测试）。

## 题 5：MPU6050 驱动 mpu6050_drv（17 个测试）

隐藏测试带一个"假 I2C"（`hidden_tests/mock_hal_i2c.c`）：模拟芯片寄存器（上电值 0x6B = 0x40、0x75 = 0x68），记录每一次读写，可以让指定的第 N 次操作失败，地址不对时不应答。

| 测试 | 检查什么 |
|---|---|
| test_init_configures_registers | 4 × 4 种量程组合，初始化后 0x6B = 0x01，0x1B、0x1C 的值都是"量程 << 3" |
| test_init_reads_who_am_i_first | 第一个总线操作是读 0x75；所有操作都发往 0x68 |
| test_ad0_high_uses_0x69 | AD0 接高时用 0x69；芯片在 0x69 却用 0x68 访问 → 总线错误 |
| test_wrong_id_rejected_without_writes | 读到 0x98 → 返回"芯片不对"，**一次写都没有**（0x6B 还是 0x40），之后读数据被拒绝 |
| test_bus_error_on_id_read | 读身份就失败 → 总线错误，只发生 1 次操作 |
| test_bus_error_mid_init_stops | 第一次写失败 → 立即停止（总共 2 次操作），驱动未就绪 |
| test_invalid_range_no_bus_access | 量程超出范围、dev 为 NULL → 参数错误，不碰总线 |
| test_read_is_one_burst_of_14_bytes | 读数据恰好是**一次**从 0x3B 开始读 14 字节 |
| test_raw_values_big_endian | 7 个原始值按高字节在前正确拼出，包括负数和 -32768、32767 |
| test_accel_conversion_all_ranges | 4 档量程下，1 g 的原始值都换算成 1000 mg，-0.5 g → -500 |
| test_gyro_conversion_all_ranges | 4 档量程下，10 °/s 的原始值都换算成 1000；原始值 -1 按向零取整 |
| test_conversion_truncates_toward_zero | 6.1 mg → 6，-6.1 mg → -6；152.67 → 152，-152.67 → -152 |
| test_extreme_raw_values | ±16 g 下 -32768 → -16000 mg；±2000 °/s 下 32767 → 199798（不溢出） |
| test_temperature_conversion | 原始值 0 → 3653（36.53 °C），340 → 3753，-521 → 3500，-12419 → 1，20000 → 9535 |
| test_read_bus_error_leaves_output_untouched | 读数据时总线失败，输出结构体一个字节都不改 |
| test_read_before_init_and_null | 没初始化、dev 或输出为 NULL → 参数错误，不碰总线 |
| test_failed_reinit_makes_not_ready | 初始化成功后再初始化一次失败，驱动变回未就绪 |

错卷：低字节在前（6 个测试抓到）、量程位置不对（1 个测试）、分 7 次读（1 个测试，正好是"一次读 14 字节"那条）、温度漏偏移（1 个测试）。

## 资源额度（实测 × 2，栈不低于 32）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| button_events | 180 / 0 / 0 | 360 / 0 / 32 |
| mpu6050_drv | 368 / 0 / 48 | 736 / 0 / 96 |

mpu6050 的栈用量不含 I2C 函数本身（它由使用方提供，判定器在报告里注明了这一点）。
