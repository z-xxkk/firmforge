# 新第 2 题 rmt_pulse_meter（RMT 测脉宽）：规则要点（草稿，待负责人审核）

起草：Claude Code，2026-11-03。ESP32-C3 没有脉冲计数器，原第 2 题 pcnt_freq_meter 换成本题（DECISIONS 第 87 条）。
依据：ESP-IDF v6.1 头文件与驱动源码（行号为该版本）、ESP32-C3 能力表 `soc/esp32c3/include/soc/soc_caps.h`。
共同规定 C1–C5 见 A 批（第 83 条）。★ 表示没有直接出处、是我定的规则。

**难度**：2。**接线**：`FF_PIN_PWM_OUT` 经回环线接 `FF_PIN_PWM_IN`（C3 SuperMini 上是 GPIO6 → GPIO7）。

## 接口（`include/pulse_meter.h`）

```c
typedef struct {
    uint32_t hz;              /* 平均频率 */
    uint32_t duty_permille;   /* 平均占空比（高电平占比，千分比） */
    uint32_t periods;         /* 参与平均的完整周期数 */
} pulse_stat_t;

esp_err_t pulse_meter_init(void);
esp_err_t pulse_meter_measure(uint32_t timeout_ms, pulse_stat_t *out);   /* 采集至少 32 个完整周期 */
void      pulse_meter_deinit(void);
```

## 规则

| # | 规则 | 出处 |
|---|---|---|
| 2.1 | 用 RMT 接收通道采集 `FF_PIN_PWM_IN` 上的波形 | `driver/rmt_rx.h:76` `rmt_new_rx_channel` |
| 2.2 | ★ 通道分辨率 `resolution_hz = 10 MHz`（每个计数 100 ns）。**每段电平的持续时间只有 15 位（最多 32767 个计数）**，分辨率定得太高，低频信号的半个周期会放不下 | `hal/rmt_types.h:32–35` |
| 2.3 | ★ 支持的输入范围 1 kHz–100 kHz | ★（10 MHz 分辨率下：100 kHz 一个周期 100 个计数，精度够；1 kHz 半周期 5000 个计数，不超 15 位） |
| 2.4 | "电平保持多久算结束"（`signal_range_max_ns`）换算成计数后不能超过 32767，否则 `rmt_receive` 返回参数错误；★ 本题设 3 ms | `esp_driver_rmt/src/rmt_rx.c:398–400`；`esp32c3/rmt_ll.h:52` |
| 2.5 | "短于多少算毛刺"（`signal_range_min_ns`）换算成滤波计数后不能超过 255，否则同样报参数错误；★ 本题设 1000 ns（100 kHz 的半周期是 5000 ns，不会被滤掉） | `rmt_rx.c:397–399`；`esp32c3/rmt_ll.h:51` |
| 2.6 | **连续方波永远不会"保持电平超过 3 ms"，接收不会自己结束**，所以必须打开分段接收 `flags.en_partial_rx`，在接收回调里分段收集，收够后停止 | `rmt_rx.h:58`；`soc_caps.h:238`（C3 支持接收乒乓模式） |
| 2.7 | 通道内存块 `mem_block_symbols` 必须是偶数，C3 每个通道 48 个 | `rmt_rx.h:36`；`soc_caps.h:237` |
| 2.8 | 接收回调运行在中断里：只拷贝数据、通知任务，不做计算、不打印（共同规定 C5） | `rmt_rx.h:25` |
| 2.9 | 计算：丢掉第一个（可能不完整的）符号；每个符号的周期 = 两段持续时间之和，高电平时间按 `level` 取对应那一段；频率 = 分辨率 × 周期数 / 总计数，占空比 = 高电平总计数 × 1000 / 总计数，都四舍五入 | `hal/rmt_types.h:32–35`（符号格式） |
| 2.10 | `timeout_ms` 内没收够 32 个完整周期（例如没有信号）返回 `ESP_ERR_TIMEOUT`；参数非法返回 `ESP_ERR_INVALID_ARG` | ★ |

## 隐藏测试要点

- 测试用 LEDC 在 `FF_PIN_PWM_OUT` 输出 1 kHz / 250‰、10 kHz / 500‰、100 kHz / 750‰，频率误差 ≤ 1%、占空比误差 ≤ 2%、`periods` ≥ 32；
- 没有信号时，`pulse_meter_measure(200, …)` 在 200–300 ms 内返回 `ESP_ERR_TIMEOUT`；
- 反初始化后再初始化；参数非法。

## 变异体（大模型易错点）

| 变异体 | 错在哪 |
|---|---|
| res_80mhz | 分辨率设 80 MHz：1 kHz 的半周期是 40000 个计数，超过 15 位，测量结果错（规则 2.2） |
| no_partial | 没开分段接收：连续方波永远收不完，总是超时（规则 2.6） |
| min_ns_10us | 毛刺阈值设 10 µs：超过滤波上限，接收报参数错误；即使不报错也会把 100 kHz 的脉冲当毛刺滤掉（规则 2.5） |

## 需要你决定

这道题的规则（尤其 ★ 的 2.2 分辨率、2.3 输入范围、2.4–2.5 两个阈值、2.10 超时）可以吗？通过后我写代码，再一起自检。
