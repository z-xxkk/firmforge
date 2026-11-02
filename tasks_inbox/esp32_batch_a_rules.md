# ESP32-S3 评测题 A 批（第 1–6 题，片上外设 + I2C 扫描）：规则要点（草稿，待负责人审核）

起草：Claude Code，2026-11-02。清单见 tasks_inbox/esp32_task_list.md（DECISIONS 第 82 条）。
依据：ESP-IDF v6.1 头文件（本机 ~/esp/esp-idf-v6.1，行号为该版本）、ESP32-S3 技术参考手册 v1.8、本项目生成的 sdkconfig。
★ 表示没有直接出处、是我定的规则。

## 共同规定（6 道题都适用）

| # | 规则 | 出处 |
|---|---|---|
| C1 | 引脚只用 `hw_config.h` 的宏：脉冲输出 `FF_PIN_PWM_OUT`（GPIO12）、脉冲输入 `FF_PIN_PWM_IN`（GPIO13）、`FF_PIN_UART1_TX/RX`（GPIO10/11）、`FF_PIN_I2C0_SDA/SCL`（GPIO4/5） | DECISIONS 第 66 条、硬件档案 |
| C2 | 所有超时参数单位是**毫秒**；交给 FreeRTOS 时必须用 `pdMS_TO_TICKS()` 换算。本工程 `CONFIG_FREERTOS_HZ=100`，1 个 tick = 10 ms | 项目 sdkconfig |
| C3 | 每个模块都有 `*_deinit()` / `*_stop()`，释放全部资源；释放后可以再次初始化（隐藏测试会连续初始化两次） | ★ |
| C4 | 出错返回 `esp_err_t`，参数非法返回 `ESP_ERR_INVALID_ARG`，不许 `abort` 或无限等待 | ★（与编码 Agent 提示词一致） |
| C5 | 中断回调里只做计数一类的简单操作，不许调用 `printf` / `ESP_LOG*` / 任何会阻塞的函数 | ESP-IDF 中断编程要求；`esp_intr_alloc.h:42` `ESP_INTR_FLAG_IRAM` 说明 |

---

## 1. gpio_edge_count（难度 1）

**接口**（`include/edge_counter.h`）
```c
esp_err_t edge_counter_init(void);
uint32_t  edge_counter_get(void);
void      edge_counter_reset(void);
void      edge_counter_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 1.1 | 输入脚 `FF_PIN_PWM_IN`，只在**上升沿**计数（`GPIO_INTR_POSEDGE`） | `hal/gpio_types.h:93` |
| 1.2 | 用 `gpio_install_isr_service` + `gpio_isr_handler_add`；★ 如果中断服务已经被别人装过（返回 `ESP_ERR_INVALID_STATE`），当作成功继续 | `driver/gpio.h:343、377` |
| 1.3 | 计数变量在中断和任务之间共享，必须是 `volatile` 或原子操作 | ★ |
| 1.4 | ★ 要能正确数 10 kHz 以内的脉冲 | ★ |

**隐藏测试要点**：从 GPIO12 打出 1000 个脉冲（1 kHz），计数正好 1000；清零后再数；测试先装好中断服务再调 init，init 必须仍然成功；反初始化后再初始化。
**变异体**：上下沿都计数（得到 2000）；清零只清了局部变量；中断服务已装时 init 返回错误。

## 2. pcnt_freq_meter（难度 2）

**接口**（`include/freqmeter.h`）
```c
esp_err_t freqmeter_init(void);
esp_err_t freqmeter_measure(uint32_t gate_ms, uint32_t *hz);   /* gate_ms：10–1000 */
void      freqmeter_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 2.1 | 用脉冲计数器（PCNT）在 `FF_PIN_PWM_IN` 上数上升沿 | `driver/pulse_cnt.h:114` |
| 2.2 | 计数器只有 16 位，`high_limit` 必须 ≤ 32767；**必须打开 `accum_count` 并在上下限加观察点**，否则 100 kHz × 1000 ms 会溢出回绕 | `pulse_cnt.h:64–70、299`；`soc_caps.h` |
| 2.3 | 频率 = 计数 × 1000 / gate_ms，四舍五入 | ★ |
| 2.4 | gate_ms 超出 10–1000 返回 `ESP_ERR_INVALID_ARG` | ★ |

**隐藏测试要点**：测试用 LEDC 在 GPIO12 输出 1 k / 10 k / 100 kHz，分别用 100 ms 和 1000 ms 闸门测量，误差 ≤ 1%；非法闸门时间。
**变异体**：没开 `accum_count`（100 kHz × 1000 ms 时结果错）；上下沿都数（翻倍）；漏乘 1000。

## 3. ledc_pwm（难度 2）

**接口**（`include/pwm_out.h`）
```c
esp_err_t pwm_init(uint32_t freq_hz, uint32_t duty_permille);   /* 100 Hz–100 kHz，0–1000‰ */
esp_err_t pwm_set_duty(uint32_t duty_permille);
void      pwm_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 3.1 | 输出脚 `FF_PIN_PWM_OUT`；ESP32-S3 的 LEDC 只有低速模式 | TRM LEDC 一章 |
| 3.2 | 占空比分辨率按频率选：最大 14 位，且"频率 × 2^分辨率"不能超过时钟（80 MHz）；用 `ledc_find_suitable_duty_resolution` 或自己算 | `driver/ledc.h:133`；`soc_caps.h:241` |
| 3.3 | 占空比 = 千分比 × 2^分辨率 / 1000 | ★ |
| 3.4 | `ledc_set_duty` 之后必须调用 `ledc_update_duty` 才生效 | `driver/ledc.h:169、283` |
| 3.5 | 参数超范围返回 `ESP_ERR_INVALID_ARG` | ★ |

**隐藏测试要点**：在 GPIO13 测频率（PCNT）和高电平比例（均匀采样），1 kHz / 10 kHz / 100 kHz，频率误差 ≤ 1%，占空比误差 ≤ 2%；改占空比后立即生效；读 LEDC 定时器寄存器核对分辨率。
**变异体**：分辨率固定 13 位（100 kHz 配置失败）；把千分比当百分比；改占空比没调 `ledc_update_duty`。

## 4. gptimer_tick（难度 2）

**接口**（`include/tick.h`）
```c
esp_err_t tick_start(uint32_t hz);      /* 1 Hz–10 kHz */
uint32_t  tick_count(void);
esp_err_t tick_stop(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 4.1 | 计数分辨率 1 MHz（`resolution_hz = 1000000`），报警值 = 1000000 / hz | `driver/gptimer.h:25` |
| 4.2 | 必须开 `auto_reload_on_alarm`，`reload_count = 0`，否则只触发一次 | `gptimer.h:166–168` |
| 4.3 | 先 `gptimer_enable` 再 `gptimer_start`；停止时 stop → disable → 删除 | `gptimer.h:205、239` |
| 4.4 | 回调里只计数，返回值表示是否需要切换任务（这里返回 false） | `gptimer.h:140` |

**隐藏测试要点**：1 kHz 运行 1000 ms，计数 1000±2；10 kHz 同理；停止后计数不再变；再次启动正常。
**变异体**：报警值算成 1000 / hz（快 1000 倍）；没开自动重装（只计 1 次）；没调 `gptimer_enable`（启动失败）。

## 5. uart_loopback（难度 2）

**接口**（`include/link.h`）
```c
esp_err_t link_init(uint32_t baud);
int       link_write(const uint8_t *data, size_t len);                     /* 返回写入字节数，失败 -1 */
int       link_read_line(char *buf, size_t cap, uint32_t timeout_ms);      /* 返回行长度（不含换行），超时 -1 */
void      link_deinit(void);
```

| # | 规则 | 出处 |
|---|---|---|
| 5.1 | 用 UART1，TX = `FF_PIN_UART1_TX`、RX = `FF_PIN_UART1_RX`，RTS/CTS 写 `UART_PIN_NO_CHANGE` | `driver/uart.h:20` |
| 5.2 | **v6.1 的 `uart_set_pin` 是宏，接受 5 个或 7 个参数**（第 6、7 个是 DTR/DSR）；其他个数编译能过，但运行时报错 | `driver/uart.h:394–410` |
| 5.3 | `uart_driver_install` 的接收缓冲区必须大于硬件 FIFO（128 字节） | `driver/uart.h:119` 说明 |
| 5.4 | `timeout_ms` 用 `pdMS_TO_TICKS` 换算（共同规定 C2） | `uart.h:593` |
| 5.5 | ★ 一行超过 `cap - 1` 字节时，返回前 `cap - 1` 字节，丢掉这一行剩下的部分直到换行符 | ★ |

**隐藏测试要点**：经回环发送 "hello\n"，读回 "hello"（长度 5）；无数据时 200 ms 超时，实际用时 200–300 ms；超长行截断；读 UART1 分频寄存器核对波特率；反初始化后再初始化。
**变异体**：超时直接当 tick 用（等 10 倍时间）；没调 `uart_set_pin`（数据走默认引脚，收不到）；接收缓冲区写 128（安装失败）。

## 6. i2c_bus_scan（难度 2）

**接口**（`include/bus_scan.h`）
```c
/* found：调用方给的数组；cap：数组大小；*n：实际应答的地址个数（可能大于 cap，只写前 cap 个） */
esp_err_t bus_scan(i2c_master_bus_handle_t bus, uint8_t *found, size_t cap, size_t *n);
```

| # | 规则 | 出处 |
|---|---|---|
| 6.1 | 用新版接口 `i2c_master_probe` 逐个探测；**不许用旧接口 `i2c_driver_install` 等**（v6.1 仍能编译，但和新驱动混用会在运行时冲突） | `driver/i2c_master.h:260`；本项目实测（DECISIONS 第 79 条前后的发现） |
| 6.2 | 每个地址探测超时 ≤ 50 ms | ★ |
| 6.3 | 扫描范围 0x08–0x77，跳过 I2C 规范保留的地址（0000 XXX 与 1111 XXX 两组） | NXP UM10204 Rev 7.0 p.16 §3.1.12 表 4（资料编号 nxp-i2c-um10204） |
| 6.4 | 结果按地址从小到大；`*n` 是实际个数，`found` 只写前 `cap` 个，不能越界 | ★ |
| 6.5 | 总线由调用方创建并传入，模块不能自己创建或删除总线 | ★（为了隐藏测试能独立读寄存器，清单第四节） |

**隐藏测试要点**：结果正好是 {0x57, 0x68}；整个扫描用时 < 2 秒；cap = 1 时 `*n` = 2、只写一个、数组后面的哨兵字节不被改；读 I2C0 控制寄存器确认主机模式。
**变异体**：用旧接口（合规检查拦截）；`*n` 被截成 cap；不检查 cap 越界写。

---

## 需要你决定

1. 这 6 道题的规则（尤其是 ★ 的 1.2、1.4、2.3、3.3、5.5、6.2–6.5）可以吗？
2. 第 6 题的保留地址依据是 NXP 的 I2C 规范 UM10204（nxp.com 官方免费下载，约 1.4 MB）。同意的话我下载并登记进资料清单。
