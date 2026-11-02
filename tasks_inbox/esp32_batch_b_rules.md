# ESP32-S3 评测题 B 批（第 7–11 题，MPU6050、MAX30102、综合）：规则要点（草稿，待负责人审核）

起草：Claude Code，2026-11-02。清单见 tasks_inbox/esp32_task_list.md（DECISIONS 第 82 条），共同规定 C1–C5 见 A 批（第 83 条）。
页码对应资料清单 hardware/esp32s3_supermini/sources.toml 里登记的版本：
mpu6050-rm = 寄存器手册 RM-MPU-6000A rev 4.0，mpu6050-ps = 产品规格书 PS-MPU-6000A rev 3.1（两者都是官方文档的非官方镜像，DECISIONS 第 79 条），max30102-ds = MAX30102 数据手册。
★ 表示没有直接出处、是我定的规则。

## 驱动题的共同规定

| # | 规则 | 出处 |
|---|---|---|
| D1 | I2C 总线由调用方创建并传入（`i2c_master_bus_handle_t`），驱动只用 `i2c_master_bus_add_device` 挂自己的设备，反初始化时只删自己的设备 | ★（清单第四节） |
| D2 | 器件地址用 `hw_config.h` 的 `FF_IMU_ADDR`（0x68）、`FF_PPG_ADDR`（0x57） | 硬件档案 |
| D3 | 设备时钟 ≤ 400 kHz | mpu6050-ps（I2C 快速模式 400 kHz）；max30102-ds（I2C 最高 400 kHz） |
| D4 | 多字节读用**一次**连续读（`i2c_master_transmit_receive`：先写寄存器地址，再读 N 字节），保证读到的是同一时刻的数据 | mpu6050-rm p.30 数据寄存器说明；max30102-ds p.16 读 FIFO 伪代码 |

---

## 7. mpu6050_drv_idf（难度 3）

**接口**（`include/mpu6050.h`）
```c
typedef enum { MPU_ACCEL_2G = 0, MPU_ACCEL_4G, MPU_ACCEL_8G, MPU_ACCEL_16G } mpu_accel_range_t;
typedef enum { MPU_GYRO_250 = 0, MPU_GYRO_500, MPU_GYRO_1000, MPU_GYRO_2000 } mpu_gyro_range_t;
typedef struct { int16_t accel[3], gyro[3], temp; } mpu_raw_t;
typedef struct { float accel_g[3], gyro_dps[3], temp_c; } mpu_data_t;

esp_err_t mpu_init(i2c_master_bus_handle_t bus, mpu_accel_range_t accel, mpu_gyro_range_t gyro);
esp_err_t mpu_read_raw(mpu_raw_t *out);
esp_err_t mpu_read(mpu_data_t *out);
void      mpu_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 7.1 | 初始化先读 WHO_AM_I（0x75），不是 0x68 返回 `ESP_ERR_NOT_FOUND` | mpu6050-rm p.46 |
| 7.2 | **上电默认处于睡眠模式**，必须把 PWR_MGMT_1（0x6B）的 SLEEP 位（bit6）清零，否则数据全是 0 | mpu6050-rm p.42 |
| 7.3 | ★ 时钟源选 X 轴陀螺的 PLL（CLKSEL = 1） | ★（规格书建议用陀螺作时钟源，原文待逐字核对） |
| 7.4 | 加速度量程写 ACCEL_CONFIG（0x1C）bit4–3，陀螺量程写 GYRO_CONFIG（0x1B）bit4–3，**要左移 3 位** | mpu6050-rm 寄存器 27、28 |
| 7.5 | ★ 数字低通 CONFIG（0x1A）的 DLPF_CFG = 3 | ★ |
| 7.6 | 从 0x3B 起一次读 14 字节：加速度 XYZ、温度、陀螺 XYZ，每个 16 位**高字节在前** | mpu6050-rm p.30–31 |
| 7.7 | 换算：加速度 16384 / 8192 / 4096 / 2048 LSB/g；陀螺 131 / 65.5 / 32.8 / 16.4 LSB/(°/s) | mpu6050-ps p.13、p.12 |
| 7.8 | 温度 = 原始值（有符号）/ 340 + 36.53 | mpu6050-rm p.31 |

**隐藏测试要点**：测试在同一总线上挂自己的设备，独立读 0x6B（SLEEP = 0）、0x1C、0x1B、0x1A 核对配置；板子静止时 \|a\| 在 0.85–1.15 g、各轴角速度 < 10 °/s、温度 0–60 °C；`mpu_read_raw` 与测试自己连续读的结果一致（允许噪声误差）。
**变异体**：没清 SLEEP 位；高低字节反；量程没左移；温度公式缺 36.53 偏移。

## 8. mpu6050_drdy（难度 3）

**接口**（`include/mpu_stream.h`，依赖第 7 题的 `mpu6050.h` 类型）
```c
/* 每个数据就绪中断读一次，把 mpu_raw_t 放进 q；rate_hz：10–200 */
esp_err_t mpu_stream_start(i2c_master_bus_handle_t bus, uint32_t rate_hz, QueueHandle_t q);
esp_err_t mpu_stream_stop(void);
uint32_t  mpu_stream_dropped(void);     /* 队列满丢掉的条数 */
```

| # | 规则 | 出处 |
|---|---|---|
| 8.1 | 采样率 = 陀螺输出率 / (1 + SMPLRT_DIV)；**低通开着（DLPF_CFG 1–6）时陀螺输出率是 1 kHz，关着（0 或 7）是 8 kHz** | mpu6050-rm p.12 |
| 8.2 | 本题 DLPF_CFG = 3（同 7.5），所以 SMPLRT_DIV = 1000 / rate_hz − 1 | 由 8.1 推出 |
| 8.3 | INT_PIN_CFG（0x37）：高电平有效、推挽（INT_LEVEL = 0、INT_OPEN = 0）；★ 锁存直到清除（LATCH_INT_EN = 1）且读任何寄存器即清除（INT_RD_CLEAR = 1） | mpu6050-rm p.27 |
| 8.4 | INT_ENABLE（0x38）的 DATA_RDY_EN（bit0）置 1 | mpu6050-rm p.28 |
| 8.5 | GPIO7（`FF_PIN_IMU_INT`）上升沿中断；**中断里只通知任务**（任务通知或信号量的 FromISR 版本），I2C 读写在任务里做 | 共同规定 C5 |
| 8.6 | ★ 队列满时不等待，丢掉这一条并计数 | ★ |
| 8.7 | 停止时：先关 INT_ENABLE，再移除中断、删除任务；之后队列不再增加 | ★ |

**隐藏测试要点**：100 Hz 运行 2 秒，收到 200±10 条，间隔中位数 10±1 ms；50 Hz 同理；独立读 SMPLRT_DIV = 9（100 Hz 时）、INT_ENABLE bit0 = 1；停止后不再有数据；用长度 2 的队列且不取数据时，丢弃计数增加。
**变异体**：SMPLRT_DIV 按 8 kHz 算（采样率错 8 倍）；中断里直接读 I2C（崩溃）；开了锁存但从不读寄存器清除（只来 1 次中断）。

## 9. max30102_fifo（难度 3）

**接口**（`include/max30102.h`）
```c
typedef struct { uint32_t red, ir; } ppg_sample_t;
esp_err_t ppg_init(i2c_master_bus_handle_t bus, uint16_t rate_sps);   /* 50、100、200、400 */
int       ppg_read(ppg_sample_t *buf, size_t cap);                     /* 返回读到的样本数 0..cap，出错 -1 */
void      ppg_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 9.1 | 地址 0x57（写 0xAE、读 0xAF）；PART_ID（0xFF）必须是 0x15，否则 `ESP_ERR_NOT_FOUND` | max30102-ds p.29、p.11 |
| 9.2 | 先软复位：MODE_CONFIG（0x09）RESET 位（bit6）置 1，**等它自动清零**再配置（★ 最多等 100 ms） | max30102-ds p.18 |
| 9.3 | FIFO_CONFIG（0x08）：★ 不平均（SMP_AVE = 0）、★ 满了回绕（FIFO_ROLLOVER_EN = 1） | max30102-ds p.17 |
| 9.4 | 配置完把 FIFO 写指针（0x04）、溢出计数（0x05）、读指针（0x06）清零 | max30102-ds p.16 |
| 9.5 | 模式 MODE = 011（SpO2 模式：红光 + 红外） | max30102-ds p.18 |
| 9.6 | SPO2_CONFIG（0x0A）：采样率按表 6 选（50→000、100→001、200→010、400→011）；★ LED_PW = 11（411 µs，18 位）；★ ADC 量程 01（4096 nA） | max30102-ds p.19（表 6、表 7） |
| 9.7 | ★ LED 电流 LED1_PA（0x0C）、LED2_PA（0x0D）= 0x24 | max30102-ds p.24（0.2 mA/LSB） |
| 9.8 | 可读样本数 = (写指针 − 读指针) mod 32，**写指针比读指针小时要回绕** | max30102-ds p.16 伪代码 |
| 9.9 | 每个样本 6 字节：红光 3 字节、红外 3 字节，高字节在前；**数据左对齐、不要移位**，取低 18 位（& 0x3FFFF） | max30102-ds p.14–15（表 1、表 2） |

**隐藏测试要点**：独立读 PART_ID、MODE、SPO2_CONFIG、FIFO_CONFIG 核对；100 sps 下每隔不规则的时间读，3 秒累计 300±10 个样本（考验指针回绕）；所有样本 ≤ 0x3FFFF；反初始化后再初始化。
**变异体**：指针没回绕；复位后没等 RESET 清零就配置（配置被复位冲掉）；采样率编码错（100 sps 写成表 6 的错误编码）。

## 10. max30102_temp（难度 2）

**接口**（`include/max30102_temp.h`）
```c
esp_err_t ppg_temp_read(i2c_master_bus_handle_t bus, float *celsius);
float     ppg_temp_convert(uint8_t tint, uint8_t tfrac);    /* 纯换算，便于测零下 */
```

| # | 规则 | 出处 |
|---|---|---|
| 10.1 | 置 TEMP_EN（0x21 bit0）启动一次测量，等 DIE_TEMP_RDY（中断状态 2，0x01 bit1）或 TEMP_EN 自动清零；★ 最多等 100 ms | max30102-ds p.22、p.26 |
| 10.2 | 温度 = TINT（**二进制补码，有符号**，每位 1 °C）+ TFRAC（bit3–0，每位 0.0625 °C） | max30102-ds p.22 |
| 10.3 | ★ 只读温度，不复位、不改其他配置（可以和第 9 题同时用） | ★ |

**隐藏测试要点**：板上读出 15–45 °C，乘 16 是整数；连续读两次都成功；测试先把 MODE 设成某个值，读完温度后 MODE 不变；`ppg_temp_convert(0xFF, 0x0)` = −1.0、`(0xE7, 0x8)` = −24.5（零下只能这样测）。
**变异体**：TINT 当无符号（零下错）；没等测量完成就读；TFRAC 按 0.1 °C 换算。

## 11. imu_uart_logger（难度 3，综合）

**接口**（`include/logger.h`）
```c
esp_err_t logger_start(i2c_master_bus_handle_t bus, uint32_t rate_hz);   /* 10–100 */
esp_err_t logger_stop(void);
```

**帧格式**（★ 本题自定，写进需求）：
| 字节 | 内容 |
|---|---|
| 0–1 | 帧头 0xA5 0x5A |
| 2 | 序号（每帧加 1，255 之后回到 0） |
| 3–16 | 加速度 XYZ、陀螺 XYZ、温度原始值，共 7 个 int16，**小端** |
| 17–18 | CRC-16/CCITT-FALSE（多项式 0x1021、初值 0xFFFF），覆盖字节 2–16，**小端** |

| # | 规则 | 出处 |
|---|---|---|
| 11.1 | 数据就绪方式同第 8 题（MPU6050 量程固定 ±2 g、±250 °/s） | 第 7、8 题 |
| 11.2 | 从 UART1（115200 bps，引脚同第 5 题）发出；★ 接收缓冲区 ≥ 1024 字节，接收方向留给别人读 | 第 5 题 |
| 11.3 | CRC 参数 | CRC-16/CCITT-FALSE 公开参数表（旧题 crc16 已核对过同类参数） |

**隐藏测试要点**：测试从 UART1 读回环数据，按帧解析：CRC 全对、序号连续、50 Hz 下 2 秒收到 100±5 帧、加速度原始值换算后 \|a\| ≈ 1 g。
**变异体**：字段写成大端；CRC 把帧头也算进去；序号不增加。

---

## 需要你决定

1. 这 5 道题的规则（★ 的 7.3、7.5、8.3 锁存方式、8.6、8.7、9.2 等待时间、9.3、9.6–9.7、10.1 等待时间、10.3、11 帧格式、11.2）可以吗？
2. 7.3"用陀螺作时钟源"我记得规格书里有建议，但这次没在页面里逐字找到原文，所以标了 ★；你同意的话保留，否则改成默认内部时钟。
