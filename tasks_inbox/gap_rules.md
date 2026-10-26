# 补缺口规则草稿（待负责人审核）

起草：Claude Code，2026-10-26。对应 [requirements_gap.md](requirements_gap.md) 里的 3 个缺口，负责人已同意"按推荐补"。格式同前几批：**什么情况 → 应该怎样**，★ 是我定的。

## 查过的资料

| 资料 | 用到的结论 |
|---|---|
| DeepSeek 官方文档《思考模式》（api-docs.deepseek.com/zh-cn/guides/thinking_mode） | "思考模式支持工具调用功能"。带了 `tools` 的请求，**之后所有请求都必须完整回传 `reasoning_content`**，否则返回 400 错误；思考模式下 temperature 不生效 |
| MCP 规范最新版 2026-07-28（modelcontextprotocol.io/specification/2026-07-28/basic） | 基于 JSON-RPC 2.0；**这一版改成无状态**：每个请求在 `_meta` 里自带协议版本和客户端能力，服务端不能依赖之前的请求；结果要带 `resultType`。规范要求兼容旧版本客户端 |
| MCP 2025-11-25 及以前 | 旧版本要先 `initialize` 握手。Claude Code 等现有客户端可能还在用旧版本 |

## 一、二、工具集成（Function Calling 与 MCP）

已展开成单独的框架文档 [tools_mcp_framework.md](tools_mcp_framework.md)，以那份为准。

## 三、Agent 定义补"技能、工具集、记忆能力"

| 情况 | 应该怎样 |
|---|---|
| 新增的字段 | `skills`：一句话列表，写这个 Agent 擅长什么（给人看，也给规划和调度参考）；<br>`tools`：能用的工具名列表（第一节的 3 个）；<br>`memory`：`experience = true/false`（用不用经验库）、`knowledge = true/false`（用不用知识库） |
| 这些字段起不起实际作用 | **起**：`tools` 决定生成时给哪些工具；`memory` 决定上下文里放不放经验和知识库条目（取代现在按配置写死的做法）。不是只给人看的装饰 |
| 写了不存在的工具名 | 加载时报错，指出哪个文件哪一行 |
| 怎么"发现" Agent | 新命令 `firmforge agents`：列出每个 Agent 的名称、角色、全部版本、档位、技能、工具、记忆、历史表现（尝试次数、成功率、平均花费）；`--role coder` 只看某个角色 |
| 新增的 analyst（需求分析）Agent | 只在界面方案选 B 时加（见 gui_sketch.md 第七节第 1 问）；它的工具只有 `kb_search` |

## 四、性能和压力测试

| 情况 | 应该怎样 |
|---|---|
| 测什么 | 决赛要求的 3 项：并发调度能力、响应延迟、任务成功率，并和"串行调度""单 Agent"对比 |
| 并发调度能力怎么测（不花钱） | 新命令 `firmforge bench`：用假模型（★ 每次调用固定等 2 秒，模拟网络延迟），造 1、2、4、8 个互不依赖的模块；分别用串行（b3）和智能并行（full），LLM 并发上限取 1、2、4、8。<br>记录：总用时、每分钟完成的模块数、同时在干活的 Agent 最多几个、调度器自己花的时间 |
| 判定器也要测吗 | ★ 要：同一批模块，判定并发 1、2、4，看编译和测试的吞吐量（判定器在 Docker 里真跑） |
| 响应延迟怎么测 | 新命令 `firmforge stats <运行目录或评测目录>`：从真实运行记录里统计每个 Agent、每个模型的调用延迟（中位数、90% 分位、最大值）、token、判定耗时 |
| 成功率对比 | 阶段 5 正式评测加跑 b3（串行），只在能拆成多个模块的题上跑（DECISIONS 第 49 条原定的做法） |
| 结果放哪 | `results/bench/`：CSV + 一张 Markdown 表，方案书直接引用 |
