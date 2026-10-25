# 定点数 PID 控制器 pid_ctrl

实现 `include/pid_ctrl.h` 中声明的全部函数：一个用整数定点运算的 PID 控制器，带积分抗饱和、输出限幅、对测量值求微分、调参不突变、手动 / 自动无扰切换。

## 约束

- 不能使用动态内存、标准输入输出函数和**浮点运算**，不能用递归。
- 头文件由任务锁定，不能修改。

## 定点数约定

- 增益 kp、ki、kd 的单位是 1/1024（`PID_GAIN_ONE` = 1024 表示 1.0），范围 0–`PID_GAIN_MAX`（1048576，即 1024.0）。
- 增益是"每次采样"的增益：采样周期由调用方负责，每个采样周期调用一次 `pid_compute`。
- 积分项内部放大 1024 倍保存。
- 中间结果一律用 64 位整数。
- "÷ 1024"按 C 语言整数除法，**向零取整**（例如 −4.5 → −4，不能用右移）。

## 计算（自动模式下每次调用 `pid_compute(pid, setpoint, input)`）

1. 误差 e = setpoint − input
2. 积分 I = I + ki × e，然后把 I 钳到 [out_min × 1024, out_max × 1024]
3. 测量值变化 d = input − 上次的 input
4. 输出 = (kp × e + I − kd × d) ÷ 1024，再钳到 [out_min, out_max]
5. 记下本次 input，返回输出

说明（依据 Brett Beauregard《Improving the Beginner's PID》）：
- 第 2 步的积分限幅防止"积分饱和"：输出被限幅后积分不再一直累加，设定值回落时能立即响应；
- 第 3 步对**测量值**求微分而不是对误差求微分，设定值突变时输出不会出现尖峰；
- 积分里累加的是 ki × e（而不是先累加 e 再乘 ki），所以运行中修改 ki 时输出不会突然跳变。

## 函数行为

| 函数 | 正常行为 | 异常 / 边界 |
|---|---|---|
| `pid_init(pid, kp, ki, kd, out_min, out_max)` | 设定增益和输出范围；进入**手动模式**，输出 0（再钳到范围内），积分 0，上次 input 为 0；返回 true | pid 为 NULL、任何增益为负或超过 `PID_GAIN_MAX`、out_min ≥ out_max：返回 false |
| `pid_set_tunings(pid, kp, ki, kd)` | 只改增益，积分保持不变；返回 true | 增益不合法或 pid 为 NULL：返回 false，什么都不改 |
| `pid_set_limits(pid, out_min, out_max)` | 改输出范围，并**立即**把当前输出钳到新范围、把积分钳到 [out_min × 1024, out_max × 1024]；返回 true | out_min ≥ out_max 或 pid 为 NULL：返回 false，什么都不改 |
| `pid_set_auto(pid, automatic, input)` | 从手动切到自动时：积分 = 当前输出 × 1024（再钳到范围内），上次 input = 这里给的 input；已经是自动再设自动：什么都不做；切到手动：只改模式 | pid 为 NULL：什么都不做 |
| `pid_set_output(pid, output)` | 手动模式下设定输出（钳到范围内），返回 true | 自动模式下或 pid 为 NULL：返回 false，什么都不改 |
| `pid_compute(pid, setpoint, input)` | 自动模式按上面计算；手动模式直接返回当前输出，不改积分，也不记 input | pid 为 NULL：返回 0 |
| `pid_output(pid)` | 当前输出 | pid 为 NULL：返回 0 |
| `pid_is_auto(pid)` | 是否自动模式 | pid 为 NULL：返回 false |

## 例子

kp = 2048（2.0）、ki = 512（0.5）、kd = 0，范围 −1000…1000，手动切到自动时 input = 40、输出 0：

| 第几次 | setpoint | input | 误差 | 积分（×1024） | 输出 |
|---|---|---|---|---|---|
| 1 | 100 | 40 | 60 | 30720 | (2048×60 + 30720) ÷ 1024 = 150 |
| 2 | 100 | 50 | 50 | 56320 | (102400 + 56320) ÷ 1024 = 155 |
| 3 | 100 | 103 | −3 | 54784 | (−6144 + 54784) ÷ 1024 = 47 |
