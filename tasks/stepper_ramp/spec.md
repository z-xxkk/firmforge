# 步进电机梯形加减速 stepper_ramp

实现 `include/stepper_ramp.h` 中声明的全部函数：按 Atmel 应用笔记 AVR446《Linear speed control of stepper motor》的算法，实时算出步进电机每一步之间的定时器间隔，使电机按恒定加速度起步、以最高速度运行、再按恒定减速度停在指定步数。

## 约束

- 不能使用动态内存、标准输入输出函数和**浮点运算**（包括 sqrt），不能用递归。
- 头文件由任务锁定，不能修改。

## 单位与常数

- 计时器频率 1 MHz，每圈 200 步。头文件里的整数常数已经按 AVR446 的公式算好。
- 步数 steps：正数正转、负数反转，绝对值 1–64000。
- 加速度 accel、减速度 decel：单位 0.01 rad/s²，范围 71–32000。
- 最高速度 speed：单位 0.01 rad/s，范围 12–32000。
- 间隔（delay）：计时器周期数（微秒）。

## 整数运算的规则

- 所有除法和取余都按 **C 语言整数规则**：向零取整，余数与被除数同号（分母为负数时与数学上的取整不同）。
- 中间结果用 64 位整数，避免溢出。
- `stepper_isqrt(x)`：整数平方根，返回不超过 √x 的最大整数。

## 开始移动：`stepper_move(s, steps, accel, decel, speed)`

令 n = |steps|，按下面的顺序计算（÷ 都是整数除法）：

1. min_delay = A_T_X100 ÷ speed（最高速度对应的间隔）
2. c0 = T1_FREQ_148 × isqrt(A_SQ ÷ accel) ÷ 100（第一步的间隔，乘 0.676 是为了补偿近似公式在第一步的误差，已经含在 T1_FREQ_148 里）
3. 若 n = 1：状态 DECEL，accel_count = −1，间隔 = c0，结束（不做下面几步）
4. max_s_lim = speed × speed ÷ (A_X20000 × accel ÷ 100)（加速到最高速度需要的步数）；为 0 时当作 1
5. accel_lim = n × decel ÷ (accel + decel)（不考虑最高速度时开始减速的位置）；为 0 时当作 1
6. 若 accel_lim ≤ max_s_lim（还没到最高速度就要减速，三角形曲线）：decel_val = accel_lim − n；
   否则（梯形曲线）：decel_val = −(max_s_lim × accel ÷ decel)
7. decel_val 为 0 时当作 −1
8. decel_start = n + decel_val
9. "上一次加速的间隔" = min_delay
10. 若 c0 ≤ min_delay：间隔 = min_delay，状态 RUN；否则间隔 = c0，状态 ACCEL
11. step_count = 0，accel_count = 0（n = 1 时见第 3 步），rest = 0

参数超出范围、s 为 NULL，或上一次移动还没结束（状态不是 STOP）：返回 false，什么都不改。成功返回 true。

## 每一步：`stepper_next(s, &delay)`

状态为 STOP 时返回 false。否则：

1. `*delay` = 当前间隔（含义：**现在走这一步，然后等 delay 个周期再调用下一次**）；
2. 位置加 1（反转时减 1），step_count 加 1；
3. 按当前状态计算新的间隔：

**ramp 公式**（ACCEL 和 DECEL 共用）：
```
num  = 2 × 当前间隔 + rest
den  = 4 × accel_count + 1
rest = num 除以 den 的余数（C 规则）
新间隔 = 当前间隔 − num ÷ den（C 规则）
```

| 状态 | 这一步做什么 |
|---|---|
| ACCEL | accel_count 加 1，按 ramp 公式算新间隔。若 step_count ≥ decel_start：accel_count = decel_val，转 DECEL；否则若新间隔 ≤ min_delay："上一次加速的间隔" = 新间隔，新间隔改为 min_delay，rest = 0，转 RUN |
| RUN | 新间隔 = min_delay。若 step_count ≥ decel_start：accel_count = decel_val，新间隔 = "上一次加速的间隔"，转 DECEL |
| DECEL | accel_count 加 1，按 ramp 公式算新间隔（这时 accel_count 是负数，分母为负，间隔变长）。若 accel_count ≥ 0：转 STOP |

4. 当前间隔 = 新间隔，返回 true。

一次移动正好调用 n 次 `stepper_next` 返回 true；最后一次返回的 delay 没有实际意义（之后已经停止）。

`delay` 或 s 为 NULL 时返回 false，什么都不改。

## 其他函数

| 函数 | 行为 |
|---|---|
| `stepper_init(s)` | 状态 STOP，位置 0；s 为 NULL 什么都不做 |
| `stepper_state(s)` | 当前状态；s 为 NULL 返回 STOP |
| `stepper_position(s)` | 累计位置（各次移动累加）；s 为 NULL 返回 0 |

## 例子

AVR446 演示程序的默认参数：走 400 步，accel = decel = 4000，speed = 2000。

- min_delay = 3141592 ÷ 2000 = 1570
- c0 = 6760 × isqrt(628318530 ÷ 4000) ÷ 100 = 6760 × isqrt(157079) ÷ 100 = 6760 × 396 ÷ 100 = 26769
- 第 1 步返回 26769；第 2 步：num = 53538，den = 5，新间隔 = 26769 − 10707 = 16062
