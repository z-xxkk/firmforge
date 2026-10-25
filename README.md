# FirmForge

面向嵌入式固件开发的多 Agent 编排与验证平台（第五届"未来杯"技术大赛赛题一参赛作品）。
目标芯片 STM32F103（Cortex-M3）。设计文档见 `docs/ARCHITECTURE.md`，开发顺序见 `docs/ROADMAP.md`。

## 当前进度

**阶段 1（判定器 + 首批 3 个任务 + Docker）：已完成，在 Docker 镜像中验收通过（2026-10-23）。**

- 判定器 `firmforge.verifier`：L1 主机编译、L2 交叉编译、L3 资源预算（flash / RAM / 单函数栈帧 / 动态栈）、
  L4 静态检查（禁用符号 + cppcheck）、L5 单元测试（Unity + ASan/UBSan + 超时 + gcov 覆盖率）、隐藏测试层；
  JSON 报告、错误签名、给 Agent 看的报告（剔除隐藏测试层）。
- 任务自检 `check_task`：参考实现必须通过，每个变异体必须在预期层第一个失败。
- 评测任务：`ringbuf`、`crc16_modbus`、`debounce`，共 7 个变异体；预算按参考实现实测用量校准（见 `docs/DECISIONS.md` 第 19、20 条）。
- 命令行：`doctor`、`tasks`、`check-task`、`verify`；`docker/Dockerfile`。

Docker 中的验收结果：`doctor` 所有工具可用；`check-task --all` 3 个参考实现 PASS，7 个变异体全部在预期层失败；
`tests/fixtures/` 的样例各在预期层失败；59 个单元测试全部通过、无跳过。

| 任务 | 实测 flash / RAM / 调用链最坏栈（字节） | 预算 |
|---|---|---|
| ringbuf | 272 / 0 / 24 | 544 / 0 / 48 |
| crc16_modbus | 128 / 0 / 28（查表写法 flash 632） | 1264 / 0 / 56 |
| debounce | 72 / 0 / 0 | 144 / 0 / 32 |

**阶段 2（接入 DeepSeek + 单 Agent 基线 + 评测脚本）：已完成，2026-10-24 用真实 API 验收。**

验收结果：`models` 列出 deepseek-flash、deepseek-v4-pro；`run --task tasks/ringbuf --config b2` 第 1 次即通过（隐藏测试 19/19），
调用日志中 Authorization 为 `***`、全部记录里搜不到 key；`replay` 可回放。第一组分数 `results/first`（未提交到仓库）：

| 任务 | B1 | B2 |
|---|---|---|
| crc16_modbus | 通过 | 通过 |
| debounce | 未通过（host_compile：用了 NULL 却没包含 `<stddef.h>`） | 第 2 次通过（报错回喂后补上头文件） |
| ringbuf | 通过 | 通过（运行中断后单独补跑，结果已并入） |

合计：B1 3 题过 2 题，B2 3 题全过；6 次运行共约 0.92 元，每次 0.07–0.27 元、40–155 秒（思考模式，周末空闲价）。每个组合只跑了 1 次，样本很小，只能作为方向参考。

- LLM 客户端 `firmforge.llm`：标准库 urllib 实现的 OpenAI 兼容接口，429 / 5xx / 网络错误指数退避重试；
  FakeLLM（单元测试用，不联网）；调用日志 `llm_calls.jsonl` + `llm_calls/<n>.json`，Authorization 显示为 `***`。
- 文件块协议与路径白名单：Agent 只能写 `src/`、`tests/` 下的 `.c` / `.h`，其他路径拒绝并记录。
- 运行目录 `.ff_runs/<run_id>/`、事件流 `events.jsonl`、SQLite（runs、node_attempts 两张表）。
- 单 Agent 基线：B1 一次生成不修正；B2 判定报错直接回喂、最多 3 轮修正；同一个 Agent 同时写实现和测试。
- 评测：`eval` 输出 `results.csv`、`summary.md`；最终是否通过只看 L1–L4 + 隐藏测试，在临时目录判定。
- 命令：`models`、`run`、`eval`（含 `--estimate`）、`replay`。
- 模型：fast = deepseek-flash，strong = deepseek-v4-pro，均开启思考模式；成本按北京时间分高峰 / 空闲计价（DECISIONS 第 23、24 条）。
- 单元测试 114 个（本机 1 个因缺交叉工具链跳过；Docker 中全部通过），其中包括"发给 LLM 的消息和运行目录里都不含隐藏测试内容"。

**阶段 3 准备：已完成（2026-10-25）。**

- 约束依据审计（`docs/CONSTRAINTS_AUDIT.md`）与工作流设计依据调研（`docs/WORKFLOW_RESEARCH.md`），据此做出 DECISIONS 第 26–34 条。
- 已实现：默认禁用符号按 MISRA C:2012 与最小权限原则扩充；资源预算层改为调用链最坏栈，并用 -O0 调用图检查递归。
- 新增 3 道题（median_filter、soft_timer、uart_frame），Docker 中自检通过：参考实现 PASS，10 个变异体全部在隐藏测试层被抓到；隐藏测试清单已审核（`tasks_inbox/batch1_hidden_tests.md`）。
- 6 道题的资源预算：

| 任务 | 实测 flash / RAM / 调用链最坏栈 | 预算 |
|---|---|---|
| median_filter | 224 / 0 / 48 | 448 / 0 / 96 |
| soft_timer | 182 / 0 / 8 | 364 / 0 / 32 |
| uart_frame | 304 / 0 / 48 | 608 / 0 / 96 |

- 第二批 2 道题（button_events、mpu6050_drv），规则先查资料再定（DECISIONS 第 35 条）；Docker 中自检通过，7 个变异体全部被隐藏测试抓到；隐藏测试清单已审核（`tasks_inbox/batch2_hidden_tests.md`）。

| 任务 | 实测 flash / RAM / 调用链最坏栈 | 预算 |
|---|---|---|
| button_events | 180 / 0 / 0 | 360 / 0 / 32 |
| mpu6050_drv | 368 / 0 / 48 | 736 / 0 / 96 |

- 第三批 2 道题（w25q_drv、modbus_slave），规则依据 Winbond 数据手册与 Modbus 官方规范（DECISIONS 第 36 条）；Docker 中自检通过，8 个变异体全部被隐藏测试抓到；隐藏测试清单已审核（`tasks_inbox/batch3_hidden_tests.md`）。

| 任务 | 实测 flash / RAM / 调用链最坏栈 | 预算 |
|---|---|---|
| w25q_drv | 482 / 0 / 320 | 964 / 0 / 640 |
| modbus_slave | 466 / 0 / 56 | 932 / 0 / 112 |

- 评测任务共 10 道（数据结构 2、协议 3、状态机 3、驱动 2，难度 1–3），`check-task --all` 全部通过；单元测试 122 个（Docker 中全部通过）。

**阶段 3（多 Agent + 编排引擎 + 调度器）：已完成，2026-10-25 用真实 API 验收。**

验收结果：
- `run --task tasks/modbus_slave --config full`：规划拆成 crc、pdu、slave 3 个模块，crc 与 pdu 同时进行，slave 等两者完成后开始；交付物通过全部 18 个隐藏测试，花费 1.44 元、约 14 分钟。
- 每次分配的得分明细在实时输出和 `node_assigned` 事件里（成功率、成本、负载、探索四项）。
- 正式评测 `results/stage3`（未提交到仓库）：按 DECISIONS 第 49 条只跑 b2、full、b5，10 道题各 1 次，共约 15.1 元、约 3 小时（周日空闲价）：

| 配置 | 最终通过 | 首次通过 | 平均 token | 每通过一题成本 |
|---|---|---|---|---|
| b2 单 Agent + 修正 | 10 / 10 | 8 / 10 | 28576 | 0.35 元 |
| full 多 Agent | 10 / 10 | 9 / 10 | 61541 | 0.48 元 |
| b5 同等花费从头重写 | 9 / 10（mpu6050_drv 没过） | 6 / 10 | 52550 | 0.75 元 |

如实说明：
- 每个组合只跑 1 次，b2 与 full 都是 10 题全过，差异在随机波动范围内，**这组数据不能说明多 Agent 比单 Agent 更强**；题目对当前模型偏简单，出现了"天花板"。
- 能看出的是：同等花费下"从头重写"（b5）不如"带着报错修改"（b2、full）；full 的花费主要在测试 Agent（strong 档），编码 Agent 全部被 smart 分给了便宜的 coder-fast（15 个模块都一次或修改后通过，coder-strong 一次也没被选中）。
- 修正循环在真实模型上走通了：debounce 诊断为 code（缺 `<stddef.h>`）交给编码 Agent，modbus 的一个模块诊断为 test（测试的头文件包含写错）交给测试 Agent，两次都修好。

计划与提示词草案见 `docs/STAGE3_PLAN.md`，模型档位、接口字段等决定见 DECISIONS 第 37–48 条。

- 6 个 Agent（`agents/*.toml`）：规划、测试、诊断用 strong 档，审查用 fast 档，编码有 coder-fast 和 coder-strong 两个；每个角色一份提示词（`prompts/`）。注册表支持按角色和任务类别查找、同名多版本取最新。
- 规划结果校验（`orchestrator/dag.py`）：无环、id 唯一、文件不重叠、路径在 `src/` 下、公共函数由且只由一个模块负责、接口头文件属于本模块；不合格带着问题清单重做，最多 2 次，仍不合格退化为单模块。
- 工作流模板 `workflows/module_dev.toml`，引擎按模板解释执行七种节点（agent、verify、parallel、condition、loop、subflow/foreach、human）。
- 执行引擎（`orchestrator/engine.py`）：asyncio，LLM 4 路、判定 4 路并发；没有依赖关系的模块同时进行，同一模块里编码和写测试同时进行；每次尝试一个隔离目录；全部模块完成后集成判定；按 Ctrl+C 不必等正在进行的请求返回。
- 角色隔离：测试 Agent 只看到需求、公共头文件和规划，修正轮交给它的报告和诊断指令先删去实现代码片段；编码 Agent 只能写本模块的文件，并且能看到完整规划和依赖模块的头文件。
- 修正循环（`orchestrator/loop.py`）：诊断 → 按责任交给编码或测试 Agent → 再判定，最多 6 次；这次比历史最好的一次差，就从最好的那次重新开始（rollback 事件）。
- 调度器（`orchestrator/scheduler.py`）：serial、round_robin、random、smart；smart 每次分配都把成功率、成本、负载、探索四项得分写进 `node_assigned` 事件。能力画像存在 SQLite 的 `agent_stats` 表。
- 审查：只在客观层全部通过后运行，high 级问题最多触发 1 次额外修改，修改没通过就退回，意见写进报告。
- 人工节点：`--human-review` 时规划完成后在终端停下等确认，输入意见则让规划 Agent 重做。
- 评测配置 b1、b2、b3、b4、full、b5；b5 的 token 预算自动取同一实验里 full 的平均值；`summary.md` 新增"每通过一题的平均 token 和成本"。
- 单元测试 161 个，Docker 中全部通过、无跳过。其中包括：测试 Agent 的消息里没有实现代码（修正轮也一样）、模块并行与依赖顺序（看事件时间戳）、诊断分派、回滚、四种调度策略与 smart 的公式、审查只修 1 次、人工节点、b5 预算、所有角色都看不到隐藏测试；以及把 Modbus 拆成 crc → frame → slave 三个模块跑完整流程，交付物通过隐藏测试。以上全部用假 LLM，真实模型的表现要等验收。

**阶段 4（升级、经验库、知识库、桌面软件）：进行中。**

- 新增 10 道难度 3 新题的清单与第四批规则已审核（DECISIONS 第 55、56 条）。第四批 3 道题（xmodem_rx、mavlink_parser、onewire_ds18b20）已写完，Docker 中自检通过：参考实现 PASS，12 个变异体全部被隐藏测试抓到；隐藏测试清单已审核（DECISIONS 第 57 条）。第五批 3 道题（isotp、sd_spi_init、at_parser）已写完，Docker 中自检通过：12 个变异体全部被隐藏测试抓到；隐藏测试清单已审核（DECISIONS 第 59 条）。第六批 4 道题（pid_ctrl、stepper_ramp、mem_pool、flash_kv）已写完，Docker 中自检通过：16 个变异体全部被隐藏测试抓到；隐藏测试清单待审核（`tasks_inbox/batch6_hidden_tests.md`）。**评测集达到 20 道**（数据结构 4、协议 6、状态机 6、驱动 4；难度 3 的 14 道），`check-task --all` 全部通过。
- 升级阶梯：同一个错误签名连续出现或本级修了 3 轮还没过就升一级——检索知识库 → 换 Agent 从头重写 → 重新规划 → 转人工；每升一级发 `escalated` 事件。
- 经验库（`memory/experience.py`）：修正让失败层前进时由诊断 Agent 总结一条经验，相似的合并；编码时按需求关键词或失败的 key 检索 top-3 放进上下文。
- 知识库（`kb/` 下 7 篇自写摘要，`memory/knowledge.py`），用标准库实现的 BM25 检索（`memory/bm25.py`），中文按单字和双字切分。
- 消融配置 `full-no-exp`、`full-no-esc`、`full-no-review`；`results.csv` 的 escalations、experiences_used 两列有了真实数值；`eval --memory` 可沿用另一个实验的经验库。
- 单元测试 176 个，Docker 中全部通过。新增的包括：同一个错误连续出现时逐级升级到第 4 级、重新规划不能改动已完成的模块、转人工时负责人的提示交给编码 Agent、第一次运行记下的经验在第二次运行里被用上。以上全部用假 LLM，真实模型的表现要等验收。
- 还没做：桌面软件（先出界面草图）。

### 阶段 3 验收命令（需要 `.env` 里的 key，会产生费用）

正式评测按 DECISIONS 第 49 条用 `--configs b2,full,b5`。

```bash
docker build -t firmforge -f docker/Dockerfile .
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge run --task tasks/modbus_slave --config full
docker run --rm -it --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge run --task tasks/modbus_slave --config full --human-review
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge eval --configs b1,b2,b3,b4,full,b5 --tasks all --runs 1 --out results/stage3 --estimate
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge eval --configs b1,b2,b3,b4,full,b5 --tasks all --runs 1 --out results/stage3
```

### 阶段 2 验收命令

```bash
docker build -t firmforge -f docker/Dockerfile .
docker run --rm --env-file .env -v "$PWD":/work -w /work firmforge models
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge run --task tasks/ringbuf --config b2
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge eval --configs b1,b2 --tasks all --runs 1 --out results/first
docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge replay .ff_runs/<运行目录名>
```

## 快速开始

```bash
docker build -t firmforge -f docker/Dockerfile .
docker run --rm firmforge doctor
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge check-task --all
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge verify tests/fixtures/syntax_error --task tasks/ringbuf
```

不用 Docker、直接在本机运行（缺失的工具对应的层会显示 `[SKIP]`，结论最多是 incomplete）：

```bash
PYTHONPATH=src python3 -m firmforge doctor
python3 -m unittest discover -s tests -v
```

## 判定器

| 层 | 名称 | 通过条件 |
|---|---|---|
| L1 | host_compile 主机编译 | `gcc -std=c11 -Wall -Wextra -Werror -pedantic` 逐文件编译通过 |
| L2 | cross_compile 交叉编译 | `arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -Os … -Werror` 通过 |
| L3 | resources 资源预算 | flash、静态 RAM、单函数栈帧不超预算，没有动态栈 |
| L4 | static_check 静态检查 | 没有禁用符号（malloc、printf 等）；cppcheck 没有 error/warning |
| L5 | unit_tests 单元测试 | Agent 写的测试全部通过，无崩溃、无 sanitizer 报错、无超时，测试数 > 0 |
| — | hidden_tests 隐藏测试 | 任务自带的参考测试；只有评测流程运行，Agent 看不到 |

退出码：0 通过，1 失败，2 不完整（有层因工具缺失被跳过），3 用法错误。

环境变量：`FF_CC`、`FF_CROSS_CC`、`FF_CPPCHECK` 等覆盖工具命令；`FF_TEST_TIMEOUT` 测试超时秒数（默认 10）；
`FF_SANITIZE=0` 关闭 ASan/UBSan。

## 已知局限

- 调用链栈用量不计外部库函数（如 memcpy）的栈帧；有函数指针间接调用时无法完整计算（本层标为不完整）。
- 循环上界只靠运行超时兜底，没有静态证明（约束审计 A8）。
- 分时计价只区分工作日和周末，法定节假日按普通工作日处理。
- 在 Linux 上用 `docker run -v` 运行时要加 `--user "$(id -u):$(id -g)"`，否则容器内生成的 `.ff_build/` 属于 root。
- 国内网络下拉取基础镜像需要给 Docker 守护进程配置代理（`/etc/systemd/system/docker.service.d/http-proxy.conf`）。

## 开源组件

- [Unity](https://github.com/ThrowTheSwitch/Unity) v2.6.1，MIT 许可，原样放在 `third_party/unity/`。

## 人工智能使用说明

代码由 Claude Code 按设计文档实现，项目负责人负责想法、设计决定和验收（见 `docs/DECISIONS.md` 第 5 条）。
