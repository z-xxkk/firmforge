# 工作流设计器元素方案 第 1 版（2026-11-01）

依据 DECISIONS 第 70 条（自由画布 + 领域元素 + 运行时进度）。先调研同类产品，再定元素；每条设计注明借鉴来源，★ 表示没有直接来源、是我自己提出的。

## 一、调研摘要

| 产品 | 值得借鉴的做法 | 来源 |
|---|---|---|
| Dify | If / Elif / Else 条件，用下拉选运算符（包含、等于、大于、为空…），多个条件用"且 / 或"组合，不写代码；变量有类型（字符串、数字、对象、布尔、数组、文件）；循环有"结束条件 + 最大次数 + 退出循环节点"三种停法；LLM、HTTP、代码、工具节点可设出错处理：无（停止）/ 默认值 / **出错分支（画布上用橙色显示）**，出错分支能拿到错误类型和错误信息 | docs.dify.ai 的 If-Else、Iteration、Loop、Handle Errors 页 |
| n8n | 核心节点按用途分组：分流（IF、Switch）、合并、循环、等待、子流程、错误处理；每个节点可设"失败重试"和"出错时：停止 / 继续 / 走错误输出"；可以为整个流程指定一个"错误流程"，任何一步失败都会触发它 | docs.n8n.io 的 flow-logic、error-handling、nodes 页 |
| Node-RED | **节点下方显示状态**：颜色（红、绿、黄、蓝、灰）+ 形状（实心、空心）+ 不超过 20 字的说明；Switch 节点的每个出口标着匹配的规则，路线一眼可见；Catch 节点统一接住某一组节点的错误；连线不带类型 | nodered.org 的 handling-errors、status、nodes 页 |
| LangGraph | 整个流程共享一份**有类型定义的状态**；条件边由路由函数决定下一步；每个节点可单独设重试策略（默认最多 3 次、首次间隔 0.5 秒、每次翻倍、上限 128 秒、加随机抖动；有的错误类型不重试），重试用完后交给出错处理函数决定去哪；人工确认时暂停，输入可按定义好的格式校验，Studio 会渲染成表单；Studio 能**从某一步的存档分叉重跑** | docs.langchain.com 的 graph-api、fault-tolerance、interrupts、studio 页 |
| ComfyUI | 每个节点声明输入类型和输出类型（如 IMAGE、MODEL），按类型连接；执行前有校验，不通过就不运行 | docs.comfy.org custom-nodes 页 |
| Unreal 蓝图 | 不同数据类型的引脚用不同颜色区分；"执行线"（控制先后）和"数据线"分开 | Unreal 蓝图变量文档（只核实到颜色表与"必须由执行线调用"） |
| Jenkins Stage View | 每个阶段的进度条**按历史平均耗时**估计还要多久 | plugins.jenkins.io/pipeline-stage-view |
| GitHub Actions、GitLab、Airflow | 只显示状态图标和已用时间，不估计剩余时间 | 各自官方文档 |
| NN/G 进度指示研究 | 超过 10 秒的操作要显示完成度或步数；时间只给大概值（"约 3 分钟"），并留余量；开始时估得保守一点 | nngroup.com/articles/progress-indicators |

未能核实：Coze 的节点文档（页面是前端渲染，没读到内容）；Dify 和 n8n 的重试次数与间隔的具体数值。

## 二、设计原则

1. **画布上只有两类东西：步骤和流程控制**（借鉴 n8n 的分组；蓝图"执行线 / 数据线"分开的思路）。
2. **每个步骤写明需要什么、产出什么，类型对不上不让连**（借鉴 ComfyUI 的输入输出类型、LangGraph 的类型化状态、Dify 的变量类型）。类型就是嵌入式里的"成果物"：需求文档、硬件档案、资料集、规则集、设计、代码、测试、固件、各类报告。
3. **条件用下拉选，不写代码**（借鉴 Dify If-Else）。可以引用的是共享状态里的字段，例如 `硬件档案.芯片系列`、`诊断.责任方`、`板子.已连接`。
4. **每个步骤都能设重试和出错去向，出错线用另一种颜色**（借鉴 LangGraph 重试策略与出错处理、Dify 出错分支、n8n 的出错选项）。
5. **元素粒度 = 一个嵌入式工程师会说出口的一件事** ★。太粗（"写固件"）就没有可调的地方；太细（"调用一次工具"）就变成编程。细节放在元素的属性里调。
6. **核心步骤不能删、顺序不能乱**（DECISIONS 第 66、70 条）：规则核对必须在编码之前，人工确认规则不能关闭；保存前检查，不满足就不让保存。

## 三、元素清单

### 1. 嵌入式步骤（12 个）

| 元素 | 需要 | 产出 | 主要属性 |
|---|---|---|---|
| 需求澄清 | 用户需求 | 需求文档、硬件档案 | Agent、最多追问几轮 |
| 查资料并提炼规则 | 硬件档案（+ 用户资料） | 资料集、规则集（待核对） | Agent、是否联网、资料来源白名单 |
| 规则核对 | 规则集（待核对）、资料集 | 规则集（已核对） | 退回上限（默认 3）；内部 7 步固定，**不可删** |
| 设计分层 | 需求文档、规则集 | 设计（模块、接口、硬件层 / 逻辑层） | Agent |
| 编码 | 设计、规则集 | 代码 | Agent、模型档位、思考强度 |
| 写测试 | 需求文档、规则集、设计 | 测试 | Agent（看不到代码） |
| 编译 | 代码（+ 测试） | 编译报告、固件 | 目标芯片、开发包版本 |
| 合规检查 | 代码、规则集 | 合规报告 | 哪些项由程序判、哪些由 Agent 审 |
| 电脑测试 | 代码、测试 | 测试报告 | 超时 |
| 接线自检 | 硬件档案 | 接线状态 | 自检项（如 I2C 扫描地址） |
| 上板测试 | 固件、测试、接线状态 | 测试报告（含寄存器读数、串口记录） | 串口、超时、复位方式、重复几次（判断是否不稳定） |
| 诊断 | 各类报告 | 诊断（责任方：代码 / 测试 / 规则 / 硬件） | Agent |

另有两个收尾元素：**规则入库**（规则集 → 知识库）、**交付报告**。

### 2. 流程控制（7 个）

| 元素 | 作用 | 借鉴 |
|---|---|---|
| 开始 / 结束 | 流程入口和出口 | n8n、Dify |
| 岔路 | 如果 / 否则如果 / 否则，条件用下拉选，"且 / 或"组合 | Dify If-Else |
| 循环 | 停止条件三选多：结束条件、最大次数、**连续 N 次同一失败就停（没进展）** ★ | Dify Loop |
| 对每个模块 | 规划拆出几个模块，就展开几份子流程，可设同时进行几个 | Dify Iteration、n8n Loop Over Items |
| 并行 | 几条线同时做，全部完成再往下 | n8n Merge |
| 等人确认 | 暂停，弹出表单（通过 / 修改 / 驳回 + 意见），表单格式由元素定义 | LangGraph interrupt + Studio 表单 |
| 子流程 | 调用模板库里的另一张图 | n8n Execute Workflow |

### 3. 三种连线

| 连线 | 样子 | 借鉴 |
|---|---|---|
| 顺序线 | 灰色实线 | — |
| 条件线 | 线上标出条件文字，例如"芯片 = ESP32" | Node-RED Switch 出口标签 |
| 出错线 | 橙色，标出错误类型，例如"烧录失败" | Dify 出错分支 |

另设一个**全局出错去向**：任何步骤没有单独设出错线时，统一走这里（默认：保存现场 → 转人工）。借鉴 n8n 的错误流程和 Node-RED 的 Catch。

## 四、运行时显示

| 显示什么 | 怎么显示 | 借鉴 |
|---|---|---|
| 每个元素的状态 | 元素下方：颜色 + 形状 + 一行字，例如"第 2 轮 · coder-strong · 1 分 12 秒"；状态有等待、进行中、通过、失败、跳过、等你操作 | Node-RED 节点状态 |
| 走过的路线 | 实际走过的线高亮，岔路口标出依据 | Node-RED 出口标签 |
| 整体进度 | 顶部："第 5 / 11 步 · 已用 6 分钟 · 预计还要约 8–20 分钟" | NN/G：显示步数、只给大概时间 |
| 剩余时间怎么算 | 按每类元素的历史耗时（中位数到 90% 分位）加总；循环按"历史平均轮数"估；随着进展收窄范围；等人操作的时间不算，单独提示 | Jenkins 按历史平均估计；NN/G 留余量、开始时保守 |
| 没有历史数据时 | 标"估计依据不足"，只显示步数，不显示时间 | ★ |
| 从某一步重跑 | 点开某一步，可以改它的输入后从这里重跑，其余步骤沿用存档 | LangGraph Studio 分叉重跑 |

## 五、预置模板（用同一套元素画成）

1. 新写外设驱动（完整主线）
2. 移植到另一款芯片（逻辑层复用，硬件层重查规则、重写）
3. 纯逻辑模块（不查芯片资料、不上板）
4. 只更新规则库（查资料 → 核对 → 入库）
5. 没有板子（测试只到电脑测试，结论标"未上板"）

## 六、需要负责人确认

1. "查资料"和"提炼规则"合成一个元素，还是分成两个？合成更简单；分开则可以单独替换资料来源。我建议合成。
2. 12 个步骤元素 + 7 个控制元素 + 3 种连线的规模是否合适？
3. "从某一步重跑"要不要做？它对调试很有用，但工作量不小，我建议放到决赛阶段。

## 来源

- Dify：[节点总览](https://docs.dify.ai/en/guides/workflow/node)、[If-Else](https://docs.dify.ai/en/cloud/use-dify/nodes/ifelse.md)、[Iteration](https://docs.dify.ai/en/cloud/use-dify/nodes/iteration.md)、[Loop](https://docs.dify.ai/en/cloud/use-dify/nodes/loop.md)、[Handle Errors](https://docs.dify.ai/en/cloud/use-dify/build/predefined-error-handling-logic.md)、[Variable Aggregator](https://docs.dify.ai/en/cloud/use-dify/nodes/variable-aggregator.md)
- n8n：[Flow logic](https://docs.n8n.io/flow-logic/)、[Error handling](https://docs.n8n.io/flow-logic/error-handling/)、[Nodes](https://docs.n8n.io/workflows/components/nodes/)、[Loop](https://docs.n8n.io/build/flow-logic/loop.md)
- Node-RED：[Handling errors](https://nodered.org/docs/user-guide/handling-errors)、[Node status](https://nodered.org/docs/creating-nodes/status)、[Nodes](https://nodered.org/docs/user-guide/editor/workspace/nodes)
- LangGraph：[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、[Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance.md)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts.md)、[Studio](https://docs.langchain.com/langsmith/use-studio.md)
- [ComfyUI 自定义节点](https://docs.comfy.org/custom-nodes/walkthrough)；[Unreal 蓝图变量](https://dev.epicgames.com/documentation/en-us/unreal-engine/blueprint-variables-in-unreal-engine)
- [Jenkins Pipeline Stage View](https://plugins.jenkins.io/pipeline-stage-view/)；[GitHub Actions 可视化图](https://docs.github.com/en/actions/monitoring-and-troubleshooting-workflows/monitoring-workflows/using-the-visualization-graph)；[Airflow UI](https://airflow.apache.org/docs/apache-airflow/stable/ui.html)
- [NN/G：Progress Indicators](https://www.nngroup.com/articles/progress-indicators/)
