# FirmForge 架构设计

本文档是实现依据。代码与本文档不一致时，以本文档为准；需要修改本文档时，先征得项目负责人同意（见 CLAUDE.md）。

## 0. 总览

```
┌────────────────────────────────────────────────────────────────┐
│ 界面层   命令行 cli.py            桌面软件 gui/（PySide6）       │
│          └──── 只调用核心库公开接口，靠事件流获取进度 ────┘      │
├────────────────────────────────────────────────────────────────┤
│ 编排层   工作流引擎 engine   调度器 scheduler   自我循环 loop     │
│          Agent 注册表 registry   事件总线 events                 │
├────────────────────────────────────────────────────────────────┤
│ Agent 层 规划 planner  编码 coder×N  测试 tester                 │
│          审查 reviewer  诊断 diagnoser                           │
├────────────────────────────────────────────────────────────────┤
│ 工具层   判定器 verifier（L1–L5 + 隐藏测试）                      │
│          知识库 knowledge   经验库 experience   LLM 客户端 llm    │
├────────────────────────────────────────────────────────────────┤
│ 存储层   运行目录 .ff_runs/（快照、事件、调用日志）              │
│          SQLite .ff_data/firmforge.db（统计、经验）              │
└────────────────────────────────────────────────────────────────┘
```

一次运行的主线：

```
需求 + 任务 → 规划 Agent 生成模块 DAG
          → 调度器按依赖和优先级派发模块
          → 每个模块：编码 ∥ 写测试 → 判定器 → 通过？
                        否 → 诊断 → 修正 / 升级 → 再判定
          → 全部模块通过 → 集成判定 → 交付代码 + 验证报告
          → 成功的修正总结成经验写入经验库
```

## 1. 目录结构

```
firmforge/
├── CLAUDE.md                 Claude Code 项目规则
├── README.md
├── pyproject.toml            无运行依赖；可选依赖 [gui] = PySide6
├── firmforge.toml            全局配置（模型档位、并发、循环参数、调度权重）
├── .env.example              DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
├── docker/Dockerfile
├── third_party/
│   ├── unity/                Unity 测试框架源码（MIT），原样拷贝 unity.c/.h/_internals.h + LICENSE
│   └── unity_support/unity_weak_hooks.c   弱符号 setUp/tearDown
├── agents/*.toml             Agent 定义
├── prompts/*.md              提示词模板（string.Template 占位符 $spec 等）
├── workflows/*.toml          工作流模板
├── kb/*.md                   知识库文档（自己写的摘要，不整段复制规范原文）
├── tasks/<id>/               评测任务（见 §2.1）
├── src/firmforge/
│   ├── config.py             Toolchain（外部命令）与 Settings（firmforge.toml）
│   ├── tasks.py              任务加载
│   ├── verifier/             report.py signature.py layers.py runner.py selfcheck.py
│   ├── llm/                  client.py（OpenAI 兼容）fake.py log.py
│   ├── agents/               base.py protocol.py roles.py registry.py
│   ├── orchestrator/         dag.py workflow.py engine.py scheduler.py loop.py events.py
│   ├── memory/               bm25.py experience.py knowledge.py
│   ├── store.py              SQLite 访问
│   ├── runs.py               运行目录与快照
│   ├── evaluation/           runner.py configs.py metrics.py
│   ├── cli.py
│   ├── mcp_server.py         可选：把判定器暴露为 MCP 工具
│   └── gui/                  PySide6，唯一允许第三方依赖的子包
├── tests/                    unittest
└── docs/                     ARCHITECTURE.md ROADMAP.md DECISIONS.md
```

运行时生成、已加入 `.gitignore`：`.ff_build/`、`.ff_runs/`、`.ff_data/`、`results/`、`.env`。

## 2. 数据格式

### 2.1 评测任务 `tasks/<id>/`

```
task.toml          元数据、预算、规则、变异体
spec.md            给 Agent 看的需求（中文，写清边界行为和 NULL 处理）
include/*.h        公共接口头文件——由任务锁定，Agent 不能修改
hidden_tests/*.c   隐藏参考测试——Agent 永远看不到
                   test_*.c 是测试程序（Unity，自带 main）；其他 .c 是支撑文件（如 mock HAL），
                   会链接进该目录下的每个测试程序
reference/*.c      人写的参考实现，用来检验隐藏测试本身
mutants/*.c        故意改坏的版本，用来确认隐藏测试能抓到错误
```

`task.toml`：

```toml
id = "ringbuf"
title = "字节环形缓冲区"
category = "data_structure"      # data_structure | protocol | state_machine | driver
difficulty = 1                    # 1 简单 / 2 中等 / 3 困难
source = "嵌入式常见模块，参考 UART 接收缓冲区的典型写法"   # 任务来源，答辩时会被问

[budget]                          # 不写的项不检查
flash_bytes = 512                 # text + data
ram_bytes = 0                     # data + bss（模块自身静态 RAM）
stack_bytes = 64                  # 单个函数最大栈帧

[rules]
extra_forbidden_symbols = []      # 在默认禁用列表上追加
allowed_symbols = []              # 从默认禁用列表中移除

[[mutants]]
file = "mutants/no_wrap.c"
replaces = "ringbuf.c"            # 替换 reference/ 中的哪个文件
expect_fail = "hidden_tests"      # 期望最先失败的层
why = "写指针没有取模，越界写"
```

默认禁用符号（DECISIONS 第 26 条）：动态内存（MISRA C:2012 Rule 21.3）；`<stdio.h>` 全部输入输出函数（Rule 21.6）；`abort exit _Exit quick_exit getenv system`（Rule 21.8）；`setjmp longjmp`（Rule 21.4）；`signal raise`（Rule 21.5）；进程与网络调用（fork、exec 系列、socket 等，最小权限原则）。完整列表以 `src/firmforge/tasks.py` 的 `DEFAULT_FORBIDDEN_SYMBOLS` 为准。

预算的定法：先用 `check-task` 量出参考实现在 arm-none-eabi-gcc `-Os` 下的实际用量，再取约 1.5–2 倍。预算由人定，写进 task.toml。

### 2.2 工作目录（一次尝试的代码）

```
<workdir>/
├── src/*.c  src/*.h     实现（内部头文件也放这里）
└── tests/test_*.c       Agent 写的单元测试（Unity，每个文件自带 main）
    tests/*.c            其他 .c 为支撑文件（如测试 Agent 自己写的 mock HAL），链接进每个测试程序
```

公共头文件不放在工作目录里。判定器编译时用 `-I <task>/include`，因此 Agent 改不了接口。

### 2.3 判定器报告（JSON）

```json
{
  "verdict": "fail",                 // pass | fail | incomplete（无失败但有层或子检查被跳过）
  "failed_layer": "unit_tests",      // 第一个失败的层；通过时为 null
  "signature": "unit_tests:3fa2c81d9e01",
  "duration_s": 3.42,
  "layers": [
    {
      "layer": "host_compile", "title": "主机编译", "status": "pass",
      "duration_s": 0.31, "complete": true, "signature": null,
      "diagnostics": [], "metrics": {"files": 1}, "notes": [], "log": ""
    },
    {
      "layer": "unit_tests", "title": "单元测试", "status": "fail",
      "diagnostics": [
        {"severity": "error", "file": "tests/test_ringbuf.c", "line": 41,
         "message": "test_wraparound:FAIL: Expected 3 Was 0",
         "key": "test:test_wraparound:FAIL"}
      ],
      "metrics": {"total": 9, "failed": 1, "ignored": 0, "coverage_pct": 87.5}
    }
  ]
}
```

- `status`：`pass`、`fail` 或 `skipped`。
- `complete=false`：本层有子检查因工具缺失没跑（例如 cppcheck 未安装），此时报告的 `verdict` 最多是 `incomplete`。
- `log`：原始输出，截断到 4000 字符。
- **给 Agent 看的报告**由 `Report.for_agent()` 生成，一定不包含 `hidden_tests` 层。

### 2.4 错误签名

用来判断修正是不是在原地打转。

- 每条诊断都有一个归一化的 `key`：
  - 编译类：去掉路径、行列号、内存地址、进程号后的报错文本；
  - 测试类：`test:<测试函数名>:FAIL`；
  - 资源类：`budget:flash` 这种形式；
  - 静态检查类：`forbidden:<符号>` 或 `cppcheck:<规则 id>`。
- 每层的签名 = `<层名>:` + sha1(排序去重后的 keys)[:12]。
- 报告的签名 = 第一个失败层的签名。
- 保留的信息：错误类型、标识符名、测试函数名。这些变了，说明错误真的变了。

### 2.5 Agent 定义 `agents/*.toml`

```toml
name = "coder-strong"
role = "coder"                    # planner | coder | tester | reviewer | diagnoser
version = "1.0.0"
model_tier = "strong"             # 对应 firmforge.toml 里的 [models.strong]
temperature = 0.2
max_tokens = 4096
prompt = "prompts/coder.md"
task_types = ["*"]                # 适用的任务类别，* 表示全部
description = "强模型编码 Agent，用于状态机、驱动等较难任务"
```

至少提供两个编码 Agent（`coder-fast` 用 fast 档，`coder-strong` 用 strong 档），能力画像调度才有意义。

### 2.6 全局配置 `firmforge.toml`

```toml
[llm]
api_key_env = "DEEPSEEK_API_KEY"
base_url_env = "DEEPSEEK_BASE_URL"
timeout_s = 120
max_retries = 3

[pricing]                         # 高峰 / 空闲分时计价（DECISIONS 第 24 条）
utc_offset_hours = 8              # 按北京时间判断
peak_hours = ["09:00-12:00", "14:00-18:00"]   # 工作日的高峰时段；其余时段和周末为空闲

[models.fast]                     # 型号以 `firmforge models` 列出的为准
name = ""
thinking = true                   # 思考模式（DECISIONS 第 23 条）
max_tokens = 65536                # 单次输出上限（含思考内容时要留足）
price_in_per_mtok = 0.0           # 高峰价，元 / 百万 token（输入按缓存未命中）
price_out_per_mtok = 0.0
price_in_offpeak_per_mtok = 0.0   # 空闲价
price_out_offpeak_per_mtok = 0.0

[models.strong]
name = ""
thinking = true
max_tokens = 65536
price_in_per_mtok = 0.0
price_out_per_mtok = 0.0
price_in_offpeak_per_mtok = 0.0
price_out_offpeak_per_mtok = 0.0

[concurrency]
llm = 4                           # 同时进行的 LLM 调用数
verify = 4                        # 同时进行的判定数

[loop]
max_attempts_per_module = 6
rounds_before_escalation = 3
repeat_threshold = 2              # 同一签名连续出现几次就升级

[scheduler]
alpha = 0.3                       # 成本权重
beta = 0.2                        # 负载权重
gamma = 0.5                       # 探索权重
seed = 42
```

### 2.7 工作流模板与规划结果

工作流模板 `workflows/module_dev.toml` 描述结构骨架。具体拆成哪些模块由规划 Agent 在运行时决定。

```toml
name = "module_dev"
version = "1.0.0"

[[main]]
id = "plan"
kind = "agent"
role = "planner"

[[main]]
id = "confirm_plan"
kind = "human"                    # 只有开启 human_review 时才停下等待确认
enabled_by = "human_review"

[[main]]
id = "modules"
kind = "foreach"                  # 对规划出的每个模块实例化子流程，按依赖并行
over = "plan.modules"
subflow = "module_impl"

[[main]]
id = "integrate"
kind = "verify"
scope = "all"

[[subflow.module_impl.steps]]     # TOML 1.0 的内联表不能跨行，所以用表数组
id = "write"
kind = "parallel"
branches = ["code", "test"]       # 编码与写测试并行

[[subflow.module_impl.steps]]
id = "code"
kind = "agent"
role = "coder"

[[subflow.module_impl.steps]]
id = "test"
kind = "agent"
role = "tester"

[[subflow.module_impl.steps]]
id = "check"
kind = "verify"
scope = "module"

[[subflow.module_impl.steps]]
id = "repair"
kind = "loop"
until = "verify_passed"
body = "repair_round"
max = "loop.max_attempts_per_module"

[[subflow.module_impl.steps]]
id = "review_gate"
kind = "condition"
if = "verify_passed"
then = ["review"]

[[subflow.module_impl.steps]]
id = "review"
kind = "agent"
role = "reviewer"
```

节点类型有七种：`agent`、`verify`、`parallel`、`condition`、`loop`、`subflow`/`foreach`、`human`。

- 被 `parallel` 或 `condition` 引用的步骤（上例的 `code`、`test`、`review`）只在引用处执行，不会再按顺序单独执行一次。
- 审查放在修正循环之后，由 `condition` 判断"客观层已通过"再执行，这样修正后才通过的模块也会被审查。
- 任何步骤都可以带 `when`（预定义条件）；`subflow` 步骤在当前模块里执行另一个子流程。
- `repair_round` 是引擎内置的复合步骤，过程是：诊断 → 按 blame 路由给 coder 或 tester → 再判定，升级逻辑也在里面（见 §8）。

条件只能用预定义名称，不能用 `eval`：
- `verify_passed`
- `signature_repeated`
- `attempts_exhausted`
- `review_needs_fix`
- `human_review`

规划结果 `dag.json`（规划 Agent 输出，引擎校验后使用）：

```json
{
  "modules": [
    {"id": "crc16", "title": "CRC16 计算", "task_type": "protocol",
     "files": ["src/crc16.c", "src/crc16_internal.h"],
     "provides": [], "depends_on": [],
     "interface": {"src/crc16_internal.h": ["uint16_t crc16_calc(const uint8_t *data, size_t len);"]},
     "acceptance": ["'123456789' 的 CRC 为 0x4B37"]},
    {"id": "frame_rx", "title": "接收状态机", "task_type": "state_machine",
     "files": ["src/frame_rx.c"],
     "provides": ["frame_rx_feed"], "depends_on": ["crc16"], "interface": {}, "acceptance": ["..."]}
  ]
}
```

- `provides`：本模块实现的**公共头文件**函数名；
- `interface`：本模块提供给其他模块的内部头文件 → 函数原型列表（DECISIONS 第 38 条）。头文件由提供方模块写，使用方把提供方列进 `depends_on`；
- 驱动类任务里 `hal_*.h` 声明的函数由外部（测试替身）提供，不需要任何模块 `provides`。

引擎校验以下几点，不通过就让规划 Agent 重做，最多 2 次：
- 无环；
- id 唯一；
- 模块之间文件不重叠；
- 公共头文件（`hal_*.h` 除外）里的每个函数都由且只由一个模块 `provides`，`provides` 里不能有公共头文件之外的名字；
- `interface` 的头文件必须在本模块的 `files` 里；
- 模块数 1–5 个；
- 文件路径都在 `src/` 下。

简单任务允许只有一个模块。

### 2.8 事件流

核心通过事件向界面报告进度。每个事件同时写入 `events.jsonl`（一行一个 JSON），并推送给订阅者。

```json
{"ts": 1730000000.12, "run_id": "20261024-101500-ringbuf", "type": "verify_finished",
 "node_id": "frame_rx", "data": {"attempt": 2, "verdict": "fail", "failed_layer": "unit_tests"}}
```

事件类型如下：
- 运行级：`run_started`、`plan_ready`、`human_wait`、`human_done`、`run_finished`
- 节点级：`node_ready`、`node_assigned`（含 agent、得分明细）、`node_done`、`node_failed`
- Agent 调用：`agent_started`、`agent_finished`（含 token、耗时）
- 判定与循环：`verify_started`、`verify_finished`、`diagnosis`、`escalated`（含升级级别）、`rollback`、`format_retry`（Agent 没按格式交文件，当场补要）、`design_rejected`（设计分层没通过检查，带着问题重做）
- 经验库：`experience_saved`、`experience_used`
- 工具（DECISIONS 第 63 条）：`tool_called`（调用前先记录）、`tool_result`（是否成功、耗时、摘要、是否截断）

界面只读事件，不直接访问引擎内部状态。所以回放（`firmforge replay`）和实时显示走的是同一条路径。

### 2.9 运行目录 `.ff_runs/<run_id>/`

```
run.json            需求、任务、配置、模型型号、开始/结束时间、最终结论、总 token 与成本
events.jsonl        事件流
llm_calls.jsonl     每次调用的摘要：agent、模型、token、耗时、成功与否
llm_calls/<n>.json  完整的请求消息与回复（请求头脱敏），用于复盘
dag.json            规划结果
nodes/<module>/attempt_<n>/   src/ tests/ report.json diagnosis.json   ← 快照
nodes/<module>/review.json    审查意见
integration/        合并后的工作目录
first/              每个模块第一次尝试合在一起（评测算首次通过率用）
final/              交付物：src/ tests/ report.json report.md
```

快照用"每次尝试一个目录"实现，回滚 = 从历史最好的尝试目录复制。比较两次尝试的差异用 `difflib`。

### 2.10 SQLite `.ff_data/firmforge.db`

```
runs(id, created_at, task_id, config, verdict, total_tokens, cost, wall_s)
node_attempts(run_id, node_id, attempt, agent, verdict, failed_layer, signature, tokens, latency_s)
agent_stats(agent, version, task_type, attempts, first_pass, successes, total_tokens, total_latency_s, total_cost)
experiences(id, category, layer, pattern, fix, source_run, created_at, uses, helped)
```

评测时每个实验用独立的数据库文件，避免实验之间互相污染。如需延续经验库，要显式加 `--memory <db>`。

## 3. 判定器

公开接口：

```python
verify(workdir: Path, task: Task, *, layers: list[str] | None = None,
       include_hidden: bool = False, stop_on_fail: bool = True,
       toolchain: Toolchain | None = None) -> Report
check_task(task: Task, toolchain: Toolchain | None = None) -> SelfCheckResult
```

- 构建目录为 `<workdir>/.ff_build/`，每次调用前清空。
- 不同 workdir 可以并发判定。
- `stop_on_fail=True` 时，第一个失败层之后的层记为 `skipped`，原因写"前一层失败，未运行"。

| 层 | layer | 命令要点 | 通过条件 |
|---|---|---|---|
| L1 主机编译 | `host_compile` | `gcc -std=c11 -Wall -Wextra -Werror -pedantic -I<task>/include -I<workdir>/src -c <f> -o .ff_build/host/<stem>.o`，逐文件编译 | 至少 1 个源文件，且全部返回 0 |
| L2 交叉编译 | `cross_compile` | `arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -Os -ffunction-sections -fdata-sections -std=c11 -Wall -Wextra -Werror -fstack-usage -I… -c <f> -o .ff_build/cross/<stem>.o`，工作目录为 `.ff_build/cross` | 全部返回 0；交叉编译器缺失则 `skipped` |
| L3 资源预算 | `resources` | `arm-none-eabi-size <objs>`（Berkeley 格式）；L2 加 `-fcallgraph-info=su` 得到调用图和每个函数的栈帧；另用 `-O0` 编译一次的调用图检查递归 | flash=Σ(text+data)、ram=Σ(data+bss)、调用链最坏栈用量均不超预算；出现 `dynamic` 栈（VLA/alloca）或递归判失败（DECISIONS 第 27 条） |
| L4 静态检查 | `static_check` | ① `arm-none-eabi-nm -u <obj>` 查禁用符号（L2 跳过时改用主机 nm 查主机目标文件）② `cppcheck --std=c11 --enable=warning,performance,portability --inline-suppr --quiet --suppress=missingIncludeSystem --template={file}:{line}:{severity}:{id}:{message} -I<include> <workdir>/src` | 无禁用符号，且 cppcheck 没有 error/warning 级别问题（其他级别只记为 info）；cppcheck 缺失时 `complete=false` |
| L5 单元测试 | `unit_tests` | 源文件编译选项 `-std=c11 -g -O0 -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=undefined --coverage`；unity.c 和弱钩子单独编译（不加 `-Werror`、不加覆盖率）；每个 `tests/test_*.c` 加上 `tests/` 中的其他 .c 支撑文件，单独链接成一个程序，带超时运行 | 没有测试编译/链接错误；没有 FAIL；没有异常退出（崩溃或 sanitizer 报错）；没有超时；测试总数 > 0 |
| 隐藏测试 | `hidden_tests` | 与 L5 相同的机制，测试目录换成 `<task>/hidden_tests`，不统计覆盖率 | 同 L5；只有 `include_hidden=True` 时运行 |

补充细节：

- **诊断解析**：GCC 格式用正则 `^(?P<file>(?:[A-Za-z]:)?[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$` 解析。链接错误单独匹配含 `undefined reference` 的行。note 级别忽略。
- **`.su` 格式**：GCC 是 `file:line:col:func<TAB>bytes<TAB>qualifier`，clang 是 `file:line:func<TAB>…`。函数名取 `rsplit(':', 1)[-1]`。
  - 调用链栈：最坏栈 = 从每个入口函数出发，沿调用图累加栈帧的最大值。外部库函数（如 memcpy）的栈帧未知，按 0 计并写进 `notes`；出现间接调用（函数指针）时无法确定调用目标，写进 `notes` 并把本层标为 `complete=false`。
  - 递归：`-Os` 会把尾递归改成循环、把小函数内联，所以递归检查用 `-O0` 编译的调用图做，任何环都判失败（MISRA Rule 17.2、Power of 10 第 1 条）。
- **Unity 输出解析**：
  - 通过：`^(?P<file>.+?):(?P<line>\d+):(?P<name>\w+):PASS`
  - 失败：`…:(?P<name>\w+):FAIL(?::\s*(?P<msg>.*))?$`
  - 汇总：`^(\d+) Tests (\d+) Failures (\d+) Ignored`
  - 没有汇总行且返回码非 0：判为异常退出。从输出中摘取 `ERROR: AddressSanitizer` 或 `runtime error:` 所在行作为诊断。
- **运行环境变量**：`ASAN_OPTIONS=detect_leaks=0`（容器里 LeakSanitizer 可能无法工作，而且本来就禁用了 malloc）、`UBSAN_OPTIONS=print_stacktrace=1`。
- **超时**：默认 10 秒，由 `FF_TEST_TIMEOUT` 控制，超时诊断为"超时（可能死循环）"。`FF_SANITIZE=0` 时去掉 sanitizer 选项（Windows 本机临时使用）。
- **覆盖率**：对每个源文件运行 `gcov -n -o <objdir> <src>`，解析 `Lines executed:(\d+\.\d+)% of (\d+)`，按行数加权平均。失败时记为 null，不影响判定结论。
- **`check_task`**：
  - 先用参考实现（`src = reference/*.c`，不带 Agent 测试）跑 L1–L4 和隐藏测试，期望没有任何 fail；
  - 再对每个变异体，用"参考实现 + 该变异体替换对应文件"跑一遍，期望**第一个失败层**正好是 `expect_fail`；
  - 输出一张表，并给出参考实现实测的 flash、ram、栈，供人工校准预算。

## 4. LLM 客户端

```python
@dataclass
class LLMResponse:
    text: str; model: str; prompt_tokens: int; completion_tokens: int
    latency_s: float; tool_calls: list | None; raw: dict

class LLMClient(Protocol):
    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None) -> LLMResponse: ...
```

- **`OpenAICompatClient`**：
  - 用 `urllib.request` 调 `POST {base_url}/chat/completions`，`list_models()` 调 `GET {base_url}/models`；
  - 超时和重试次数取自 `firmforge.toml`；
  - 遇到 429、5xx、超时就指数退避重试；
  - API key 从环境变量读，缺失时给出明确中文报错。
- **`FakeLLM`**：按预设脚本返回（可以是列表，也可以是根据消息内容决定回复的函数），供单元测试使用。
- **调用日志**：每次调用都写 `llm_calls.jsonl` 和 `llm_calls/<n>.json`，`Authorization` 请求头替换为 `***`。
- **成本**：每次调用按发生时间（北京时间）判断高峰或空闲，成本 = token 数 × 对应档位在该时段的价格；思考内容按输出计费。

## 5. Agent

### 5.1 输出协议（文件块）

Agent 要输出文件时，一律使用下面的格式。解析器只接受该角色允许写的目录，写到其他路径一律拒绝并记录。

```
<<<FILE src/ringbuf.c
…文件内容…
>>>FILE
```

需要输出结构化结果的角色（规划、审查、诊断），用 ```` ```json ```` 代码块输出一个 JSON 对象。解析失败时，带着错误信息重试 1 次。

### 5.2 角色

| 角色 | 输入 | 输出 | 可写路径 |
|---|---|---|---|
| planner 规划 | spec、公共头文件、知识库检索结果 | `dag.json`（§2.7） | 无 |
| coder 编码 | 模块说明、**完整的规划结果（全部模块的划分、职责、接口）**、公共头文件、依赖模块的头文件、预算和禁用符号、经验库提示；修正轮还有：诊断指令、上一轮代码、给 Agent 看的报告摘录 | 实现文件 | `src/` |
| tester 测试 | **只有** spec、公共头文件和规划（含接口原型），看不到实现 | `tests/test_<module>.c`（Unity，自带 main 和 RUN_TEST；HAL 替身也写在这个文件里） | 只能写 `tests/test_<module>.c` |
| reviewer 审查 | 实现代码、L1–L4 报告 | `{"verdict": "ok"\|"needs_fix", "issues": [{"severity","file","line","rule","message"}]}` | 无 |
| diagnoser 诊断（固定用 strong 档，DECISIONS 第 31 条） | 给 Agent 看的失败报告、实现、测试、本模块历次签名 | `{"blame": "code"\|"test"\|"interface"\|"spec", "summary", "instructions", "confidence"}` | 无 |

规则：

- **测试 Agent 看不到实现**：tester 的上下文里不能出现 `src/*.c` 的内容。修正轮交给 tester 的报告摘录和诊断指令，先删去 gcc 引用的源码行和与实现代码相同的片段。这条要有单元测试守住。
- **编码 Agent 只能写本模块的文件**：规划里本模块 `files` 以外的路径一律拒绝并记录。
- **审查只作提示**：审查在客观层（L1–L5）全部通过后才运行。如果出现 high 级别的 `needs_fix`，最多触发 1 次额外的编码轮。之后不管审查意见如何，都不能阻止通过，意见写进最终报告。
- **诊断决定修正路由**：
  - `blame = code`：交给 coder；
  - `blame = test`：交给 tester；
  - `blame = interface`：直接升级到"重新规划"（阶段 4；阶段 3 按 code 处理并在报告里标注，DECISIONS 第 39 条）；
  - `blame = spec`：在报告里标记"需求歧义"，然后按 code 处理。

### 5.3 注册表

```python
registry.load_dir("agents/")
registry.register(agent_def)
registry.unregister(name)
registry.find(role, task_type) -> list[AgentDef]
registry.get(name, version=None)
```

- 同名的不同版本可以共存，默认取最新版本。
- 能力画像来自 SQLite 的 `agent_stats` 表，由注册表对外提供查询。

## 6. 编排与执行引擎

> **两种执行方式（DECISIONS 第 110 条）**：纯逻辑流程由下面的通用引擎（`orchestrator/engine.py`）执行 `workflows/module_dev.toml`；
> 上板流程由解释器 `embedded/fw_interpreter.py` 逐步执行上板工作流（`engine = "firmware"`，DECISIONS 第 117 条）：
> 每种步骤一个处理函数（查资料、设计、等人确认、条件、每个模块各跑；模块里的编码、写测试、并行、检查、修正循环、审查、等人确认、条件），
> 顺序和结构完全按文件走；真正干活的（调用 Agent、编译、上板、诊断、调度选人、经验库）复用 `embedded/flow.py` 的方法。
> `embedded/firmware_workflow.py` 检查设计器画出来的结构解释器能不能照做（`engine_problems` 按 engine 分派），放行的结构都有测试保证能跑完。
> 自带两个上板工作流：`firmware_dev`（默认）和 `firmware_review`（硬件层模块通过后审查、开了人工确认时每个模块做完等人看）。
> 编码交给谁由 smart 调度器打分，成绩按 `fw-<层>-<first|repair>` 分组记在 SQLite 的 agent_stats；修好的问题存进经验库（类别 `firmware-<层>`）。
> 评测（`board_eval.py`）不传工作流，走 `flow.py` 原来的固定路径，四种配置和以前的数据可比；测试保证"和固定路径做同一件事的工作流"调用和检查完全一样。

- 用 `asyncio` 实现。LLM 调用和判定都是阻塞调用，通过 `asyncio.to_thread` 执行，用两个信号量分别限制并发数（`concurrency.llm` 和 `concurrency.verify`）。
- 节点状态：`pending → ready → running → done | failed | skipped`。每次状态变化都发事件。
- **模块并行与隔离**：
  - 每个模块的每次尝试都在自己的目录 `nodes/<module>/attempt_<n>/` 里进行；
  - 目录内容 = 已完成依赖模块的文件（复制进来）+ 本模块的文件；
  - 模块通过后，它的文件复制进 `integration/`；
  - 全部模块完成后，对 `integration/` 做集成判定，范围是全部 Agent 测试。
- **人工节点**：
  - 引擎暂停，并发 `human_wait` 事件；
  - 命令行在终端里询问，桌面软件弹窗询问；
  - 评测模式下自动跳过（视为确认）。
- `run(spec_or_task, config) -> RunResult` 是唯一的入口，命令行、桌面软件、评测都调用它。

## 7. 调度器

调度器回答两个问题：先做哪个就绪节点，交给哪个 Agent。

**优先级**：就绪节点按"到终点的最长路径长度"降序排列（关键路径优先），相同时按规划里的顺序（模块没有单独的难度）。

**策略接口**：`choose(node, candidates, stats, load) -> (agent, score_breakdown)`，有以下实现：

| 策略 | 用途 | 规则 |
|---|---|---|
| `serial` | 基线 B3 | 一次只跑一个节点（模块内的编码和写测试也先后进行），按拓扑序；固定用配置指定的 Agent（B3 是 coder-strong，与 B1/B2 同一个模型），没指定时用该角色注册的第一个 |
| `round_robin` | 基线 B4 | 并行；Agent 轮流分配 |
| `random` | 对照 | 并行；随机选（固定种子） |
| `smart` | 完整系统 | 并行；按下式打分取最高 |

```
score(a, t) = p̂ − α·cost_norm − β·load + γ·sqrt( ln(N + 1) / (n_a + 1) )

p̂        = (该 Agent 在此类任务上的成功次数 + 1) / (尝试次数 + 2)    拉普拉斯平滑
cost_norm = 该 Agent 平均成本 / 候选者中的最大平均成本
load      = 该 Agent 正在处理的节点数 / 并发上限
N         = 此类任务的总尝试次数，n_a = 该 Agent 在此类任务上的尝试次数
```

- 得分明细随 `node_assigned` 事件发出，界面上可以看到"为什么选它"。
- 成本项：所有候选都有实测成本之前，用模型档位的标价（输入 + 输出单价）比较相对成本。
- 升级到"换 Agent"时，调度器会把当前 Agent 排除在候选之外（阶段 4）。

## 8. 自我循环

每个模块的修正流程：

```
attempt = 1
loop:
    verify(本模块) → 通过：结束
    记录签名；与历史最好的尝试比较得分，变差则下一轮从最好的尝试开始（rollback 事件）
    if attempt ≥ max_attempts_per_module: 失败
    if 同一签名连续出现 ≥ repeat_threshold 次 或 本级已修正 ≥ rounds_before_escalation 轮:
        升级到下一级
    else:
        诊断 → 按 blame 路由给 coder 或 tester
    attempt += 1
```

- **尝试得分** = 通过的客观层数（0–5）+ L5 测试通过比例。用于判断变好还是变差。
- **升级阶梯**（每升一级就发 `escalated` 事件）：
  1. 检索知识库，把相关条目加入编码上下文；
  2. 换一个 Agent，通常从 fast 档换到 strong 档，并且**从头重写**：新 Agent 只拿需求、接口和"之前失败要点"的摘要，不接着改旧代码（DECISIONS 第 32 条）；
  3. 回到规划层，修改该模块的接口或拆分方式，受影响的下游模块重新执行；
  4. 转人工（评测模式下记为失败）。
- 升级细节（DECISIONS 第 50–52 条）：
  - 第 1 级检索到的知识库条目放进之后所有的编码上下文，本轮照常诊断和修改；
  - 第 2 级用调度器在排除当前编码 Agent 后的候选里挑一个；没有可换的就照常修改；
  - 第 3 级每次运行最多 1 次：规划 Agent 拿到当前规划和失败要点，输出新规划；已完成或正在进行的其他模块必须原样保留，否则带着问题重做（最多 2 次），仍不合格就直接到第 4 级；接替的模块从第 3 级开始，再修不好直接转人工；
  - 第 4 级只在 `--human-review` 时在终端询问负责人，输入的提示交给编码 Agent 再改；没有人工或直接回车就放弃该模块。
- **基线差异**：B1 = 0 轮修正；B2 = 只有修正，没有诊断 Agent，直接把报错交给编码者，也没有升级。

## 9. 经验库与知识库

- **检索**：用标准库自己实现 BM25（`memory/bm25.py`），中文按单字和双字切分，英文按单词切分。DeepSeek 不提供 embedding 接口，而且数据量小，关键词检索就够用。以后要换向量检索，只需要替换这一个模块。
- **经验写入**：某次交给编码 Agent 的修正让失败层前进了或者直接通过时，由诊断 Agent 总结成一条经验：`{category, layer, pattern(失败的 key), fix(≤3 行)}`。pattern 直接取判定器给出的失败 key，不由 LLM 编写。写入前先检索：同类别、同层、pattern 的归一化 BM25 相似度（查询得分 ÷ 文档对自己的得分）≥ 0.8 就合并（`uses` 加 1），不新增。模块最终通过时，用到的经验 `helped` 加 1。
- **经验使用**：编码 Agent 首次编码时，用"spec 关键词 + 任务类别"检索 top-3；修正轮用"失败的 keys"检索 top-3。结果放进提示词里的"过往经验"一节，同时发 `experience_used` 事件。
- **消融开关**：配置 `full-no-exp` 关闭经验库（不检索也不写入），`full-no-esc` 关闭升级阶梯，`full-no-review` 关闭审查。
- **经验库存在哪**：默认就在运行用的 SQLite 里（评测时每个实验一个数据库，同一实验里前面任务的经验后面能用上）；`eval --memory <db>` 改用另一个数据库里的经验库。
- **知识库**：`kb/*.md` 按二级标题切块，用同一套 BM25。内容是自己写的摘要，例如编码规则、Modbus RTU 帧格式要点、STM32F103 存储容量等。full 系列配置在规划时检索 top-3 给规划 Agent，并在升级第 1 级使用；b3、b4 不用知识库。

## 10. 评测

```
firmforge eval --configs b1,b2,b3,b4,full --tasks all --runs 3 --out results/<实验名>/
```

| 配置 | 内容 |
|---|---|
| `b1` | 单 Agent（coder-strong）一次生成，不修正 |
| `b2` | 单 Agent + 修正循环（报错直接回喂，最多 3 轮） |
| `b3` | 多 Agent，`serial` 调度，固定用 coder-strong，无审查、无升级、无经验库 |
| `b4` | 多 Agent，`round_robin` 调度，无审查、无升级、无经验库 |
| `full` | 多 Agent，`smart` 调度，升级 + 经验库 + 审查 |
| `full-no-exp` / `full-no-esc` / `full-no-review` | 消融实验 |
| `b5` | 同等花费对照（DECISIONS 第 30 条）：单 Agent（coder-strong）每次从头生成、不带报错，直到通过或用完 token 预算；预算 = 同一实验中 `full` 在该任务上的平均 token。因此 `b5` 必须和 `full` 一起跑，且排在 `full` 之后 |

- **判定标准**：最终是否通过 = 交付代码通过 L1–L4 和**隐藏测试**。Agent 自己写的测试通过不算数。
- **`results.csv`**：每个"任务 × 配置 × 重复"一行，包含这些列：task、category、difficulty、config、run、first_pass、final_pass、attempts、tokens、cost、wall_s、flash、ram、coverage_pct、failed_layer、escalations、experiences_used。
- **`summary.md`**：各配置的均值 ± 标准差，按任务类别分别统计；另给出"每通过一题的平均 token 和成本"（总 token ÷ 通过题数），用于按同等花费比较（DECISIONS 第 30 条）。
- **复现条件**：固定 temperature 和种子；`run.json` 记录模型型号。
- **开跑前估算成本**：`--estimate` 只跑 1 个任务、1 次，然后按规模推算总 token 和成本。

## 11. 命令行

```
firmforge doctor                         工具链和 API 配置检查
firmforge models                         列出 DeepSeek 可用型号
firmforge tasks                          列出评测任务
firmforge check-task <dir>… | --all      任务自检
firmforge verify <workdir> --task <dir> [--hidden] [--json out.json]
firmforge run (--task <dir> | --spec spec.md --include <dir>) [--config full] [--human-review]
firmforge eval …                         见 §10
firmforge replay <run_dir> [--speed 4]   在终端回放一次运行
firmforge mcp                            以 stdio 方式启动 MCP 服务（可选）
```

- 退出码：0 = 通过，1 = 失败，2 = 不完整，3 = 用法或配置错误。
- 输出用 `[PASS]`、`[FAIL]`、`[SKIP]` 这类 ASCII 标记，避免 Windows 控制台编码问题。

## 12. 桌面软件（PySide6）

按界面草图第 4 版实现（DECISIONS 第 62 条，`tasks_inbox/gui_sketch.md`），代码在 `src/firmforge/gui/`：

- **四个工作区**（左侧竖栏）：
  - 对话：会话列表、和 Agent 团队的对话、模块图 / 流程图、详情（检查结果、代码、调度得分、发给 AI 的内容）；
  - 工作流设计器：照 Qt Designer 的停靠面板（步骤箱、画布、步骤树、属性、代码与检查），画布和 `workflows/*.toml` 双向同步；拖拽照 Node-RED、n8n：步骤箱拖进画布、靠近连线或步骤自动吸附、拖离断线、悬停连线点 × 断开，没连上的步骤不能保存或运行（DECISIONS 第 102 条）；
  - Agent 团队：改 `agents/*.toml`（档位、思考强度、工具集、记忆、技能），保存成新版本；
  - 设置：写回 `firmforge.toml` 和 `.env`（`config_edit.py`，按行修改、保留注释，密钥只写不读）。
- **对话**：需求分析 Agent（`chat.py`）追问并整理需求；规划确认、工作流里的"等人确认"、转人工、外部工具调用确认都以消息和按钮出现在对话里，不再弹窗。
- **任务单**：发给任何 Agent 的用户消息都是"意图、当前位置、引用、约束、附件"五节（`agents/brief.py`）；画布上的步骤以 TOML 出现在"引用"里。
- **后端模式**：
  - `docker`（默认）：`docker run -i … firmforge run …`，`PYTHONPATH` 指向挂载的源码，运行目录挂载到本机；界面每 0.2 秒读取新增的 `events.jsonl` 行；人工回复当作终端输入写进容器的标准输入。Windows 上依赖 Docker Desktop。
  - `local`：在本机后台线程里直接调用核心库（需要本机有工具链），人工回复通过线程事件传回。
- **历史运行**：打开任意运行目录直接显示完整记录和最终状态（DECISIONS 第 106 条）；"从头回放"按时间顺序重放事件，倍速可调，单个间隔最多等 3 秒。实时显示、打开历史和回放共用一套事件处理代码。
- **样式**：浅色简约、圆角分档、松石绿唯一强调色，跟随系统深浅色；相邻面板之间是可拖动的分隔条，关闭时用 `QSettings` 记住布局。
- **嵌入式新流程**（DECISIONS 第 100 条，`tasks_inbox/gui_sketch_board.md`）：左侧竖栏加"硬件""评测"，共六个工作区。
  - 对话页的"流程"选"上板"：写一句需求跑完整流程，或选一道上板评测题按某种配置跑一次（最后用隐藏测试判定）；模块卡片三段进度（合规、编译、测试）；详情区有"上板测试"（从每次尝试的 `serial.log` 解析出用例，没通过的展开原因，下面是串口原文）和"用到的规则"；底部状态栏显示板子、串口、ESP-IDF、规则库条数。
  - 知识库页（DECISIONS 第 114、115 条，替换原来的硬件页）：`grounding/catalog.py` 把各板子目录里的资料、规则、手册和实测对不上的地方排成固定目录——芯片 → 开发框架（ESP-IDF，按外设）→ 外接器件（按类别）→ 开发板；中间是这一格的说明（芯片能力读自 ESP-IDF 的 soc_caps.h、板子的硬件档案、器件参数）和条目，右边是依据（引用原文、手册原页、核对经过）。能搜索、能驳回已在用但错了的规则；新规则的确认在对话里做（`gui/rule_review.py`）。
  - 要上板的运行一律用本机后端（`EmbeddedBackend`），不进 docker：Docker Desktop 在 Windows 上不能把 USB 板子交给容器。
  - 命令行和界面共用 `embedded/workspace.py` 读硬件目录、建流程、建评测上下文、建规则流程。
  - 工程图和接线图（DECISIONS 第 109、113 条）：`embedded/project_map.py` 从运行目录算数据（design.json、wiring.json、每个模块最后交的代码、串口日志和电脑测试报告），`gui/project_map_view.py` 画工程图（ProjectMapView）和接线图（WiringView），对话页上板流程时用"工程图 / 接线图 / 流程图"开关切换，事件来了合并成一次重画，"展开"后积木列出接口、连线写出用到的函数。
  - 亮点展示（DECISIONS 第 105 条）：对话页"过程"标签按已经发生的事件画修正时间线（`board_view.repair_timeline`），点某次尝试切换下面各标签；知识库和对话里的规则卡片"看手册原页"打开原页窗口（`page_viewer.py`，图片和单词位置来自 `grounding/pagepic.py` 调 poppler），"手册和板子对不上"对照卡读 `hardware/<板子>/conflicts.json`（`grounding/conflicts.py` 加载时核对原句和实测记录）。

## 13. 工具与 MCP（DECISIONS 第 63 条）

框架和每个设计点的出处见 `tasks_inbox/tools_mcp_framework.md`，代码在 `src/firmforge/tools/` 和 `src/firmforge/mcp/`：

- **工具箱**（`tools/registry.py`）：内置工具和外部 MCP 工具登记在一起，按工具集分组；发给模型的只有名字、说明、参数格式。Agent 定义里的 `toolsets` 决定它能用哪些。
- **执行流水线**（`tools/pipeline.py`）：记录调用 → 检查（能不能用、参数、要不要问人）→ 守卫（只拒不放，不许碰隐藏测试）→ 执行（超时）→ 整理（截断、失败如实返回）→ 记录结果。
- **调用循环**（`tools/loop.py`）：一次生成最多 5 轮工具往返；带了工具的请求之后，按 DeepSeek 要求回传 `reasoning_content`。
- **MCP 服务端**（`firmforge mcp`，stdio，JSON-RPC 2.0）：同时支持 2026-07-28（每个请求自带版本，`server/discover`）和 2025-11-25（`initialize`）；工具 `list_tasks`、`verify_c_module`（不跑隐藏测试）、`kb_search`。
- **MCP 客户端**：启动本机外部服务，先 `server/discover` 试探、不行退回 `initialize`；外部工具名为 `mcp__<服务名>__<工具名>`；清掉名字像密钥的环境变量；断线重连最多 5 次；默认每次调用前问人；评测时不用。

## 14. 部署

- **Dockerfile**：
  - 基础镜像 `ubuntu:24.04`；
  - apt 安装 `gcc libc6-dev gcc-arm-none-eabi libnewlib-arm-none-eabi binutils-arm-none-eabi cppcheck python3 python3-pip git ca-certificates`；
  - 环境变量 `PYTHONUTF8=1`、`ASAN_OPTIONS=detect_leaks=0`；
  - 执行 `pip install --break-system-packages -e .`；
  - `ENTRYPOINT ["firmforge"]`。
- **在容器里调用 LLM**：用 `--env-file .env` 传入 API key。
- **运行环境**（DECISIONS 第 101 条）：只在 Linux（Ubuntu 24.04）上开发和测试，不做 Windows 实测和打包；桌面软件用 `pip install -e .[gui,sources]` 安装后在本机运行。运行环境、依赖包、部署步骤写在 README 的"运行环境与部署"一节。
