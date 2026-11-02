# MPU6050 数据按帧从串口发出（综合）

MPU6050 每产生一次"数据就绪"中断，就读一组数据，按下面的帧格式从 UART1 发出去。

## 硬件

- MPU6050：地址 `FF_IMU_ADDR`（0x68），INT 脚接 `FF_PIN_IMU_INT`（GPIO7），I2C 总线由调用方创建好传入；
- UART1：TX `FF_PIN_UART1_TX`（GPIO10）、RX `FF_PIN_UART1_RX`（GPIO11），115200 bps、8N1，不用 RTS/CTS。

## 接口（`include/logger.h`，不能修改）

- `esp_err_t logger_start(i2c_master_bus_handle_t bus, uint32_t rate_hz)`：采样率 10–100 Hz；
- `esp_err_t logger_stop(void)`：停止并释放全部资源（包括 UART 驱动、自己挂上的 I2C 设备），之后可以再次启动。

## 帧格式（19 字节）

| 字节 | 内容 |
|---|---|
| 0–1 | 帧头 0xA5 0x5A |
| 2 | 序号：每帧加 1，255 之后回到 0 |
| 3–16 | 加速度 X、Y、Z，陀螺 X、Y、Z，温度：7 个 int16 原始值，**小端** |
| 17–18 | CRC-16/CCITT-FALSE（多项式 0x1021、初值 0xFFFF、不反转、不异或），覆盖字节 2–16，**小端** |

## 要求

1. MPU6050 配置同"数据就绪中断采集"：唤醒（时钟源 X 轴陀螺 PLL）、DLPF_CFG = 3、量程 ±2 g / ±250 °/s、采样率由 SMPLRT_DIV 决定、INT 高电平有效推挽锁存且读任何寄存器即清除、只开数据就绪中断；
2. 中断处理函数里只通知任务，I2C 读写和串口发送在任务里做；
3. UART1 的接收缓冲区至少 1024 字节，接收方向留给别的代码读（不要在本模块里读走）；
4. 参数非法返回 `ESP_ERR_INVALID_ARG`，已经启动时再启动返回 `ESP_ERR_INVALID_STATE`。
