# 工具与 MCP 框架（第 2 版，待负责人审核）

起草：Claude Code，2026-10-26。第 1 版是我自己设计的；负责人要求先查同类应用的框架再定。这一版**每个设计点都标明借鉴自谁**，没有出处的标 ★（我定的）。

---

## 0. 一句话

**给 Agent 装上"手"（工具）。手的接口用业界标准：模型这边用 Function Calling，工具这边用 MCP。这样 Agent 能自己查资料、试编译；别人做的工具能接进来；我们的判定器也能接到别人的 AI 软件里去。**

## 1. 三个词（打比方）

| 词 | 比方 | 在我们这里 |
|---|---|---|
| 工具 | Agent 的手 | 一个能被调用的功能，如"查知识库""试编译" |
| Function Calling | Agent 说"帮我拿扳手"的**固定说法** | DeepSeek 接口规定的格式：模型回"调用 X、参数 Y"，平台执行后把结果放回对话 |
| MCP | 工具界的 **USB-C 插座标准** | 按标准做的工具，任何支持 MCP 的 AI 软件都能接。我们两头都做：接别人的工具（客户端），也让别人接我们（服务端） |

## 2. 查了哪些同类框架（一手资料）

| 框架 | 资料 | 和我们相关的做法 |
|---|---|---|
| **DeepSeek Harness**（DeepSeek 官方开源） | 仓库 docs/architecture、docs/subsystems/tools、docs/tool-execution-pipeline、packages/mcp/mcp-client 的说明文档 | ① 工具定义分两半：发给模型的只有"名字、说明、参数格式"，超时、能否并行、界面怎么显示这些**只留在平台内部，绝不发给模型**；② 每次调用走一条固定的**流水线**：先记录调用 → 调用前检查（权限、审批、沙箱）→ 守卫（只能拒绝、不能放行）→ 执行（套超时）→ 调用后处理（改写、截断）→ 记录结果；③ 工具出错时明确告诉模型失败，**不给模型假的成功**；④ 原则"**模型看到的都要记录在案**"，任何一次请求都能从日志重建；⑤ 标明"可以并行"的工具才能同时执行；⑥ MCP 工具命名 `mcp__服务名__工具名`，发给 MCP 服务的永远是原始工具名；⑦ 工具列表**整批更新**，失败就保留上一批，不会只剩一半；⑧ 断线重连：0.5 秒起逐次翻倍、最长 30 秒、最多 10 次；单次调用默认超时 60 秒；⑨ 启动外部服务前，**清掉名字像密钥的环境变量** |
| **Claude Code**（Anthropic） | 官方文档 code.claude.com/docs/en/mcp | ① 配置格式 `mcpServers: {名字: {command, args, env, timeout}}`，支持 `${变量}` 和 `${变量:-默认值}`；② 工具输出超过 1 万 token 警告、默认上限 2.5 万 token；③ 有启动超时和单次调用超时；④ 断线重连最多 5 次、1 秒起指数退避；⑤ 项目里自带的 MCP 服务，**第一次用要人点头**；⑥ 远程配置里引用密钥类变量一律读成空，防止项目配置偷密钥 |
| **OpenAI Agents SDK** | 官方文档 openai.github.io/openai-agents-python/mcp | ① 每个服务可以设**允许名单 / 禁止名单**过滤工具；② 工具列表可以缓存，不用每次都问；③ 审批可以按工具设：总是问 / 从不问；④ 名字可以带服务名前缀，避免和自带工具重名；⑤ 工具调用全程留痕（tracing） |
| **Hermes Agent**（Nous Research） | 官方文档 user-guide/features/tools、configuration | ① 工具按"**工具集**"分组开关（web、terminal、file、memory 等），MCP 服务自动成为一个工具集；② 可以按场景选启用哪些工具集；③ 配置文件放普通设置，密钥单独放 `.env` |
| **MCP 规范 2026-07-28** | modelcontextprotocol.io/specification/2026-07-28：basic、versioning | ① 新版**无握手**，每个请求自带版本号；② 服务端必须实现 `server/discover`；③ "**双版本**"实现：客户端先用 `server/discover` 试探，收到看不懂的错误或超时就退回旧版的 `initialize` 握手；服务端看对方怎么开口决定用哪一版；④ 版本不支持时回错误码 -32022 并列出支持的版本；⑤ 调用工具前宿主要取得用户同意 |
| **DeepSeek 接口文档** | 思考模式页 | 思考模式支持工具调用；带了工具的请求，**之后每次都要把 `reasoning_content` 完整传回**，否则报 400 |

## 3. 我们的框架：5 个部件

```
          ┌──────────────────────── FirmForge ────────────────────────┐
          │                                                            │
 DeepSeek ◄┼─► ① 调用循环 ──► ② 工具箱（登记 + 过滤）                  │
          │     多轮往返        ├─ 工具集 knowledge：kb_search          │
          │     回传思考内容     ├─ 工具集 build：compile_check          │
          │        │            ├─ 工具集 interface：list_interface     │
          │        ▼            └─ 工具集 mcp-<服务名>：外部工具 ◄─┐     │
          │   ③ 执行流水线                                        │     │
          │   记录调用 → 检查 → 守卫 → 执行 → 整理 → 记录结果      │     │
          │                                                      │     │
          │   ④ MCP 客户端 ─────────────────────────────────────┘─────┼──► 外部 MCP 服务
          │                                                            │
          │   ⑤ MCP 服务端（firmforge mcp） ◄──────────────────────────┼─── Claude Code 等
          └────────────────────────────────────────────────────────────┘
```

### ① 调用循环（和模型来回说话）

| 规则 | 借鉴 |
|---|---|
| 把这个 Agent 能用的工具清单（只含名字、说明、参数格式）随请求发出 | DeepSeek Harness ① |
| 模型回"要调用"→ 交给执行流水线 → 把**模型这一轮的完整回复（含思考内容）**和工具结果放回对话 → 再请求 | DeepSeek 接口文档 |
| 模型一次要调几个工具时，标明"可以并行"的同时执行，其余按顺序 | DeepSeek Harness ⑤ |
| ★ 一次生成最多 5 轮工具往返；用完后最后一次请求不带工具，要求直接交卷 | 防止无限调用；Claude Code 等用户可中断，我们是无人值守，需要硬上限 |

### ② 工具箱（登记、分组、过滤）

| 规则 | 借鉴 |
|---|---|
| 内置工具和外部 MCP 工具登记在同一个工具箱，Agent 看到的是一张统一清单 | OpenAI Agents SDK ④、DeepSeek Harness |
| 工具按**工具集**分组：`knowledge`、`build`、`interface`，每个外部 MCP 服务自动成为 `mcp-<服务名>` | Hermes ① |
| Agent 定义里写能用哪些工具集（也可以细到单个工具）；外部服务可以设允许 / 禁止名单 | Hermes ②、OpenAI Agents SDK ① |
| 外部工具的名字 `mcp__服务名__工具名`；服务名只许字母数字、下划线、横线，最长 32 个字符；转名字时有损的就加一段哈希，保证不撞名 | DeepSeek Harness ⑥、Claude Code |
| 外部工具列表整批更新，失败保留上一批 | DeepSeek Harness ⑦ |

### ③ 执行流水线（每次调用都走这 6 步，所有规矩集中在这里）

| 步 | 做什么 | 借鉴 |
|---|---|---|
| 1 记录调用 | 先写一条 `tool_called` 事件和完整参数，再执行（崩溃了也查得到） | DeepSeek Harness ②④ |
| 2 调用前检查 | 这个 Agent 能用这个工具吗？参数符合格式吗？需要问人吗（外部工具默认要问；评测模式一律不问、外部工具直接禁用） | DeepSeek Harness ②、OpenAI Agents SDK ③、MCP 规范 ⑤ |
| 3 守卫 | **只能拒绝、不能放行**的硬规则：任何路径指向 `hidden_tests/` 一律拒绝 | DeepSeek Harness ②（守卫只拒不放） |
| 4 执行 | 套超时：内置 `compile_check` 60 秒，外部工具 60 秒 | DeepSeek Harness ⑧ |
| 5 整理结果 | 太长就截断：★ 每次最多 8000 个字符（约 3000 token，远低于 Claude Code 的 2.5 万 token 上限，因为我们一次生成可能调多次），完整内容另存到运行目录；出错就标"失败"并附原因，绝不伪装成功 | Claude Code ②、DeepSeek Harness ③ |
| 6 记录结果 | 写 `tool_result` 事件（是否成功、耗时、摘要），界面据此显示一行记录 | DeepSeek Harness ④ |

### ④ MCP 客户端（接别人的工具）

| 规则 | 借鉴 |
|---|---|
| 配置格式和 Claude Code 一样（名字、command、args、env、timeout），写在 `firmforge.toml` 的 `[mcp.servers.<名字>]` 下；支持 `${变量}`、`${变量:-默认值}` | Claude Code ① |
| ★ 只做本机子进程（stdio）；远程 HTTP 不做（要处理登录授权，时间不够） | — |
| **双版本**：先发 `server/discover`，回得上来就用 2026-07-28；回了看不懂的错误或 5 秒没回应，就退回 `initialize` 握手（2025-11-25） | MCP 规范 ③ |
| 启动外部服务时清掉名字像密钥的环境变量（含 KEY、TOKEN、SECRET、PASSWORD），`env` 里明确写的才传 | DeepSeek Harness ⑨、Claude Code ⑥ |
| 启动超时 ★ 10 秒；断线重连 1 秒起翻倍、最多 5 次（取 Claude Code 的做法，比 DeepSeek Harness 的 10 次保守）；重连失败就撤掉它的工具并在界面提示 | Claude Code ④、DeepSeek Harness ⑧ |
| 新加的外部服务**第一次启用要你在界面上点确认** | Claude Code ⑤ |
| 首批只接一个官方参考服务器 `fetch`（抓网页转文字）做演示，默认关闭 | ★ |

### ⑤ MCP 服务端（`firmforge mcp`，让别人接我们）

| 规则 | 借鉴 |
|---|---|
| stdio，一行一条 JSON-RPC 2.0 消息；日志只写标准错误 | MCP 规范 |
| **双版本**：收到带版本的请求按 2026-07-28 无状态处理，必须实现 `server/discover`；收到 `initialize` 就按 2025-11-25 处理；版本不支持回 -32022 并列出支持的版本 | MCP 规范 ②③④ |
| 3 个工具：`list_tasks`、`verify_c_module`（跑第 1–5 层，**不跑隐藏测试**）、`kb_search` | ★ |
| 传进来的文件：只收 `src/`、`tests/` 下的 `.c`、`.h`，路径不能有 `..`，总共不超过 512 KB，写临时目录、用完删除 | ★ |
| 设置页给出现成配置片段，复制进 Claude Code 就能用 | Claude Code 的配置格式 |

## 4. 内置工具

| 工具集 | 工具 | 参数 → 返回 | 为什么需要 | 能并行 |
|---|---|---|---|---|
| knowledge | `kb_search` | 一句话 → 知识库最相关 3 段（注明出处） | 现在只有升级第 1 级才查知识库；有了工具，Agent 随时能查 | 能 |
| build | `compile_check` | 文件内容 → 主机编译 + 交叉编译的结果和报错原文 | 交卷前自己发现语法错误，少浪费一整轮判定和修正 | 能（各用各的临时目录） |
| interface | `list_interface` | 无 → 本模块要实现的函数、依赖模块提供的函数、公共头文件 | 并行写代码最常见的错误是接口对不上（DECISIONS 第 33 条） | 能 |

## 5. 谁能用什么

写在 `agents/*.toml`，界面"Agent 团队"里勾选改的就是它：

```toml
name = "coder-fast"
skills = ["按需求写嵌入式 C 模块", "按诊断修改代码"]
toolsets = ["knowledge", "build", "interface"]
memory = { experience = true, knowledge = true }
```

| Agent | ★ 默认工具集 | 理由 |
|---|---|---|
| 编码 | knowledge、build、interface | 最需要查资料、试编译、对接口 |
| 测试 | knowledge、interface | **不给 build**：测试 Agent 不该看到实现，编译测试又离不开实现 |
| 诊断、规划 | knowledge | 判断原因、拆模块时参考资料 |
| 审查 | 无 | 只提意见 |
| 需求分析（若采用） | knowledge | 追问需求时参考领域知识 |

外部 MCP 工具集默认不给任何 Agent，要你在"Agent 团队"里手动勾。

## 6. 公平和评测

| 规则 | 理由 |
|---|---|
| ★ 单 Agent 基线（b1、b2、b5）和多 Agent 拿到同样的内置工具 | 差别仍只在分工，对比才干净 |
| ★ 新增消融配置 `full-no-tools` | 单独量出工具本身带来多少提升 |
| 评测模式下外部 MCP 工具一律禁用 | 结果可复现 |
| 评测汇总新增：平均每题调用工具次数、工具耗时占比；`firmforge stats` 按工具统计 | OpenAI Agents SDK ⑤ 的留痕思路 |

## 7. 代码放在哪（核心库，只用标准库）

| 文件 | 部件 |
|---|---|
| `src/firmforge/tools/registry.py` | ② 工具箱：登记、工具集、过滤、命名 |
| `src/firmforge/tools/pipeline.py` | ③ 执行流水线 |
| `src/firmforge/tools/loop.py` | ① 调用循环 |
| `src/firmforge/tools/builtin.py` | 3 个内置工具 |
| `src/firmforge/mcp/protocol.py` | JSON-RPC、双版本处理（两端共用） |
| `src/firmforge/mcp/client.py` | ④ MCP 客户端 |
| `src/firmforge/mcp/server.py` | ⑤ MCP 服务端 |
| `src/firmforge/llm/fake.py`（改） | 假模型支持预设的工具调用 |

DeepSeek Harness 和 OpenAI Agents SDK 都用官方 MCP SDK；我们按 CLAUDE.md 规定核心库只用标准库，所以自己实现 JSON-RPC 和双版本处理（stdio 部分不复杂）。

## 8. 怎么验证（单元测试，全用假模型）

1. 调一次工具后交卷；连续多轮；第 5 轮后不再给工具。
2. 思考内容被回传（第二次请求里带着第一次的 `reasoning_content`）。
3. 工具出错 / 超时 / 参数不合法：模型收到"失败"及原因，运行不中断。
4. Agent 调不在自己清单里的工具：被拒绝。
5. 守卫：任何指向 `hidden_tests/` 的调用都被拒绝。
6. 可以并行的工具同时执行，不能并行的按顺序。
7. 结果截断到 8000 字，完整内容存在运行目录。
8. MCP 服务端：新版（带版本的请求 + `server/discover`）和旧版（`initialize`）各走一遍；不支持的版本回 -32022。
9. MCP 客户端：接我们自己的服务端，分别模拟新版和旧版服务，都能发现工具并调用；断线重连；清密钥环境变量。
10. 评测模式下外部工具被禁用。

## 9. 开发顺序

| 顺序 | 内容 | 依赖 |
|---|---|---|
| 1 | 工具箱 + 执行流水线 + 调用循环 + 假模型支持工具 | 无 |
| 2 | 3 个内置工具 + Agent 定义加 `toolsets`、`skills`、`memory` + `firmforge agents` | 1 |
| 3 | MCP 协议 + 服务端 | 无（可与 1 并行） |
| 4 | MCP 客户端 + 接 fetch 演示 | 1、3 |
| 5 | `full-no-tools`、统计 | 2 |
| 6 | 你用真模型试跑 1–2 道题，看 Agent 会不会用工具 | 2 |

## 10. 需要你拍板的 ★

1. 一次生成最多 5 轮工具往返。
2. 每次工具结果最多 8000 字，完整内容另存。
3. 默认工具集分配（第 5 节），测试 Agent 不给 build。
4. 单 Agent 基线也给同样的内置工具 + 新增 `full-no-tools`。
5. MCP 只做本机 stdio、首批只接 fetch；启动超时 10 秒、重连最多 5 次。
6. MCP 服务端的 3 个工具和文件限制。
