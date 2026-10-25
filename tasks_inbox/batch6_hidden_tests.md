# 第六批题目：隐藏测试清单（待负责人审核）

起草：Claude Code，2026-10-25。四道题都已在 Docker 中自检通过：标准答案全部通过，16 份错卷全部被隐藏测试抓到（下面写的"被几个测试抓到"是实际跑出来的数）。规则按 `batch6_rules.md`（已审核）实现。**评测集至此达到 20 道，`check-task --all` 全部通过。**

**规则落地时补充的细节（请一起确认）**
- PID：增益加了上限 `PID_GAIN_MAX` = 1048576（即 1024.0），超过就拒绝。理由：保证 64 位中间结果在极端输入下也不溢出（溢出在 C 里是未定义行为）。
- 步进电机：accel_lim 正好等于 max_s_lim 时按三角形处理（规则写的是"max_s_lim < accel_lim 才是梯形"，相等落在"否则"）。
- Flash 键值存储：一条条目用一次写操作写完；`kv_read` 允许 buf 为 NULL、cap 为 0，用来只查询长度；`kv_crc16` 带一个起始值参数，可以分段计算。

---

## 题 17：定点数 PID 控制器 pid_ctrl（15 个测试）

隐藏测试里自带一个用另一种写法实现的模型（分开处理正负号、显式写出向零取整），逐步对照输出。

| 测试 | 检查什么 |
|---|---|
| test_spec_example | 需求文档里的三步例子 |
| test_starts_in_manual | 初始为手动、输出 0（钳到范围内）；手动时不计算 |
| test_rounding_toward_zero | −4.5 → −4、−1.5 → −1（不能用右移） |
| test_integral_accumulates | 只有积分时每步加 Ki × 误差 |
| test_anti_windup_recovers_immediately | 饱和 200 步后设定值回落，下一步输出立即下降 |
| test_no_derivative_kick_on_setpoint_change | 只有 D：设定值从 20 跳到 900 输出仍为 0；测量值变 5 时输出 −50 |
| test_ki_change_does_not_bump | 运行中把 Ki 调大 4 倍，输出不跳 |
| test_bumpless_manual_to_auto | 手动输出 300 切到自动，输出保持 300 |
| test_repeated_auto_does_not_reinit | 已经是自动再设自动，积分不被重置 |
| test_auto_to_manual_and_back | 自动 → 手动设定输出 → 再切回自动，全程与模型一致 |
| test_set_limits_clamps_immediately | 改范围后输出和积分立即钳住；不合法的范围被拒绝 |
| test_set_output_only_in_manual | 手动时设定输出（钳到范围），自动时拒绝 |
| test_invalid_arguments | 负增益、超上限增益、下限 ≥ 上限、各种 NULL |
| test_extreme_values_no_overflow | 增益最大、输入为 int32 极值，结果与模型一致（同时检查没有溢出） |
| test_long_random_run_matches_model | 2000 步随机设定值和测量值，中途改增益、改范围，每一步都与模型一致 |

错卷：对误差求微分（4 个）、Ki 放在积分外面（5 个）、积分不限幅（3 个）、切自动时积分清零（2 个）。

## 题 18：步进电机梯形加减速 stepper_ramp（13 个测试）

期望的间隔序列由我另写的 Python 程序按同样的公式生成（专门按 C 语言的整数除法规则处理负数分母），不是拿参考答案跑出来的；C 参考实现与它逐个数字一致，包括一次 64000 步的移动。

| 测试 | 检查什么 |
|---|---|
| test_isqrt | 整数平方根 12 个取值（含 0、完全平方数前后、32 位最大值） |
| test_avr446_default_trapezoid | AVR446 演示程序的默认参数（400 步、4000 / 4000 / 2000）：400 个间隔逐个对照；中段是最高速度 |
| test_triangle_profile | 没到最高速度就减速的三角形曲线：100 个间隔逐个对照 |
| test_single_step | 只走 1 步 |
| test_two_steps | 走 2 步 |
| test_starts_directly_in_run | 速度很低时第一步就是最高速度（直接 RUN） |
| test_reverse_direction | 反转 50 步，位置 −50；再正转 20 步，位置累计为 −30 |
| test_asymmetric_and_min_accel | 加减速度相差很大；最小加速度 71 |
| test_long_moves | 64000 步和反转 30000 步：总和、校验值、最后一个间隔、最小间隔都与 Python 结果一致 |
| test_profile_shape | 加速段间隔递减，任何间隔都不小于最高速度对应的间隔 |
| test_rejects_bad_parameters | 步数 0 / ±64001、加减速度 70 / 32001、速度 11 / 32001、NULL |
| test_busy_rejects_new_move | 移动中拒绝新移动，停下后可以 |
| test_next_when_stopped_and_null | 停止时取下一步返回 false 且不改输出；各种 NULL |

错卷：不保留余数（5 个）、梯形与三角形判断写反（8 个）、减速起点差一步（8 个）、第一步不乘 0.676（7 个）。

## 题 19：固定块内存池 mem_pool（9 个测试）

| 测试 | 检查什么 |
|---|---|
| test_alloc_order_lifo | 第一次按地址从小到大；刚释放的块最先被分配 |
| test_exhaustion_returns_null | 用完返回 NULL；缓冲区边角不用 |
| test_block_size_equal_to_pointer | 块大小正好等于一个指针 |
| test_init_rejections | 放不下指针、不是对齐单位的倍数、缓冲区没对齐、0 块、超过 256 块 |
| test_bad_frees_do_not_corrupt | 别的缓冲区、紧挨着末尾、末尾之后、块中间、从没分配过的块、重复释放、NULL；之后池仍然完好（4 块各分一次、互不相同） |
| test_peak_and_reset | 历史最大值；重置后保留、重新初始化才清零；重置后旧指针都算空闲 |
| test_data_survives_in_allocated_blocks | 释放一半后，没释放的块内容不变 |
| test_random_stress_against_ledger | 随机分配释放 2 万次：块互不重叠、都在缓冲区内且对齐，统计与测试自己记的账一致 |
| test_null_pool | 各种 NULL |

错卷：不查重复释放（2 个）、不查块起点（1 个）、范围检查差一（1 个）、历史最大值不更新（2 个）。

## 题 20：Flash 键值存储 flash_kv（17 个测试）

隐藏测试带一块"假 Flash"：写入只能把 1 变成 0（想把 0 写回 1 就记一次违规，**每个测试结束时都要求违规次数为 0**），记录擦除和写入次数，可以设定"做到第 N 个操作单位时断电"（每写一个字节、每擦除一次算一个单位）。

| 测试 | 检查什么 |
|---|---|
| test_crc_reference_value | "123456789" → 29B1h；可以分段算 |
| test_blank_flash_is_formatted | 有垃圾内容的 Flash 挂载后被格式化：扇区头正确、剩余 1020 |
| test_spec_example_record_bytes | 需求文档的例子：条目逐字节正确（CRC 用测试自己的算法核对）；补齐的是 FFh |
| test_latest_value_wins_and_survives_remount | 同一 id 多次写以最后一次为准；重新上电后仍然正确 |
| test_unchanged_value_not_written | 值没变不写 Flash；长度不同要写 |
| test_delete | 删除后读不到；重复删除、删除不存在的 id 不写 Flash；删除后可以重新写 |
| test_read_buffer_too_small | 缓冲区不够时给出实际长度；buf 为 NULL、cap 为 0 只查长度 |
| test_garbage_collection_keeps_latest | 反复改 3 个 id 200 次，触发多次整理：最新值都在，已删除的不复活，只有一个扇区有头 |
| test_sequence_number_increments | 整理后扇区序号递增 |
| test_no_space_keeps_old_data | 14 条 64 字节的值写满后第 15 条报"空间不够"，已有数据完好；改已有 id 仍然可以 |
| test_both_headers_valid_picks_newer | 两个扇区都有效时选序号新的，包括 FFFFh → 0000h 回绕；旧的被擦除 |
| test_corrupt_record_stops_scan | 中间一条坏了：之前的照常读，之后的空间不用，下一次写入触发整理后恢复正常 |
| test_power_cut_during_append | 追加一条时在每一个字节处断电：重新上电后要么旧值要么新值，没写完的不会被当成有效 |
| test_power_cut_during_gc_write | 触发整理的写入，在整个过程（擦除、复制、写新条目、写头、擦旧扇区）的**每一个单位**断电：id 1 要么旧值要么新值，其他 id 全都完好，已删除的不复活，之后还能正常写 |
| test_power_cut_during_gc_delete | 删除操作在每一个单位断电：要么还在、要么已删除，其他数据完好 |
| test_parameters_and_unmounted | 没挂载、各种参数错误；最大合法 id FFFEh |
| test_flash_read_error | Flash 读失败时报错 |

错卷：整理时旧条目也复制（被删除的值复活，2 个）、先写扇区头（复制到一半断电后选中不完整的扇区，1 个）、找到第一条就返回（8 个）、没变也写（1 个）。

## 资源额度（实测 × 2）

| 题目 | 实测 Flash / 内存 / 调用链最坏栈 | 额度 |
|---|---|---|
| pid_ctrl | 564 / 0 / 48 | 1128 / 0 / 96 |
| stepper_ramp | 620 / 0 / 48 | 1240 / 0 / 96 |
| mem_pool | 288 / 0 / 32 | 576 / 0 / 64 |
| flash_kv | 1298 / 0 / 632 | 2596 / 0 / 1264 |

flash_kv 的栈比较大：写入 → 追加 → 查最新值 → 读条目这条调用链上，每一层都有一个 64 字节的值缓冲区。
