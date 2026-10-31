# 嵌入式编程相较一般编程的痛点：资料调研（2026-10-31）

目的：重新定位项目前，先弄清"嵌入式写代码到底难在哪"，以及"现有大模型 / Agent 在嵌入式上具体错在哪"。
每条结论后注明出处；数字尽量取论文正文，只读到摘要或二手摘要的会注明。

## 一、核心证据（按可信度排）

### 1. IoT-SkillsBench（杜克大学，arXiv 2603.19583，2026 年 3 月）——真实开发板测试
- 3 种平台（Arduino/AVR、ESP32-S3 + ESP-IDF、nRF52840 + Zephyr），23 种外设，42 道题，分三级（GPIO/ADC/UART → I2C/SPI/1-Wire → 中断、多传感器、调度）。
- **全部烧进真实开发板测试**，人工核对约 100 小时。结果分三类：编译/烧录失败、能跑但行为不对（含卡死、看门狗复位）、正确。
- **失败的主体是"能编译、能烧录、但行为不对"**：例如不加技能时 Zephyr 3 个编译失败对 15 个行为失败，ESP-IDF 5 对 11。
- 原文观点："编译成功并烧录到设备，并不保证运行行为正确"；"很小的配置不匹配就可能导致无声的失败"。
- 给 Agent 加**专家写的领域知识（skills）**后：pass@5 从 101/126 升到 124/126；而**让模型自己生成的知识**反而略降（95/126）。结论原文："起作用的是有依据的领域知识，而不只是更大的模型"。
- 剩下的 2 个失败是硬件问题（5V 模块接 3.3V 板、旋转编码器方向不统一）。

### 2. EmbedAgent / EmbedBench（ICSE 2026，arXiv 2506.11003）——仿真器测试
- 126 题 × 3 种设定（给原理图写代码、只给需求自己设计电路再写代码、把 Arduino 项目迁移到 ESP32/Pico），在 Wokwi 仿真器里实时检查元件状态。
- 最好成绩：给原理图时 DeepSeek-R1 55.6%；不给原理图 50.0%；**迁移到 ESP32 的 ESP-IDF 最好只有 29.4%**（Claude 3.7），DeepSeek-V3 只有 2.4%；迁移到 Pico 的 MicroPython 则高得多（73.8%）。
- 失败原因（以下来自论文后半部分的摘要，未逐字核对）：七段数码管编码错占约 41–44%；按键去抖逻辑错占约 40–48%（例如用固定 150 ms 延时导致误触发）；ESP32 迁移有约 42% 是语法 / 头文件错误，**即使提示里写了 ESP-IDF 版本，也会用该版本不兼容的函数**。
- 两个改进手段（检索参考代码、把编译错误回喂一轮）：R1 从 55.6% 提到 65.1%，ESP32 迁移从 21.4% 提到 27.8%；编译错误回喂让语法错误从 34.1% 降到 3.2%。

### 3. Englhardt 等（华盛顿大学，arXiv 2307.03817，2023，后发表于 IMWUT）——真实硬件 + 用户研究
- 自建硬件在环框架：LLM 写代码 → 编译 → 烧录 → 用串口采集输出，和人写的参考程序的输出做比对。
- IMU 传感器（I2C）任务：GPT-3.5 **100% 至少编造了一个寄存器地址或配置值**，38% 把 I2C 地址弄错；GPT-4 只有 16% 端到端正确，主要卡在"寄存器配置和数值换算"，还会弄错位域宽度。摘要写的是"GPT-4 生成可用 I2C 接口的比例为 66%"（论文内部数字不完全一致）。
- 低功耗优化：模型给的建议组合能把电流从 9.1 mA 降到 12.2 µA，接近专家的 8.6 µA，但其中一条建议反而增加了功耗。
- 他们提出的人机协作流程：提示里写清器件型号和库 → 编译错误回喂 → 在真实硬件上测试 → 具体描述哪里不对 → 拆成小模块。15 人用户研究中，LoRa 传感器任务成功率从 25% 升到 100%。

### 4. 其他
- 微控制器 IoT 编程基准（MDPI Future Internet 2026，只读到搜索摘要）：简单应用 19–23 个模型能编译通过，复杂场景只有 3–5 个模型能写出能用的代码；最常见的错误是**编造不存在的库、用错 API**。
- LLM 生成固件的安全问题（arXiv 2509.09970，只读到摘要）：反复出现缓冲区溢出、竞态条件、拒绝服务类缺陷；用"模糊测试 + 静态分析 + 运行时监测 → 按缺陷类别让 LLM 打补丁"的循环，修复率 92.4%。
- 行业调查（数据较老、多为工具厂商发起）：2007 年 354 人调查中 56% 认为调试是最耗时的环节，60% 必须在真实硬件上调试；多核调试最大问题是"不可复现"（33%）。2017 年调查里"调试"和"赶进度"并列最大挑战（各 23%）。
- 嵌入式测试（Aalto 大学学位论文 + 厂商博客）：因为代码依赖具体硬件，单元测试和测试驱动开发经常被省略；常用的办法是把依赖硬件的部分替换成替身，在电脑上跑测试；硬件数量有限会妨碍持续集成；同一份代码换一版硬件可能就出错。
- 中断与栈：中断时序导致的竞态和栈溢出是典型的难复现缺陷（Ember-IO 固件模糊测试、SDRacer 中断竞态检测与修复论文）。原因包括 RAM 小、没有虚拟内存兜底、每个任务各有自己的栈要单独定大小、中断还会占用这些栈。

## 二、归纳：嵌入式相较一般编程的 7 个痛点

| # | 痛点 | 一般编程 | 嵌入式 | 主要依据 |
|---|---|---|---|---|
| 1 | **"编译过、测试过"不等于"板子上对"** | 跑通测试基本就对了 | 时序、外设初始化、引脚、电压配置错了，软件检查都查不出，要上板才知道 | IoT-SkillsBench：失败主体是"能烧录但行为不对" |
| 2 | **硬件事实必须精确到寄存器和位** | API 文档在训练数据里很多 | 寄存器地址、位域、I2C 地址、换算系数全在几百页的数据手册里，模型最爱编造 | Englhardt：GPT-3.5 100% 编造寄存器 |
| 3 | **生态碎片化、版本敏感** | 主流库少，接口稳定 | 芯片厂商 SDK、RTOS、HAL 版本众多，同一功能写法各不相同；冷门平台的训练数据少 | EmbedAgent：ESP-IDF 最好 29.4%，写了版本号还用错函数；IoT-SkillsBench：Zephyr 更差 |
| 4 | **资源和实时性是硬约束** | 内存几乎无限，慢点也能用 | flash、RAM、栈有硬上限，没有虚拟内存兜底；有截止时间、低功耗要求 | 栈溢出相关文献；Englhardt 低功耗实验 |
| 5 | **并发来自中断，缺陷难复现** | 并发多在线程层面，有成熟工具 | 中断随时打断，共享变量竞态、时序相关的崩溃难以复现 | SDRacer、Ember-IO；2007 调查"不可复现"33% |
| 6 | **测试和调试离不开硬件** | 测试在电脑上就能跑 | 要么上板（板子少、慢、难自动化），要么做硬件替身；调试最耗时 | Aalto 论文、Auriga；调试占比调查 |
| 7 | **领域"套路"要求高** | 通用算法为主 | 去抖、数码管编码、通信协议帧、状态机这些看似简单的嵌入式惯用写法，模型反而常错 | EmbedAgent：去抖错和数码管错各占四成 |

另外两条共识：
- **补领域知识比换大模型更有效**：专家写的知识让 IoT-SkillsBench 接近满分，模型自己总结的知识反而无效。
- **最有效的反馈是"真实执行结果"**：编译错误回喂能消掉大部分语法错误，但行为错误只能靠硬件或高保真仿真的执行结果来暴露。

## 三、对照现在的 FirmForge

| 痛点 | 现在有没有覆盖 |
|---|---|
| 1 板上行为 | ❌ 只有软件检查，不上板、不仿真 |
| 2 寄存器 / 数据手册 | ❌ 题目都是与硬件无关的纯逻辑模块（或用替身），没有数据手册依据 |
| 3 SDK / 版本碎片 | ❌ 只针对一种目标、裸 C，不涉及厂商 SDK |
| 4 资源约束 | ✅ flash / RAM / 调用链最坏栈预算 |
| 5 中断并发 | ❌ 没有中断、竞态相关的检查 |
| 6 测试离不开硬件 | ⚠️ 用电脑上的单元测试加替身，绕开了硬件，但也就测不到硬件相关的错误 |
| 7 领域套路 | ⚠️ 知识库 7 篇、经验库；题目里有去抖、帧解析，但没有专门针对这些套路的知识 |

结论：现在的 FirmForge 本质上是"一个面向 C 代码的通用多 Agent 编程平台，外加资源预算检查"。论文反复指出的嵌入式核心痛点（1、2、3、5）基本没有覆盖，这和负责人"跟市面上的编程 Agent 没太大区别"的判断一致。

## 来源

- [Skilled AI Agents for Embedded and IoT Systems Development（arXiv 2603.19583）](https://arxiv.org/html/2603.19583)
- [EmbedAgent: Benchmarking LLMs in Embedded System Development（arXiv 2506.11003）](https://arxiv.org/abs/2506.11003)；[ICSE 2026 页面](https://conf.researchr.org/details/icse-2026/icse-2026-research-track/260/EmbedAgent-Benchmarking-Large-Language-Models-in-Embedded-System-Development)
- [Exploring and Characterizing LLMs for Embedded System Development and Debugging（arXiv 2307.03817）](https://arxiv.org/abs/2307.03817)
- [Benchmarking LLMs for Embedded Systems Programming in Microcontroller-Driven IoT Applications（MDPI）](https://www.mdpi.com/1999-5903/18/2/94)
- [Securing LLM-Generated Embedded Firmware（arXiv 2509.09970）](https://arxiv.org/html/2509.09970v1)
- [Debugging Survey Says（Electronic Design）](https://www.electronicdesign.com/technologies/embedded/article/21768440/debugging-survey-says-what-works-what-doesnt)；[Embedded development survey（eeNews Europe）](https://www.eenewseurope.com/en/embedded-development-survey-reveals-trends-concerns-for-engineers/)
- [Evaluation of test-driven approaches for embedded software development（Aalto）](https://aaltodoc.aalto.fi/items/26c04f22-146a-418f-89fd-5318895aad98)；[Four Major Pitfalls in Testing Embedded Software（Auriga）](https://auriga.com/blog/2017/embedded-software-testing-pitfalls/)
- [Ember-IO（arXiv 2301.06689）](https://arxiv.org/pdf/2301.06689)；[SDRacer（arXiv 2305.17869）](https://ar5iv.labs.arxiv.org/html/2305.17869)；[Five top causes of nasty embedded software bugs（Embedded.com）](https://www.embedded.com/?p=4008917)
