# 第二批题目规则（草稿，待负责人审核）

起草：Claude Code，2026-10-24。按负责人要求，先查资料再定规则：每条规则后面注明依据。
标 ★ 的是资料没有定死、由我选的规则，需要你确认。

---

## 题 4：按键事件 button_events（状态机，难度 3）

### 参照了什么

主要参照 Arduino 生态里最常用的按键库 **OneButton**（mathertel/OneButton）。我读了它的源码，它的规则如下[1]：
- 默认时间：消抖 50 ms，单击判定 400 ms，长按判定 800 ms；
- 双击的时间窗口**从第一次松开开始计时**：松开后 400 ms 内再按下，算连击；
- 长按松开后**不会**再报单击；
- 只启用双击时，第二次松开立刻报"双击"，不用再等。

旁证：嵌入式常用的 MultiButton 库，短按阈值 300 ms、长按 1000 ms[2]；Windows 默认双击时间 500 ms[3]；Android 双击超时 300 ms、长按 400–500 ms[4]。各家数值不同，但都在"几百毫秒"这个量级，判定逻辑和 OneButton 一致。

### 怎么用

调用方每个 tick（建议 1 ms）调用一次 `button_update`，传入按键**已经消抖过**的状态（按下 / 松开），函数返回这个 tick 产生的事件：无、单击、双击、长按开始、长按结束。

### 规则

| 情况 | 应该怎样 | 依据 |
|---|---|---|
| 初始化时给出两个时间：双击窗口 click_ticks、长按阈值 press_ticks | ★ 任一个给 0 时用默认值：click = 400、press = 800（按 1 ms 一个 tick，即 OneButton 的默认值） | [1] |
| ★ 本模块不做消抖 | 输入应是已消抖的状态（可用题 3 debounce 的结果）。理由：职责单一，消抖已经有单独的题目考 | 我的选择 |
| 按下并保持，连续按住的 tick 数达到 press_ticks | 在**达到的那个 tick** 报"长按开始" | [1]（OneButton 用"超过"，差 1 ms，我们取"达到"更好写测试） |
| 长按之后松开 | 报"长按结束"，**不报单击** | [1] |
| 短按后松开（按住不到 press_ticks） | 开始等待；从松开起 click_ticks 个 tick 内没有再按下，就在**第 click_ticks 个 tick** 报"单击" | [1] |
| 松开后 click_ticks 内又按下，再短按松开 | 在**第二次松开的那个 tick**立刻报"双击"，不再等待 | [1] |
| 第二次按下后一直按住，达到长按阈值 | ★ 报"长按开始"，前面那一次单击**丢弃不报**；之后松开报"长按结束" | 与 OneButton 的实际行为一致[1] |
| 双击之后的第三次按下 | 当作新一轮的第一次按下 | [1]（最大连击数为 2 时） |
| 一直松着不动 | 不报任何事件 | — |
| 结构体传 NULL | 返回"无事件"，不崩溃 | Power of 10 第 7 条 |

**错卷（计划）**：双击窗口从第一次**按下**开始计时；长按松开后又多报一次单击；单击晚一个 tick 才报。

---

## 题 5：姿态传感器驱动 mpu6050_drv（驱动，难度 3）

### 参照了什么

**InvenSense 官方《MPU-6000 and MPU-6050 Register Map and Descriptions》Revision 4.0**[5]。下面的寄存器地址、数值、换算公式都是我逐页核对过原文的：

| 内容 | 原文要点 | 页码 |
|---|---|---|
| 身份寄存器 WHO_AM_I（0x75） | 默认值 0x68 | 第 46 页 |
| I2C 地址 | 7 位地址的最低位由 AD0 引脚决定：AD0 接低是 0x68，接高是 0x69 | 第 46 页 |
| 电源管理 PWR_MGMT_1（0x6B） | 上电复位值 0x40，即**上电后处于睡眠模式**；第 6 位 SLEEP；第 2–0 位 CLKSEL 选时钟源，"强烈建议"用陀螺仪做时钟参考（CLKSEL = 1 表示以 X 轴陀螺仪为参考的 PLL） | 第 8、41 页 |
| 陀螺仪量程 GYRO_CONFIG（0x1B） | 第 4–3 位 FS_SEL：±250 / 500 / 1000 / 2000 °/s，灵敏度 131 / 65.5 / 32.8 / 16.4 LSB/(°/s) | 第 14、32 页 |
| 加速度量程 ACCEL_CONFIG（0x1C） | 第 4–3 位 AFS_SEL：±2 / 4 / 8 / 16 g，灵敏度 16384 / 8192 / 4096 / 2048 LSB/g | 第 15、30 页 |
| 数据寄存器 0x3B–0x48 | 依次是加速度 X/Y/Z、温度、陀螺仪 X/Y/Z，每个 2 字节，**高字节在前**，16 位有符号（二进制补码） | 第 30–32 页 |
| 连续读取 | 原文说明：一次连续读（burst read）能保证读到的是**同一时刻**的数据；分开单字节读则不能保证 | 第 30 页 |
| 温度换算 | 温度(°C) = 原始值 / 340 + 36.53 | 第 31 页 |

I2C 接口仿照 STM32 官方 HAL 的"按寄存器地址读写"函数 HAL_I2C_Mem_Read / Mem_Write[6]，做了简化，见下文。

### 怎么用

题目提供一个 I2C 接口头文件 `hal_i2c.h`（Agent 不能改），里面有两个函数："从某设备的某寄存器开始连续读 N 字节"、"往某设备的某寄存器开始连续写 N 字节"，返回成功或失败。驱动只能通过这两个函数访问芯片。

驱动提供两个功能：
1. **初始化**：给出 AD0 接法、加速度量程、陀螺仪量程；
2. **读一次数据**：返回原始值，以及换算后的物理量。

### 规则

| 情况 | 应该怎样 | 依据 |
|---|---|---|
| ★ I2C 接口用 7 位地址，不需要左移 | STM32 HAL 要求调用方先把地址左移一位，容易出错；我们的接口直接收 0x68 / 0x69，在头文件注释里写明 | [6] 的反面教训；我的选择 |
| 初始化第 1 步：读 WHO_AM_I | 必须读到 0x68，否则返回"芯片不对"，**不再做后续写入** | [5] 第 46 页 |
| ★ 读到的是 0x98 这类兼容芯片 | 也算"芯片不对"。论坛上有人反映山寨模块返回 0x98，它的寄存器是否完全兼容没有定论[7] | 我的选择 |
| 初始化第 2 步：唤醒 | 向 0x6B 写 0x01（清除 SLEEP，时钟选 X 轴陀螺仪 PLL） | [5] 第 41 页 |
| 初始化第 3、4 步：设量程 | 向 0x1B 写 FS_SEL << 3，向 0x1C 写 AFS_SEL << 3，其他位写 0（不开自检） | [5] 第 14–15 页 |
| 读数据 | 从 0x3B 开始**一次连续读 14 字节**，不允许分成多次读 | [5] 第 30 页 |
| 拼字节 | 高字节在前，按有符号 16 位解释 | [5] 第 30–32 页 |
| ★ 换算单位用整数 | 加速度单位 **mg**（千分之一 g），角速度单位 **0.01 °/s**，温度单位 **0.01 °C**。理由：Cortex-M3 没有浮点硬件，嵌入式驱动常用定点整数 | 我的选择 |
| ★ 换算取整 | 一律按 C 整数除法**向零取整**，中间结果用 32 位整数，保证不溢出。例：加速度 mg = 原始值 × 1000 ÷ 灵敏度；角速度 0.01°/s = 原始值 × 1000 ÷（灵敏度 × 10）；温度 0.01°C = 原始值 × 100 ÷ 340 + 3653 | 与中值滤波题（DECISIONS 第 28 条）一致 |
| I2C 读写失败 | 立即返回"总线错误"，不继续后面的步骤；读数据失败时**不改动**输出结构体 | 我的选择（常规做法） |
| 没初始化成功就读数据 | ★ 返回"参数错误"，不访问总线 | 我的选择 |
| 量程参数超出 4 档 | 返回"参数错误"，不访问总线 | — |
| 结构体或输出指针为 NULL | 返回"参数错误"，不崩溃 | Power of 10 第 7 条 |

**判卷方式**：隐藏测试里有一个"假 I2C"，模拟芯片的寄存器，并记录驱动发出的每一次读写，所以能检查"写了哪些寄存器、写了什么值、是不是一次连续读 14 字节"。按 ARCHITECTURE 的约定，Agent 写自己的测试时也要自己写一个假 I2C。

**错卷（计划）**：拼字节时低字节在前；量程写在第 2–1 位（没左移 3 位）；用 7 次单独读代替一次连续读；温度公式漏了 36.53 的偏移。

---

## 需要你回答的

1. 按键题 3 条 ★、传感器题 5 条 ★，逐条同意吗？
2. 传感器题的单位（mg、0.01 °/s、0.01 °C）可以吗？如果你更想用浮点数（单位 g、°/s、°C），也能做，但驱动体积会大不少（软件模拟浮点运算）。

## 参考来源

1. mathertel/OneButton：[OneButton.h](https://github.com/mathertel/OneButton/blob/master/src/OneButton.h)（默认 50 / 400 / 800 ms）、[OneButton.cpp](https://github.com/mathertel/OneButton/blob/master/src/OneButton.cpp)（状态机：双击窗口从松开计时、长按后不报单击、达到最大连击数立即结算）
2. MultiButton（0x1abin）：[multi_button.h](https://toscode.mulanos.cn/RT-Thread-Mirror/MultiButton/blob/master/multi_button.h)（SHORT_TICKS 300 ms、LONG_TICKS 1000 ms）
3. Microsoft：[SetDoubleClickTime](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdoubleclicktime)（默认 500 ms）
4. Android：[ViewConfiguration（Android 9 源码）](https://code.yawk.at/android/android-9.0.0_r35/android/view/ViewConfiguration.java)（DOUBLE_TAP_TIMEOUT 300、长按 500，后续版本改为 400）
5. InvenSense，[MPU-6000 and MPU-6050 Register Map and Descriptions, Revision 4.0](https://cdn.sparkfun.com/datasheets/Sensors/Accelerometers/RM-MPU-6000A.pdf)（文档号 RM-MPU-6000A-00，2012-03-09）
6. STM32 HAL：[HAL_I2C_Mem_Read / Mem_Write 文档](https://mikrocontroller.ti.bfh.ch/halDoc/stm32f4xx__hal__i2c_8h.html)
7. Arduino 论坛：[GY-521 模块 WHO_AM_I 读到 0x98](https://forum.arduino.cc/t/bare-metal-i2c1-on-stm32f411-who-am-i-returns-0x98-not-0x68-gy-521-mpu6050/1453784)
