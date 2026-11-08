# FirmForge 开发路线

初赛作品提交截止 **11/20**，决赛提交截止 12/6，线上答辩 12/14–20（以说明会 PPT 为准）。

## 每个阶段怎么推进

1. 在项目目录打开 Claude Code，新开一个会话，粘贴该阶段的"给 Claude Code 的提示词"。
2. Claude Code 先给出实现计划（文件清单、顺序、需要你拍板的问题）。你看完回复"确认"或提修改意见。
3. Claude Code 实现。遇到设计取舍会停下来问你。
4. 你按"验收"一节逐条运行命令、确认现象。全部通过才算完成，然后让 Claude Code 提交 git。
5. 阶段结束时，Claude Code 会用几句话讲解本阶段的设计。你自己复述一遍，算是提前演练答辩。

---

## 阶段 0：环境准备（你来做，周五晚上）

- [ ] 安装 Docker Engine，并把当前用户加入 docker 组（安装时需要管理员权限，用系统弹窗授权）；装完重新登录，确认 `docker run hello-world` 能跑。
- [ ] 确认终端里能打开 Claude Code。
- [ ] 项目目录 `~/Documents/Projects/firmforge`，里面已经放好 `CLAUDE.md` 和 `docs/`。在这个目录里执行 `git init`。
- [ ] 在 GitHub 建一个仓库，先设为私有，提交作品前再公开。初赛要求提供可访问的源码仓库地址。
- [ ] 准备 DeepSeek API key，阶段 2 时写进 `.env`。key 不要发给任何人，也不要贴进聊天。
- [x] ~~把项目说明书发给 3 位队友，按附录 A 分配要收集的评测任务。~~ 没有队友，改为 Claude Code 起草、你审核（DECISIONS 第 25 条）。

---

## 阶段 1：判定器 + 首批 3 个任务 + Docker（10/24 周六）

**目标**：有一把可靠的"尺子"。给它任意一份 C 代码，它都能逐层判定，并输出结构化报告。

**交付物**
- `pyproject.toml`、`.gitignore`、`.env.example`、`README.md`（含"当前进度"）
- `third_party/unity/`（Unity v2.6.1 源码 + LICENSE）、`third_party/unity_support/unity_weak_hooks.c`
- `src/firmforge/config.py`、`tasks.py`、`verifier/`（L1–L5、隐藏测试、`check_task`）、`cli.py`（`doctor`、`tasks`、`check-task`、`verify`）
- `docker/Dockerfile`
- `tasks/ringbuf`、`tasks/crc16_modbus`、`tasks/debounce`，需求要点见附录 B
- `tests/`，包含覆盖各类错误的样例工作目录 `tests/fixtures/`

**验收**（你来运行）
1. `docker build -t firmforge -f docker/Dockerfile .` 成功；`docker run --rm firmforge doctor` 显示所有工具可用。
2. `docker run --rm -v "$PWD":/work -w /work firmforge check-task --all`：3 个参考实现结论都是 PASS，每个变异体都在预期的层失败。表格里列出参考实现实测的 flash、RAM、栈。
3. 用 `verify` 跑 `tests/fixtures/` 里的样例，各自在正确的层失败：
   - 语法错误 → L1
   - 用了 malloc → L4
   - 测试失败 → L5
   - 死循环 → L5 超时
   - 超出预算 → L3
4. 把语法错误样例里的错误挪到另一行，再跑一次：签名不变。
5. `python -m unittest discover -s tests -v` 全部通过。本机缺少工具的测试应显示 skipped，而不是失败。
6. 你读一遍 Claude Code 列出的"每个任务的隐藏测试用例清单"，确认测的行为和需求一致。**这一步只有你能做**，它保证裁判本身没有错。

**给 Claude Code 的提示词**
```
请阅读 CLAUDE.md、docs/ARCHITECTURE.md（§1、§2.1–2.4、§3、§11、§14）和 docs/ROADMAP.md 的阶段 1 与附录 B。
实现阶段 1 的全部交付物。开始写代码前，先给我：
1）文件清单和实现顺序；2）你对附录 B 中三个任务的接口头文件草案；3）你认为设计文档里不清楚、需要我决定的地方。
等我确认后再实现。实现完成后，按"验收"逐条运行能在你环境里运行的部分，并为每个任务列出隐藏测试的用例清单（测试名 + 一句话说明测什么）给我审核。
```

---

## 阶段 2：接入 DeepSeek + 单 Agent 循环 + 评测脚本（10/25 周日）

**目标**：跑出第一组分数，也就是基线 B1 和 B2。这一阶段结束就达到里程碑"一条命令跑出评测分数"。

**交付物**
- `firmforge.toml`，模型型号和价格由你填写
- `llm/`（`OpenAICompatClient`、`FakeLLM`、调用日志与脱敏）
- `agents/protocol.py`（文件块解析 + 路径白名单）、`prompts/coder.md`
- `runs.py`（运行目录）、`orchestrator/events.py`（事件总线 + jsonl）、`store.py`（runs、node_attempts 两张表）
- 单 Agent 执行器（B1、B2）
- `evaluation/`：`b1`、`b2` 两个配置，输出 `results.csv` 和 `summary.md`
- 新增命令：`models`、`run`、`eval`、`replay`（终端版）

**验收**
1. `firmforge models` 能列出 DeepSeek 的可用型号。然后把型号和价格填进 `firmforge.toml`。
2. `firmforge run --task tasks/ringbuf --config b2` 生成一个运行目录：`events.jsonl` 有内容，`llm_calls/*.json` 里 Authorization 显示为 `***`，`final/` 里有代码和报告。
3. `firmforge eval --configs b1,b2 --tasks all --runs 1 --out results/first` 输出 CSV 和汇总表。**这就是第一组分数。**
4. `firmforge replay <运行目录>` 能在终端按时间顺序回放这次运行。
5. 单元测试（全部用 FakeLLM，不联网）至少覆盖：
   - 文件块解析；
   - 写到白名单外的路径被拒绝；
   - 遇到 429 会重试；
   - 日志脱敏；
   - **发给 LLM 的所有消息里都不含隐藏测试的内容**。
6. 你看一眼 `summary.md`，记下模型在哪类任务、哪一层失败最多。下周设计多 Agent 分工就用这些观察。

**给 Claude Code 的提示词**
```
请阅读 CLAUDE.md、docs/ARCHITECTURE.md（§2.5–2.10、§4、§5.1、§8 末尾的基线说明、§10、§11）和 docs/ROADMAP.md 阶段 2。
实现阶段 2。先给我实现计划和需要我决定的问题，确认后再写。
注意：单元测试一律用 FakeLLM；真实 API 只在我手动运行验收命令时调用。
```

---

## 阶段 3：多 Agent + 编排引擎 + 调度器（10/31–11/1）

**目标**：完整的多 Agent 流水线可以跑起来，并能和单 Agent 基线对比。这一阶段结束就达到里程碑"多 Agent 版本能与单 Agent 基线对比"。

**交付物**
- `agents/*.toml`：planner、coder-fast、coder-strong、tester、reviewer、diagnoser；`prompts/` 下每个角色一个模板
- `agents/registry.py` 和 SQLite 的 `agent_stats` 表
- `orchestrator/dag.py`（规划结果校验）、`workflow.py`（读取 TOML 模板）、`engine.py`（七种节点、模块隔离目录、集成判定、人工节点）
- `orchestrator/scheduler.py`：serial、round_robin、random、smart 四种策略，得分明细随事件发出
- `orchestrator/loop.py`：修正循环、诊断路由、回滚（升级阶梯在阶段 4 做）
- 评测配置 `b3`、`b4`、`full`（此阶段的 full 还不含升级和经验库）；同等花费对照 `b5`，汇总表增加"每通过一题的平均 token 和成本"（DECISIONS 第 30 条）
- 新任务：`modbus_slave`（多模块，用来演示 DAG），再加附录 A 中的 6 个任务，评测集达到 10 个

**验收**
1. `firmforge run --task tasks/modbus_slave --config full`：`dag.json` 至少有 3 个模块；从 `events.jsonl` 的时间戳能看出没有依赖关系的模块是并行执行的。
2. 单元测试证明：tester 收到的消息里不包含任何 `src/*.c` 的内容。
3. `firmforge eval --configs b1,b2,b3,b4,full --tasks all --runs 1` 输出五组对比。
4. 你挑一次运行，对照 `node_assigned` 事件里的得分明细，能说清楚某个节点为什么分给了那个 Agent。

**给 Claude Code 的提示词**
```
请阅读 CLAUDE.md、docs/ARCHITECTURE.md（§2.5、§2.7、§5、§6、§7、§8）和 docs/ROADMAP.md 阶段 3。
实现阶段 3。先给我实现计划、规划 Agent 和诊断 Agent 的提示词草案，以及需要我决定的问题，确认后再写。
新增任务由你起草全套内容（spec.md、头文件、隐藏测试、参考实现、变异体），先把每道题的规则写成"什么情况 → 应该怎样"的白话清单给我审核，确认后再写代码。
```

---

## 阶段 4：升级、经验库、知识库、桌面软件（11/7–8）

**目标**：做出亮点，并完成界面。这一阶段结束时**冻结功能**，之后只修 bug、跑实验。

**交付物**
- 升级阶梯：知识库 → 换 Agent → 重新规划 → 人工
- `memory/`：BM25 检索、经验库（写入、合并、使用）、知识库
- `kb/` 下首批 5–8 篇摘要：编码规则、Modbus RTU 要点、Cortex-M3 注意事项等
- 消融配置 `full-no-exp`、`full-no-esc`、`full-no-review`
- `gui/`：主窗口、DAG 图、Agent 状态与得分明细、报告视图、人工确认对话框、docker 与 local 两种后端、回放模式
- 可选：`mcp_server.py`
- 评测集达到 20 个任务

**验收**
1. 用一个 FakeLLM 场景，让同一个错误连续出现，事件流里能看到逐级 `escalated`。
2. 连续跑两个同类任务：第一个产生 `experience_saved`，第二个出现 `experience_used`。
3. `pip install -e .[gui]` 后启动桌面软件：
   - docker 后端下，输入任务能实时看到 DAG 节点变色；
   - 打开一个历史运行目录，可以按 4 倍速回放；
   - 勾选人工确认后，规划完成时会弹窗等待确认。
4. `eval` 能跑全部配置，包括 3 个消融配置。

**给 Claude Code 的提示词**
```
请阅读 CLAUDE.md、docs/ARCHITECTURE.md（§8、§9、§12、§13）和 docs/ROADMAP.md 阶段 4。
实现阶段 4。桌面软件先给我一张界面布局草图（文字或 ASCII 均可）和交互说明，确认后再写。
本阶段结束后功能冻结，请在 README 中写清楚全部功能和已知局限。
```

---

## 阶段 5：实验、Windows 实测、交付（11/14–15）

> 2026-11-08 起按 DECISIONS 第 97 条执行：11/11 晚功能冻结 → 11/12 最后一轮上板正式评测（6 题 × 4 种配置 × 2 次，上限 15 元）→ 11/13–11/19 交付（桌面软件接入嵌入式新流程、Windows 实测与打包、README、方案书、PPT、视频）→ 11/20 缓冲。下面"全量评测"一条以此为准。

**目标**：拿到完整数据，材料全部齐备，11/20 前提交。

**任务**
- 全量评测：全部配置 × 20 个任务 × 3 次。先用 `--estimate` 确认 token 额度够用。
- Windows 实测：在 Docker Desktop 上跑命令行验收；桌面软件用 docker 后端完整跑一次。
- GitHub Actions：用 Windows runner 自动打包 FirmForge.exe。
- `README.md` 定稿：一键复现步骤、功能清单、已知局限、开源组件说明（Unity，MIT）、人工智能使用说明。
- 录演示视频：用桌面软件的回放模式，录一次真实运行。
- 方案书与 PPT 素材：`summary.md` 的表格、架构图、每个阶段的设计讲解（由 Claude Code 起草初稿，你修改定稿）。

**提交清单**（对照赛题一要求）
- [ ] 方案书 PDF，不少于 5000 字，含赛题要求的 8 项内容
- [ ] PPT 正文 10–15 页
- [ ] 演示视频 3–5 分钟：平台界面、编排过程、调度执行、结果验证与反馈循环
- [ ] 源码仓库已公开，README 写明一键复现方法

---

## 阶段 6：决赛准备（11/28–12/6，若晋级）

- 评测集扩到 30 个以上。
- 并发压力测试：新增 `firmforge stress --concurrency 1,2,4,8`，测吞吐、延迟、成功率。
- 调度参数 α、β、γ 的扫描实验。
- 可选：Renode 外设仿真判定器。
- 真实用户试用：记录使用过程和反馈，作为落地应用的证明。
- 答辩演练：你讲架构、调度、循环，Claude Code 扮评委追问。

---

## 附录 A：评测任务清单（20 个候选）

**2026-10-25 调整（DECISIONS 第 55 条）**：阶段 3 评测出现天花板（b2、full 都 10 题全过），下表第 4–9、12、15–18 行这些难度 1–2 的候选不再做，改为 `tasks_inbox/stage4_task_list.md` 里的 10 道难度 3 新题：xmodem_rx、mavlink_parser、onewire_ds18b20、isotp、sd_spi_init、at_parser、pid_ctrl、stepper_ramp、mem_pool、flash_kv。下表保留作为历史记录。

**分工（2026-10-24 调整，DECISIONS 第 25 条）**：项目没有队友。每道题由 Claude Code 起草全套内容，草稿包括：
- 功能描述、每个函数的输入输出；
- 边界情况和 NULL 怎么处理；
- 来源（标准、数据手册或常见开源实现，写明出处）。

Claude Code 先把规则整理成"什么情况 → 应该怎样"的白话清单交你审核（同阶段 1 的做法），你确认后再生成任务目录，
并跑 `check-task` 证明参考实现通过、变异体被抓到。题目数量先做到 10 道（阶段 3），是否扩到 20 道视审核负担再定。

| # | id | 类别 | 难度 | 要点 | 起草 |
|---|---|---|---|---|---|
| 1 | ringbuf | 数据结构 | 1 | 字节环形缓冲区 | 阶段 1 已含 |
| 2 | crc16_modbus | 算法 | 1 | CRC-16/MODBUS | 阶段 1 已含 |
| 3 | debounce | 状态机 | 1 | 按键消抖 | 阶段 1 已含 |
| 4 | crc8_maxim | 算法 | 1 | CRC-8/MAXIM（DS18B20 用） | Claude Code 起草，你审核 |
| 5 | moving_average | 滤波 | 1 | 窗口 N 的整数滑动平均，不能溢出 | Claude Code 起草，你审核 |
| 6 | median_filter | 滤波 | 2 | 窗口 5 的中值滤波 | Claude Code 起草，你审核 |
| 7 | pid_ctrl | 控制 | 2 | PID，带积分限幅和输出限幅 | Claude Code 起草，你审核 |
| 8 | q15_math | 算法 | 2 | Q15 定点乘法和加法，带饱和 | Claude Code 起草，你审核 |
| 9 | bit_utils | 数据结构 | 1 | 位段提取和设置、字节序转换 | Claude Code 起草，你审核 |
| 10 | button_events | 状态机 | 3 | 单击、双击、长按识别 | Claude Code 起草，你审核 |
| 11 | soft_timer | 状态机 | 2 | 固定数量的软件定时器，单次和周期 | Claude Code 起草，你审核 |
| 12 | led_pattern | 状态机 | 2 | LED 闪烁模式播放器 | Claude Code 起草，你审核 |
| 13 | uart_frame | 协议 | 2 | 帧头 + 长度 + 载荷 + CRC 的接收状态机，处理粘包和半包 | Claude Code 起草，你审核 |
| 14 | modbus_slave | 协议 | 3 | 功能码 03/06，多模块（用于演示 DAG） | Claude Code 起草，你审核 |
| 15 | nmea_gga | 协议 | 2 | 解析 GGA 语句，带校验和 | Claude Code 起草，你审核 |
| 16 | at_parser | 协议 | 2 | AT 指令响应解析（OK、ERROR、+CSQ: 等） | Claude Code 起草，你审核 |
| 17 | cobs | 协议 | 2 | COBS 编解码 | Claude Code 起草，你审核 |
| 18 | slip | 协议 | 1 | SLIP 编解码 | Claude Code 起草，你审核 |
| 19 | mpu6050_drv | 驱动 | 3 | 对着 mock I2C HAL 读取并换算加速度和角速度 | Claude Code 起草，你审核 |
| 20 | w25q_drv | 驱动 | 3 | 对着 mock SPI HAL 读 ID、读数据、页编程（跨页时拆分） | Claude Code 起草，你审核 |

驱动类任务的 `include/` 里要同时提供 HAL 接口头文件（如 `hal_i2c.h`）。隐藏测试用自己的 mock 实现。测试 Agent 也要自己写 mock，放在 `tests/` 下作为支撑文件。

## 附录 B：首批 3 个任务的需求要点

**ringbuf：字节环形缓冲区**
- `ringbuf_init(rb, storage, capacity)`：rb 或 storage 为 NULL，或 capacity 为 0，返回 false。容量可以是任意正整数，不要求是 2 的幂。
- `ringbuf_put`：满了返回 false，不覆盖旧数据。`ringbuf_get`：空的，或 out 为 NULL，返回 false。
- `ringbuf_count` / `ringbuf_free` / `ringbuf_is_empty` / `ringbuf_is_full`：rb 为 NULL 时依次返回 0、0、true、false。
- `ringbuf_write(rb, data, len)` / `ringbuf_read(rb, out, len)`：按剩余空间或已有数据量处理，返回实际写入或读出的字节数；遇到 NULL 返回 0。
- `ringbuf_clear`：清空。
- 变异体：写指针不取模（越界写 → 隐藏测试由 ASan 抓到）；满判断差一（隐藏测试）；init 里用了 malloc（静态检查）。

**crc16_modbus：CRC-16/MODBUS**
- `crc16_modbus(data, len)`：初值 0xFFFF，反射多项式 0xA001，无最终异或。len 为 0 或 data 为 NULL 时返回 0xFFFF。标准校验值："123456789" → 0x4B37。
- `crc16_modbus_append(frame, len)`：在 frame[len] 写低字节，在 frame[len+1] 写高字节。
- `crc16_modbus_check(frame, total_len)`：frame 为 NULL 或 total_len < 3 时返回 false；否则 CRC 正确才返回 true。
- 允许查表实现，但表必须是 const（放在 Flash）。RAM 预算为 0。
- 变异体：初值写成 0x0000（隐藏测试）；append 字节序反了（隐藏测试）；查表数组没加 const，跑进了 RAM（资源预算层）——**这个变异体专门展示"嵌入式约束做成判定器"**。

**debounce：按键消抖**
- `debounce_init(d, threshold_ticks, initial_pressed)`；threshold 为 0 时按 1 处理。
- `debounce_update(d, raw_pressed)`：每个 tick 调用一次。原始输入与稳定状态不同的 tick 连续累计达到 threshold 时，稳定状态翻转、计数清零，并返回 `BTN_EVT_PRESS` 或 `BTN_EVT_RELEASE`；中途原始输入与稳定状态相同，计数清零；其余情况返回 `BTN_EVT_NONE`。d 为 NULL 时返回 NONE。
- `debounce_is_pressed(d)`：d 为 NULL 时返回 false。
- 变异体：第 threshold+1 个 tick 才翻转（差一，隐藏测试）；抖动时计数不清零（隐藏测试）。
