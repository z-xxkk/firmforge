# 嵌入式测试方案调研（2026-10-31）

负责人手上的板子：STM32F103C8T6、ESP32-C3、ESP32-S3、ESP32-P4、C51（DECISIONS 第 68 条）。
问题：编码 Agent 写的硬件层代码，怎样自动判断"在板子上对不对"。

## 一、可用的三类手段

### 1. 寄存器级仿真

| 工具 | 支持的芯片 | 能仿真的外设 | 限制 | 来源 |
|---|---|---|---|---|
| Renode（开源、离线） | STM32F103（Blue Pill 有现成脚本） | GPIO×7、AFIO、EXTI、USART×5、I2C×2、定时器×14、DMA×2、NVIC | **没有 SPI、ADC、看门狗、RTC、CAN、USB**；RCC 只有一个固定值寄存器，时钟配置不会真正生效；定时器频率固定 10 MHz；Flash 只是普通内存 | renode/platforms/cpus/stm32f103.repl |
| Wokwi（云端） | STM32F103C8（Blue Pill）、STM32C031、STM32L031、ESP32-C3、ESP32-S3、ESP32-P4（测试版） | 外设和传感器元件多，能看引脚波形 | **必须联网**、需要令牌；免费账号每 30 天 50 分钟、单次最长 5 分钟；不支持 8051；ESP32 不支持蓝牙 | docs.wokwi.com 支持硬件页、Wokwi CI 页 |
| Espressif QEMU（开源、离线） | ESP32-C3、ESP32-S3 | UART、中断矩阵、定时器、SPI Flash、加密外设、TWAI（CAN） | **没有 Wi-Fi、蓝牙、USB、通用 SPI、RMT**；官方不提供支持 | espressif/esp-toolchain-docs qemu README |

结论：仿真能查"寄存器配没配对、中断能不能触发、串口输出对不对"，但外设覆盖不全，也查不了真实时序和电气问题（IoT-SkillsBench 也指出仿真器外设保真度有限）。

### 2. 真实开发板（硬件在环）

- **已有做法**：
  - 用第二块单片机当"测试仪"，给被测板发激励（按键、串口、模拟 I2C/SPI 从机）、采集它的输出；Tweede Golf 的 embedded-conformity、FreeHIL、RIOT 的 UART 测试模块都是这种做法。
  - Agent 直接调用"编译 → 烧录 → 读串口"工具（Agentic HIL、PlatformIO-MCP）。
  - Embedded Arena（arXiv 2606.16190，2026-06）报告：前沿模型**没有硬件反馈时部署成功率为 0%**，有硬件在环反馈后 3 轮内就能首次成功（场景是在单片机上部署边缘 AI 模型）。
- **烧录与读取**：
  - STM32 用 ST-Link 加 OpenOCD；还能通过调试口**直接读出运行中的寄存器值**。
  - ESP32-C3/S3 自带 USB 串口和 USB-JTAG，用 esptool 烧录，用 OpenOCD 读寄存器。
- **乐鑫官方测试框架** pytest-embedded：同一套测试可以在真实板、QEMU、Wokwi 上跑，支持一次测多块板。

### 3. 电脑上的单元测试加替身（已有）

适合逻辑层：协议、状态机、算法。

## 二、建议的分层测试

| 层 | 测什么 | 工具 | 什么时候跑 |
|---|---|---|---|
| T1 电脑单元测试 | 逻辑层 | 现有判定器 | 每次 |
| T2 仿真 | 硬件层的寄存器配置、中断、串口输出 | STM32 用 Renode；ESP32 用 QEMU | 每次，离线、免费 |
| T3 真实开发板 | 真实行为和时序 | 被测板 + ESP32-S3 当测试仪 + 读寄存器 | 前两层通过后；评测和演示 |

**规则和测试的结合**（本项目独有）：规则集里的寄存器事实（例如"初始化后 GPIOA_CRL 的 PA5 位段 = 推挽输出 50 MHz"），在 T2、T3 都可以**读出实际寄存器值来比对**，直接得到"代码在板上是否真的遵守了手册"的证据。

## 三、板子用途建议

- **主目标 STM32F103C8T6**：
  - ARM Cortex-M3，有官方 SVD 文件，能做寄存器事实核对；
  - Renode 和 Wokwi 都支持；
  - 现有交叉工具链（arm-none-eabi-gcc）可以直接用。
- **ESP32-S3 当测试仪**：外设多、USB 方便。
- **ESP32-C3 当第二个目标**：RISC-V 架构，开发框架是 ESP-IDF，和 STM32 差异大，正好演示"同一个需求，不同芯片写出不同代码"；QEMU 能仿真它。
- **ESP32-P4、C51 暂不纳入**：P4 的仿真支持还在测试阶段；C51 没有 SVD、没有仿真支持，工具链也完全不同。

## 四、对评审可复现性的影响

评审手上没有我们的接线，T3 他们复现不了。所以：
- T1、T2 必须一键离线可复现，这是评分里"可复现性"的依据；
- T3 提供录像、日志和寄存器读数作为证据。

## 来源

- Renode：[stm32f103.repl](https://raw.githubusercontent.com/renode/renode/master/platforms/cpus/stm32f103.repl)、[支持的板子](https://renode.readthedocs.io/en/latest/introduction/supported-boards.html)
- Wokwi：[支持的硬件](https://docs.wokwi.com/getting-started/supported-hardware)、[ESP32 指南](https://docs.wokwi.com/guides/esp32)、[Wokwi CI](https://docs.wokwi.com/wokwi-ci/getting-started)
- Espressif QEMU：[README](https://github.com/espressif/esp-toolchain-docs/blob/main/qemu/README.md)、[ESP-IDF QEMU 指南](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c3/api-guides/tools/qemu.html)
- [pytest-embedded](https://github.com/espressif/pytest-embedded)
- 测试仪做法：[Tweede Golf HAL 测试套件](https://tweedegolf.nl/en/blog/240/PoC-for-universal-hardware-in-the-loop-HAL-test-suite/)、[FreeHIL](https://forum.openhardware.science/t/introducing-freehil-portable-open-source-hardware-in-the-loop-hil-testing-infrastructure/7904)、[RIOT UART HIL 论文](https://inet.haw-hamburg.de/thesis/completed/yegor-yefremov)
- Agent 接硬件：[Embedded Arena（arXiv 2606.16190）](https://arxiv.org/html/2606.16190)、[Agentic HIL](https://mcpservers.org/fr/servers/agentic-hil/agentic-hil)
- DeepSeek 输入缓存：[KV cache 指南](https://api-docs.deepseek.com/guides/kv_cache)、[价格](https://api-docs.deepseek.com/quick_start/pricing)（缓存命中的输入价约为未命中的 1/50）
