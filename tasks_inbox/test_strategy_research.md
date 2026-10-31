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

---

# 第 2 次调研：业界一般怎么测、为什么只靠软件很难测（2026-10-31）

负责人否决了上面第一版方案：T2 每种板子要用不同的仿真软件，太麻烦。

## 一、业界的一般做法

- **按真实程度分层（V 模型，汽车、航天最典型）**：
  - MIL：只测算法模型；
  - SIL：代码在电脑上跑；
  - PIL：代码在目标处理器上跑，外部环境用电脑模拟；
  - HIL：真实控制器接一个实时模拟的外部环境。
  - 越往后越真实，也越贵。SIL 查不出时序问题，HIL 最贵，只用在依赖时序和硬件的部分。（MathWorks V 模型说明及多篇综述）
- **小团队的常见做法（Grenning《Test-Driven Development for Embedded C》）**："双目标"：同一份代码既能在电脑上编译测试，也能在板子上跑。大部分测试在电脑上用替身跑，因为板子是瓶颈。但驱动这类和硬件强相关的代码，这个办法用不上。（Grenning 一书；Meeting C++ 2024 Penning 的报告）
- **一个命令跑电脑和板子**：
  - PlatformIO：`pio test` 可以在电脑上跑，也可以自动编译、烧录到板子、通过串口收结果；STM32 和 ESP32 都支持。
  - Zephyr 的 Twister：同一份测试声明，可以跑在 native_sim、QEMU 或真实板上（`--device-testing` 加硬件清单）。
  - 乐鑫的 pytest-embedded：同一套测试可以跑在真实板、QEMU、Wokwi 上。
- **Memfault 的 Interrupt 博客讨论**：应用逻辑做单元测试；裸机驱动和硬件相关代码难做单元测试，更适合在设备上验证。

## 二、为什么只靠软件（仿真 / 在线）很难测

1. **外设要一个个建模，芯片和外设的组合太多**：固件直接读写外设寄存器，每种外设的行为都要有人写模型。固件重托管综述（Wright 等，ACM Computing Surveys 2020）总结了 28 个难题；"外设行为"和"保真度量化"至今仍是公开难题。实例：Renode 的 STM32F103 没有 SPI、ADC、看门狗。
2. **时序不准**：QEMU 开发者原话的意思是"模拟行为而不是逐周期仿真，不追求准确的时钟周期；可能有真实硬件上的 bug 在 QEMU 里不出现，反过来也一样"。依赖时序的串口、I2C 也因此只能有限支持，而且在不同电脑上跑，时序结果还不一样（OpenTitan 文档）。中断时序、竞态正好依赖这些。
3. **没有电气和模拟量**：电压、上拉、噪声、ADC 读数、PWM 反馈都没有（BMC 测试文章举例：没有真实 PWM 和转速反馈，风扇 PID 无从谈起）。
4. **外部器件也要建模**：传感器、屏幕各有各的协议细节，每个都要写模型。
5. **仿真器照着手册写，复现不了芯片本身的 bug**：勘误手册里的硬件缺陷，仿真器一般不会有。
6. **不知道仿得有多准**：没法给仿真打"可信度"，所以仿真通过不能说明板子上一定对。

结论：仿真适合快速、粗略地查功能，不能代替真实硬件；业界普遍是"电脑上大量单元测试 + 板子上验证硬件相关部分"，仿真只是可选的补充。
