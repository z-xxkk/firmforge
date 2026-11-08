# 上板评测汇总

判定：固定判定规则的合规检查 → 编译 → 上板隐藏测试（所有配置相同）。skipped 不计入通过率。

| 配置 | 通过 / 有效次数 | 通过率 | 隐藏用例通过率 | 平均尝试次数 | 平均 token | 平均成本（元） | 平均用时（秒） | 跳过 |
|---|---|---|---|---|---|---|---|---|
| single | 5 / 12 | 42% | 69% | 1.1 | 13339 | 0.0461 | 109 | 0 |
| none | 7 / 12 | 58% | 81% | 2.8 | 63045 | 0.1809 | 281 | 0 |
| unverified | 7 / 12 | 58% | 77% | 2.9 | 152911 | 0.2739 | 302 | 0 |
| verified | 4 / 12 | 33% | 71% | 3.3 | 227241 | 0.4051 | 417 | 0 |

## 每道题（通过次数 / 有效次数）

| 题目 | single | none | unverified | verified |
|---|---|---|---|---|
| ledc_pwm | 2 / 2 | 2 / 2 | 2 / 2 | 1 / 2 |
| max30102_fifo | 1 / 2 | 2 / 2 | 2 / 2 | 2 / 2 |
| max30102_temp | 0 / 2 | 0 / 2 | 1 / 2 | 0 / 2 |
| mpu6050_drdy | 1 / 2 | 2 / 2 | 1 / 2 | 1 / 2 |
| mpu6050_drv_idf | 1 / 2 | 1 / 2 | 0 / 2 | 0 / 2 |
| rmt_pulse_meter | 0 / 2 | 0 / 2 | 1 / 2 | 0 / 2 |

## 没通过的卡在哪一层

| 配置 | 层 | 次数 |
|---|---|---|
| single | board_test | 7 |
| none | board_test | 5 |
| unverified | board_test | 4 |
| unverified | build | 1 |
| verified | board_test | 8 |

## 未核对规则（每题生成一次，各次运行复用；费用不含在上表里）

| 题目 | 规则数 | token | 成本（元） |
|---|---|---|---|
| ledc_pwm | 10 | 45541 | 0.0573 |
| max30102_fifo | 9 | 40775 | 0.0521 |
| max30102_temp | 15 | 62602 | 0.0805 |
| mpu6050_drdy | 13 | 44422 | 0.0591 |
| mpu6050_drv_idf | 16 | 47473 | 0.0643 |
| rmt_pulse_meter | 16 | 55559 | 0.0724 |
| 合计 | | | 0.3857 |

## 披露：来自上板实测记录的规则（DECISIONS 第 95 条）

这些规则的依据是在本板上实测的结果，而不是芯片或器件资料。

| 配置 | 规则 | 内容 | 引用 |
|---|---|---|---|
| verified（规则库，人工确认过） | R-I2C-008 | 本板 I2C0 的 SDA=GPIO4、SCL=GPIO5，MPU6050 模块与 MAX30102 模块都接在这两条线上 | board-measured：I2C0 SDA=GPIO4、SCL=GPIO5，面包板接 MPU6050 模块与 MAX30102 模块 |
| verified（规则库，人工确认过） | R-I2C-009 | 本板 I2C 在 100 kHz 和 400 kHz 下读 MPU6050 均无错，scl_speed_hz 取 100000 或 400000 都可用 | board-measured：100 kHz 和 400 kHz 两种速率下 50 次读取都没有出错。 |
| verified（规则库，人工确认过） | R-MAX-009 | 本板 I2C0 上只有 0x57（MAX30102）和 0x68（MPU6050 模块）两个地址应答，扫描时不要期待其它地址 | board-measured：结果：I2C 总线上只有 0x57 和 0x68 两个地址应答。0x57 是 MAX30102，0x68 是 MPU60 |
| verified（规则库，人工确认过） | R-MPU-001 | 本模块读 WHO_AM_I(0x75) 得到 0x70（MPU-6500 的编号），不是 MPU6050 手册默认的 0x68 | board-measured：本模块 WHO_AM_I 读出 0x70，不是 MPU6050 手册写的 0x68 |
| verified（规则库，人工确认过） | R-MPU-002 | 温度必须按 原始值/333.87 + 21 换算（temp_lsb_per_c=333.87、temp_offset_c=21.0），不要用 MPU6050 手册的 /340+36.53 | board-measured：所以本模块温度应按 原始值/333.87+21 换算。 |
| verified（规则库，人工确认过） | R-MPU-003 | MPU6050 模块地址 0x68（AD0 接低），与 MAX30102 的 0x57 同挂 i2c0；总线上应答的只有这两个地址 | board-measured：0x57 是 MAX30102，0x68 是 MPU6050 模块（AD0 接低）。 |
| verified（规则库，人工确认过） | R-MPU-007 | 加速度、角速度按 MPU6050 手册的寄存器与换算系数读即可（本模块读数正常：静止 ±2 g 量程 Z 轴原始值约 16524） | board-measured：加速度、角速度按 MPU6050 手册的寄存器和换算系数读数正常，静止时 Z 轴加速度原始值 16524（±2 g 量程 |
| unverified（max30102_temp，未核对） | R-I2C-005 | i2c0 用 SDA=GPIO4、SCL=GPIO5，所有 I2C 器件共用这条总线 | board-measured：I2C0 SDA=GPIO4、SCL=GPIO5 |
| unverified（max30102_temp，未核对） | R-I2C-006 | SCL 用 100 kHz 或 400 kHz 都可正常通信，任选其一即可 | board-measured：100 kHz 和 400 kHz 两种速率下 50 次读取都没有出错 |
| unverified（max30102_temp，未核对） | R-MAX30102-007 | 测温流程：先置中断使能 2(0x03) 的 DIE_TEMP_RDY_EN，再置 TEMP_EN(0x21 bit0)，等 DIE_TEMP_RDY 置位后才读 TINT(0x1F)/TFRAC(0x20)；禁止以 TEMP_EN 自动清零作为测完标志 | board-measured：测温要等 DIE_TEMP_RDY（需先置 DIE_TEMP_RDY_EN），不能以 TEMP_EN 自动清零作为测完的 |
| unverified（mpu6050_drdy，未核对） | R-MPU-002 | 本板器件读 WHO_AM_I(0x75) 返回 0x70（MPU-6500 编号），ID 校验按 0x70 判定，禁止按 0x68 拒绝器件 | board-measured：2026-11-03 上板读 WHO_AM_I(0x75)=0x70，是 MPU-6500 的编号 |
