# MPU6050 数据就绪中断采集

让 MPU6050 按固定采样率产生"数据就绪"中断，ESP32-S3 每收到一次中断就读一组数据放进队列。

## 硬件

- 器件地址 `FF_IMU_ADDR`（0x68）；I2C 总线由调用方创建好传入；
- MPU6050 的 INT 脚接 `FF_PIN_IMU_INT`（GPIO7）。

## 接口（`include/mpu_stream.h`，不能修改）

- `esp_err_t mpu_stream_start(i2c_master_bus_handle_t bus, uint32_t rate_hz, QueueHandle_t q)`：采样率 10–200 Hz；每组数据（`mpu_sample_t`）放进 `q`；
- `esp_err_t mpu_stream_stop(void)`：停止采集，释放资源（只删自己挂上的设备），之后可以再次启动；
- `uint32_t mpu_stream_dropped(void)`：因队列满而丢掉的组数。

## 要求

1. 初始化：确认 WHO_AM_I、唤醒器件（时钟源 X 轴陀螺 PLL）、DLPF_CFG = 3、量程 ±2 g / ±250 °/s；
2. 采样率由 SMPLRT_DIV 决定，公式按数据手册（注意低通滤波开关对陀螺输出率的影响）；
3. INT 脚：高电平有效、推挽输出；锁存到被清除为止，读任何寄存器即清除；只打开数据就绪中断；
4. GPIO 上升沿中断；**中断处理函数里只通知任务**，I2C 读写在任务里做；
5. 每组数据一次连续读出 14 字节（加速度、温度、陀螺，高字节在前）；
6. 队列满时不等待，丢掉这一组并计数；
7. 停止时先关掉器件的中断输出，再移除 GPIO 中断、结束任务；停止后队列不再增加；
8. 参数非法返回 `ESP_ERR_INVALID_ARG`，已经启动时再启动返回 `ESP_ERR_INVALID_STATE`。
