# 上板评测汇总

判定：固定判定规则的合规检查 → 编译 → 上板隐藏测试（所有配置相同）。skipped 不计入通过率。

| 配置 | 通过 / 有效次数 | 通过率 | 隐藏用例通过率 | 平均尝试次数 | 平均 token | 平均成本（元） | 平均用时（秒） | 跳过 |
|---|---|---|---|---|---|---|---|---|
| verified | 0 / 2 | 0% | 40% | 4.0 | 236758 | 0.3666 | 323 | 0 |

## 每道题（通过次数 / 有效次数）

| 题目 | verified |
|---|---|
| max30102_temp | 0 / 2 |

## 没通过的卡在哪一层

| 配置 | 层 | 次数 |
|---|---|---|
| verified | board_test | 2 |

## 披露：来自上板实测记录的规则（DECISIONS 第 95 条）

这些规则的依据是在本板上实测的结果，而不是芯片或器件资料。

| 配置 | 规则 | 内容 | 引用 |
|---|---|---|---|
| verified（规则库，人工确认过） | R-I2C-008 | 本板 I2C0 的 SDA=GPIO4、SCL=GPIO5，MPU6050 模块与 MAX30102 模块都接在这两条线上 | board-measured：I2C0 SDA=GPIO4、SCL=GPIO5，面包板接 MPU6050 模块与 MAX30102 模块 |
| verified（规则库，人工确认过） | R-I2C-009 | 本板 I2C 在 100 kHz 和 400 kHz 下读 MPU6050 均无错，scl_speed_hz 取 100000 或 400000 都可用 | board-measured：100 kHz 和 400 kHz 两种速率下 50 次读取都没有出错。 |
| verified（规则库，人工确认过） | R-MAX-028 | 片内测温要等 DIE_TEMP_RDY（先把中断使能 2 的 DIE_TEMP_RDY_EN 置位）；TEMP_EN 自动清零不代表温度已就绪，约 27.5 ms 后数据才有效 | board-measured：结论：测温要等 DIE_TEMP_RDY（需先置 DIE_TEMP_RDY_EN），不能以 TEMP_EN 自动清零作为 |
| verified（规则库，人工确认过） | R-MPU-001 | 本模块读 WHO_AM_I(0x75) 得到 0x70（MPU-6500 的编号），不是 MPU6050 手册默认的 0x68 | board-measured：本模块 WHO_AM_I 读出 0x70，不是 MPU6050 手册写的 0x68 |
| verified（规则库，人工确认过） | R-MPU-002 | 温度必须按 原始值/333.87 + 21 换算（temp_lsb_per_c=333.87、temp_offset_c=21.0），不要用 MPU6050 手册的 /340+36.53 | board-measured：所以本模块温度应按 原始值/333.87+21 换算。 |
| verified（规则库，人工确认过） | R-MPU-003 | MPU6050 模块地址 0x68（AD0 接低），与 MAX30102 的 0x57 同挂 i2c0；总线上应答的只有这两个地址 | board-measured：0x57 是 MAX30102，0x68 是 MPU6050 模块（AD0 接低）。 |
| verified（规则库，人工确认过） | R-MPU-007 | 加速度、角速度按 MPU6050 手册的寄存器与换算系数读即可（本模块读数正常：静止 ±2 g 量程 Z 轴原始值约 16524） | board-measured：加速度、角速度按 MPU6050 手册的寄存器和换算系数读数正常，静止时 Z 轴加速度原始值 16524（±2 g 量程 |
