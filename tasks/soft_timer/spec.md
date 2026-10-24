# 8 路软件定时器 soft_timer

实现 `include/soft_timer.h` 中声明的全部函数。单片机通常只有一个硬件定时器周期性地"嘀嗒"（tick，例如每 1 ms 一次）；软件定时器在它之上模拟出 8 个互相独立的定时器，编号 0–7。

## 约束

- 不能使用动态内存和标准输入输出函数，不能用递归，不使用回调函数。
- 状态结构体由调用方提供；字段由头文件给出，实现只能使用这些字段，头文件不能修改。

## 用法

1. `soft_timer_init` 初始化，此时 8 个定时器都处于停止状态；
2. `soft_timer_start` 启动某个定时器，给出周期（多少个 tick）和类型（单次或循环）；
3. 调用方每个 tick 调用一次 `soft_timer_tick`，它返回**这一次 tick 到期的定时器**：第 i 位为 1 表示定时器 i 到期（第 0 位对应定时器 0，第 7 位对应定时器 7）。

## 时序规则

- 周期为 P 的定时器启动后，紧接着的第 1 次到第 P−1 次 `soft_timer_tick` 都不到期，**第 P 次**到期。
- 单次定时器到期后自动停止。
- 循环定时器到期后立即开始下一个周期，所以会在第 P、2P、3P…… 次 tick 到期，不能越走越慢。
- 同一次 tick 有多个定时器到期时，全部在返回值里报告。
- 对**正在运行**的定时器再次调用 `soft_timer_start`：按新的周期和类型**从当前时刻重新开始计时**（不是在原来剩余时间上累加）。

## 函数行为

| 函数 | 正常行为 | 异常 / 边界 |
|---|---|---|
| `soft_timer_init(st)` | 全部定时器停止，剩余 tick 为 0 | st 为 NULL：什么都不做 |
| `soft_timer_start(st, id, period, periodic)` | 按上面的规则启动，返回 true | st 为 NULL、id ≥ 8 或 period 为 0：返回 false，不改变任何定时器 |
| `soft_timer_stop(st, id)` | 停止该定时器，返回 true；停止一个本来就没在运行的定时器也返回 true | st 为 NULL 或 id ≥ 8：返回 false |
| `soft_timer_tick(st)` | 推进一个 tick，返回到期位图 | st 为 NULL：返回 0 |
| `soft_timer_is_running(st, id)` | 运行中返回 true | 已停止、st 为 NULL 或 id ≥ 8：返回 false |
| `soft_timer_remaining(st, id)` | 运行中返回距离到期还剩几次 tick（刚启动时等于周期，到期前最后一次 tick 之前为 1） | 已停止、st 为 NULL 或 id ≥ 8：返回 0 |

周期可以是 1 到 4294967295 之间的任意值。
