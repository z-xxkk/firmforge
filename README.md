# FirmForge

面向嵌入式固件开发的多 Agent 编排与验证平台（第五届"未来杯"技术大赛赛题一参赛作品）。
目标芯片 STM32F103（Cortex-M3）。设计文档见 `docs/ARCHITECTURE.md`，开发顺序见 `docs/ROADMAP.md`。

## 当前进度

**嵌入式新流程：上板评测题在真板上自检通过（2026-11-03），第 1 轮真实模型上板评测完成（2026-11-08）。** 板子 ESP32-C3 SuperMini（实测 ESP32-C3 AZ rev v1.1，合封 4 MB Flash），面包板接 MPU6050 模块和 MAX30102。

- `firmforge check-board-tasks --board`：11 道上板评测题全部符合预期。每题的标准答案通过全部隐藏用例（共 53 个），34 个应在板上失败的变异体全部被抓住。另有 1 个变异体由合规检查拦下。
- 上板测试的每个用例单独开机运行：跑完一个就软重启，中途失败不会连累后面的用例（DECISIONS 第 91 条）。每个用例开始前还会把外接的 MPU6050、MAX30102 软复位到上电状态，上一个用例的配置不会留下来（第 96 条）。
- 上板实测发现资料与硬件不符的两处，已按负责人决定修改：
  - MPU 模块其实是 MPU-6500 兼容芯片：WHO_AM_I 读出 0x70，温度系数不同。现在硬件档案里记录实测值，题目按档案换算（第 89 条）。
  - MAX30102 测温时，TEMP_EN 0.26 ms 就清零，但温度数据 27.5 ms 后才出来。规则改为等完成标志，并新增一个变异体（第 90 条）。
- 上板评测运行器 `firmforge board-eval`（DECISIONS 第 92 条）：
  - 四种配置：single 单 Agent / none 多 Agent 无规则 / unverified 未核对规则 / verified 已确认规则库。
  - 所有配置用同一套判定：固定合规规则 → 编译 → 上板隐藏测试。
  - 输出 summary.md 和 runs.csv，中断后可以接着跑。
  - 有花费上限（`--max-cost` 必填）；默认所有 Agent 用 deepseek-flash、思考强度 low，每个模块最多尝试 4 次；未核对规则每题生成一次、各次运行复用（第 94 条）。
  - 已用假模型在真板上跑通：交标准答案判通过（隐藏用例 5/5）；交变异体时，流程自己的简单测试说通过，评测判定仍判不通过（0/5）。
- 可信规则库：用 `firmforge rules build` 建好，47 条经负责人逐条确认。
- 第 1 轮真实模型上板评测（2026-11-08）：6 题、4 种配置、每题每种配置跑 2 次，共 48 次，合计花费 11.26 元。
  - 通过次数：single 5/12，none 7/12，unverified 7/12，verified 4/12。
  - 这一轮**不支持**"可信规则库提高通过率"。样本也太小，差异不显著（p≈0.41）。
  - verified 失败的主要原因：有一条规则只写了"等完成标志"，没写这一位在第几位；还有外接传感器不随芯片复位的评测设计问题。
  - 详见 `docs/BOARD_EVAL_RESULTS.md`，原始数据在 `docs/eval_data/board_full_20261108/`。
- 失败分析案例（2026-11-09，单独列出，不并入正式数据）：max30102_temp 的失败一路查到平台读手册时丢了表格列位置。平台改为另存按版面提取的文字，并按 PDF 坐标标出寄存器单个位在第几位（DECISIONS 第 98、99 条）。改完后 verified 两次都用对了位，1/2 通过（另一次错在温度小数换算）。详见 `docs/CASE_DIE_TEMP.md`。
- 每个用例前软复位外接传感器（第 96 条）；冻结前试点 8 次，平台运行正常。
- 桌面软件接入嵌入式新流程（2026-11-09，DECISIONS 第 100 条；截图 `docs/screenshots/gui_board_run.png`、`gui_hardware.png`、`gui_eval.png`）：
  - 对话页可以选"上板"流程：写一句需求，或选一道上板评测题按某种配置跑；能看到每次尝试的合规、编译、上板测试，上板测试的用例和串口原文。
  - 硬件页：硬件档案、实测记录、规则库；规则确认改在界面上逐条通过或驳回。
  - 评测页：设置、预估花费、题目 × 配置结果表、运行记录，读的是和命令行同一份 runs.jsonl。
  - 已验证（假模型、真板子，不花钱）：从对话页写一句需求跑完整流程（查资料 → 在硬件页通过 1 条、驳回 1 条 → 设计 → 编码、测试 → 上板 → 诊断修正）；从对话页跑一道评测题，隐藏测试 6/6；评测页开始、运行中暂停、再开始接着跑；硬件页查资料提炼规则并确认入库。三种窗口大小（最小 1200×760）× 深浅两种主题逐页检查，面板都不超出窗口。还没有用真实模型从界面跑过。
  - 同一时间只能有一个运行用板子：对话页的上板运行没结束时，评测页不能开始，反过来也一样。
- 对话页"代码"标签可以导出代码（每个模块最后一次交的那份，复制到你选的文件夹），也可以直接打开运行目录。
- 工作流设计器的拖拽改成主流做法（2026-11-10，DECISIONS 第 102 条）：从步骤箱拖进画布；拖到连线上插进去、拖到步骤下面接在后面；已连好的步骤拖远连线消失、前后自动接上，靠近连线或步骤就吸附；鼠标移到连线上点 × 断开；没连上的步骤用红色虚线框标出，连上或删除前不能保存、不能试运行。

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

**阶段 4（升级、经验库、知识库、工具与 MCP、桌面软件）：功能已全部实现（2026-10-26），用假模型测试通过；真实模型的表现等负责人验收。**

评测集与记忆
- 评测集 20 道（数据结构 4、协议 6、状态机 6、驱动 4；难度 3 的 14 道），`check-task --all` 全部通过；第四到六批规则与隐藏测试清单均已审核（DECISIONS 第 55–61 条）。
- 升级阶梯：同一个错误签名连续出现或本级修了 3 轮还没过就升一级——检索知识库 → 换 Agent 从头重写 → 重新规划 → 转人工；每升一级发 `escalated` 事件。
- 经验库（`memory/experience.py`）与知识库（`kb/` 下 7 篇，`memory/knowledge.py`，标准库 BM25）。

工具与 MCP（DECISIONS 第 63 条，框架见 `tasks_inbox/tools_mcp_framework.md`，逐点注明借鉴了 DeepSeek Harness、Claude Code、OpenAI Agents SDK、Hermes）
- Function Calling：工具箱按工具集分组（knowledge、build、interface、mcp-<服务名>）；每次调用走"记录 → 检查 → 守卫 → 执行 → 整理 → 记录结果"六步流水线；一次生成最多 5 轮，按 DeepSeek 要求回传思考内容；结果超过 8000 字截断并另存。
- 3 个内置工具：`kb_search` 查知识库、`compile_check` 草稿试编译（只跑主机编译和交叉编译，碰不到隐藏测试）、`list_interface` 看接口。单 Agent 基线也有同样的工具；消融配置 `full-no-tools` 量出工具本身的作用。
- MCP 服务端 `firmforge mcp`：同时支持 2026-07-28（无握手）和 2025-11-25（initialize），提供 `list_tasks`、`verify_c_module`（不跑隐藏测试）、`kb_search`；`firmforge mcp --selftest` 自检。
- MCP 客户端：按 `firmforge.toml` 的 `[mcp.servers.*]` 启动本机外部服务，先 `server/discover` 试探再退回旧版握手；清掉名字像密钥的环境变量；断线重连最多 5 次；默认每次调用前问人；评测时一律不用。

Agent、工作流与提示词
- Agent 定义加了技能、工具集、记忆、思考强度（关 / 低 / 高 / 最高，对应 DeepSeek 的 thinking 与 reasoning_effort），都实际生效；`firmforge agents` 列出全部 Agent、版本和历史表现。
- 发给所有 Agent 的用户消息统一为"任务单"五节：意图、当前位置、引用、约束、附件（`agents/brief.py`）。
- 工作流可以保存成新版本（旧版本进 `workflows/history/`）；保存前检查引擎能不能执行；引擎放宽为任意位置可以"等人确认"、步骤可以指定 Agent。`run --workflow 名字@版本`。
- 对话核心（`chat.py`）：需求分析 Agent 追问并整理出需求文档和头文件（先做语法检查）；运行后追问会附上运行摘要；`@模块`、`@步骤`、`@src/文件` 变成任务单里的引用；让 AI 改工作流。
- 每次运行可以设 token 和费用上限，超过就停。

性能测试（DECISIONS 第 65 条）
- `firmforge bench`：假模型（固定延迟）测串行与智能并行在不同并发上限下的吞吐、并发数、名额利用率，真工具链测判定并发。例：4 个模块、每次调用 0.5 秒时，串行 6.2 秒，智能并行（并发 4）2.4 秒。
- `firmforge stats <运行目录>`：按 Agent、模型、工具统计延迟中位数、90% 分位和 token，以及判定耗时。

桌面软件（DECISIONS 第 62 条，界面草图第 4 版；截图见 `docs/screenshots/`）
- 四个工作区：对话（会话列表、和 Agent 团队的对话、模块图 / 流程图、检查结果、代码、调度得分、"发给 AI 的内容"）、工作流设计器（照 Qt Designer 的步骤箱 / 画布 / 步骤树 / 属性 / 代码，图和 TOML 双向同步，可以让 AI 改）、Agent 团队、设置（模型、工具与 MCP、记忆与知识库、人工与权限、预算、高级，写回 `firmforge.toml` 和 `.env`，密钥只写不读）。
- 输入框下选模型档位、思考强度、配置、评测题；支持 `/task`、`/model`、`/think`、`/config`、`/workflow` 和 `@` 引用。
- 两种后端：docker（默认，容器里跑命令行，人工回复经标准输入传回）和本机（后台线程直接调核心库）；历史运行按倍速回放。
- 浅色简约、圆角分档、松石绿强调色，跟随系统深浅色；所有面板之间可以拖动调整大小，双击分隔条恢复，关闭时记住布局。

单元测试 249 个：判定器镜像里 246 个通过、3 个界面测试跳过（没装 PySide6）；这 3 个在 `firmforge-gui` 镜像里以 offscreen 方式通过。全部用假模型，不联网。
已在 Ubuntu 24.04 开发机上装好（`python3 -m venv .venv` 后 `.venv/bin/pip install -e .[gui]`，PySide6 6.12），真实窗口能打开，界面测试在本机也通过。

已知局限
- 桌面软件需要 PySide6：`pip install -e .[gui]`（依赖是 PySide6-Essentials，约 100 MB）。Ubuntu 系统 Python 没带 venv 时先装 `python3.12-venv`（要管理员密码）；也可以用 `docker/Dockerfile.gui` 跑界面测试和截图，但看不到窗口。
- 等宽字体 JetBrains Mono 没有随软件打包，没装时退回系统等宽字体。
- 本机后端点"停止"不能打断正在进行的模型调用，只能等这次调用返回；docker 后端会直接结束容器。
- docker 后端不启动外部 MCP 服务（容器里通常没有对应的命令）。
- 外部 MCP 只支持本机 stdio，不支持远程 HTTP；首批只配了 `fetch` 一个示例，默认关闭。
- 自己写的需求（对话生成的）没有隐藏测试，结论只代表通过了 Agent 自己写的测试；界面上会注明。
- 加了工具、改成任务单格式以后，Agent 看到的内容变了，阶段 3 的评测数据只能当"无工具版"参考，阶段 5 要重跑。

### 阶段 4 验收命令

```bash
docker build -t firmforge -f docker/Dockerfile .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work --entrypoint python3 firmforge -m unittest discover -s tests
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge agents
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge mcp --selftest
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge bench --out results/bench
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge stats .ff_runs
pip install -e .[gui]
firmforge gui
```

用真实模型试一题（会产生费用）：

```bash
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge run --task tasks/flash_kv --config full
```

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

## 运行环境与部署

### 我们用的环境

| 项目 | 版本 |
|---|---|
| 操作系统 | Ubuntu 24.04.5 LTS（x86_64）；只在 Linux 上开发和测试，没有做 Windows（DECISIONS 第 101 条） |
| Python | 3.12.3（要求 ≥ 3.11） |
| 核心库依赖 | 只用 Python 标准库 |
| 可选依赖 | 桌面软件 `PySide6-Essentials`（≥ 6.7，测试用 6.12.0）；读 PDF 资料 `pypdf`（≥ 5，测试用 6.21.4） |
| Docker | 29.1.3；判定器的工具链（gcc、arm-none-eabi-gcc、cppcheck、gcov）装在镜像里 |
| ESP-IDF | v6.1，默认装在 `~/esp/esp-idf-v6.1`，也可以用环境变量 `FF_IDF_PATH` 或 `IDF_PATH` 指定 |
| 大模型 | DeepSeek（OpenAI 兼容接口），API key 放在 `.env` |
| 开发板 | ESP32-C3 SuperMini（实测芯片 ESP32-C3 AZ rev v1.1），USB-C 连电脑，串口一般是 `/dev/ttyACM0` |
| 外接器件 | MPU6050 模块（实为 MPU-6500 兼容芯片）、MAX30102 模块 |

接线（以 `hardware/esp32c3_supermini/profile.json` 为准）：

| 信号 | GPIO | 说明 |
|---|---|---|
| I2C SDA / SCL | 4 / 5 | MPU6050 和 MAX30102 共用这条总线 |
| MPU6050 INT | 3 | |
| MAX30102 INT | 10 | 低电平有效、开漏，需上拉 |
| UART1 TX / RX | 0 / 1 | 回环测试：TX 接 RX |
| PWM 输出 / 输入 | 6 / 7 | 回环测试：6 接 7 |

### 部署步骤

1. 取代码，建虚拟环境，装可选依赖：
   ```bash
   git clone https://github.com/z-xxkk/firmforge.git && cd firmforge
   python3 -m venv .venv
   .venv/bin/pip install -e ".[gui,sources]"
   ```
2. 构建判定器镜像：
   ```bash
   docker build -t firmforge -f docker/Dockerfile .
   ```
3. 安装 ESP-IDF v6.1（上板相关的功能需要）：按乐鑫官方文档安装到 `~/esp/esp-idf-v6.1`，目标芯片选 esp32c3。
4. 填 API key：把 `.env.example` 复制成 `.env`，填入 DeepSeek 的 key（只放在 `.env`，不要写进代码）。
5. 串口权限：当前用户要能读写 `/dev/ttyACM0`（Ubuntu 上一般在 `plugdev` 或 `dialout` 组里）。
6. 检查环境：
   ```bash
   .venv/bin/firmforge doctor
   .venv/bin/python -m unittest discover -s tests
   ```
7. 板子插好以后，自检上板评测题（标准答案要通过、变异体要被抓住，不调用模型、不花钱）：
   ```bash
   .venv/bin/firmforge check-board-tasks --board
   ```
8. 打开桌面软件：
   ```bash
   .venv/bin/firmforge gui
   ```
   想在桌面的"显示应用"里直接点开，运行一次 `.venv/bin/firmforge gui --install-shortcut`（`--remove-shortcut` 去掉）。

### 没有板子时能复现什么

- 全部单元测试（假模型，不联网）：`.venv/bin/python -m unittest discover -s tests`。
- 上板评测题只编译、不上板：`firmforge check-board-tasks`（不加 `--board`）。
- 第 1 轮评测、失败分析案例和试点的原始数据都在 `docs/eval_data/`，桌面软件的评测页可以直接读这些 `runs.jsonl`。

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
- 桌面软件里确认规则要一次表态完：没表态的规则不会留到下次（规则流程只把通过的规则入库）。
- 只在 Linux（Ubuntu 24.04）上开发和测试，没有做 Windows（DECISIONS 第 101 条）。自动找串口只认 `/dev/ttyACM*`、`/dev/serial/by-id/*` 等，也可以用环境变量 `FF_SERIAL_PORT` 指定。

## 开源组件

- [Unity](https://github.com/ThrowTheSwitch/Unity) v2.6.1，MIT 许可，原样放在 `third_party/unity/`。

## 人工智能使用说明

代码由 Claude Code 按设计文档实现，项目负责人负责想法、设计决定和验收（见 `docs/DECISIONS.md` 第 5 条）。
