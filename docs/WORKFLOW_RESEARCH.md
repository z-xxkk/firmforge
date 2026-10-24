# 工作流设计依据调研（2026-10-24）

负责人提出：Agent 的工作流步骤当初是直接设计的，应该先查资料再确定。本文把 ARCHITECTURE 里的每一步，和已发表的研究、工程实践逐条对照。

判断分为四类：
- **有依据**：有研究或权威实践直接支持；
- **有依据，需调整**：方向对，但资料提示要改细节；
- **我的选择**：没找到直接依据；
- **新发现**：资料里提到、我们原设计没考虑到的问题。

说明：多数论文是根据摘要和二手解读核对的，没有逐篇读完全文；引用的数字以原文为准。

---

## 一、先说最重要的一条新发现

**多 Agent 不一定比单 Agent 强。** UC Berkeley 等团队分析了 7 个主流多 Agent 框架的 1600 多条运行记录，发现多 Agent 系统相对单 Agent 的提升"往往很小"，失败主要来自系统设计问题和 Agent 之间配合不好，而不只是模型能力不足[1]。

这对我们有两个直接影响：
1. 答辩不能只说"我们用了多 Agent"，必须**用数据证明**它比单 Agent 好在哪。这正是我们设计 B1–B4 基线和消融实验的原因，研究支持这么做；
2. 对比要**按同等花费比**。研究指出，只比"通过率"对多轮修正的方法不公平，因为它多花了 token；应该把通过率和总 token 放在一起比[4][5]。我们现在的成绩表只有通过率均值，需要补上（见建议 R1）。

---

## 二、逐步对照

| # | 工作流步骤（ARCHITECTURE） | 判断 | 依据 |
|---|---|---|---|
| W1 | **规划 Agent 把需求拆成模块依赖图（DAG）**，按依赖顺序执行 | 有依据 | Parsel 把任务分层拆成函数、按依赖从叶子往上实现，在 APPS 竞赛题上通过率比直接生成高 75% 以上[6]；微软 CodePlan 把仓库级修改建模为"按依赖分析做规划"[7]；MetaGPT 按软件公司的标准流程分角色传递文档[8] |
| W2 | **写代码和写测试并行，写测试的 Agent 看不到实现** | 有依据 | AgentCoder 的核心设计：测试设计者只根据需求写测试。理由是在同一段对话里紧接着代码写测试，会被代码"带偏"，失去客观性和多样性[9] |
| W3 | **用编译器、测试等客观判定器检查，而不是让模型自己判断** | 有依据（最强） | Google DeepMind 的研究表明：没有外部反馈时，模型很难自己纠正推理，有时越改越差[3]；Anthropic 指出代码能用自动化测试验证，适合"评估—优化"循环[10]；AgentCoder 的测试执行者也是脚本而不是模型[9] |
| W4 | **诊断 Agent 先判断错在代码还是测试，再分派修正** | 有依据，需调整 | MIT/微软的研究发现，修正效果的瓶颈是"反馈质量"：换更强的模型来写反馈，修正效果明显提升[4]。所以诊断这一步值得做，而且**应该用强档模型**（见 R2） |
| W5 | **修正循环：有次数上限，变差就回到最好的那次** | 有依据，需调整 | 设停止条件（最大迭代次数）是 Anthropic 的建议[10]。但同一研究[4]发现：在同等花费下，"带着报错反复修"不一定比"直接重新生成一份"强。我们的升级阶梯里没有"从头重写"这一级（见 R3） |
| W6 | **审查 Agent 只作提示，不能阻止通过**（DECISIONS 第 14 条） | 有依据 | 多项研究表明 LLM 做代码审查误报多、准确率中等：有基准测试里最好的工具 F1 也只有约 19%[11]；还有研究发现模型常把正确的代码判成有问题[12]；Mozilla、Ubisoft 的实测中只有约 7–8% 的自动审查意见被采纳[13]。我们让审查"只提示、不否决"，与这些发现一致 |
| W7 | **升级阶梯：查知识库 → 换更强的 Agent → 重新规划 → 转人工** | 有依据（顺序是我的选择） | "先用便宜模型，不行再换贵的"是 FrugalGPT 的级联思路，在实验中最多省 98% 成本[14]；遇到高影响操作时让人来确认，是 OWASP 针对"过度自主"的建议[15]。四级的先后顺序没有找到直接依据 |
| W8 | **智能调度：按历史成功率、成本、负载打分，并留一点"探索"** | 有依据 | 把"选哪个模型"当成多臂老虎机问题、用 UCB 平衡"试新的"和"用好的"，是 LLM 路由研究的主流做法（MetaLLM、OrcaRouter 等）[16]。需要注意：这类方法要足够多的数据才学得会，我们只有 10 道题，学习效果可能有限；α、β、γ 三个权重是我定的，阶段 6 计划做参数扫描 |
| W9 | **经验库：把"失败模式 → 修法"记下来，下次按相关度取前 3 条** | 有依据 | Reflexion 把失败后的反思存进记忆，指导下次尝试[17]；ExpeL 把经验提炼成"见解"，并用新增、加票、减票、修改来维护[18]。后续研究批评 ExpeL 不管相关与否把所有见解都塞进提示词[18]，我们按相关度只取前 3 条，正好避开了这个问题 |
| W10 | **多个编码 Agent 并行写不同模块** | 有依据，需注意 | Cognition（Devin 团队）指出：多个 Agent 并行"写"，容易因为各自的假设不同而互相冲突，应共享完整上下文[19]；Anthropic 也说多 Agent 更适合可以并行拆开的任务，对耦合紧密的编码任务效果较差[20]。我们的设计已经有两道保护：模块之间文件不重叠、接口头文件由任务锁定。还应让每个编码 Agent 都看到**完整的规划结果**（见 R4） |
| W11 | **人工确认节点**（规划后可停下等人确认） | 有依据 | OWASP LLM06：高影响操作应有人工把关[15] |
| W12 | **对照实验：B1 单次、B2 单 Agent 修正、B3/B4 多 Agent 简单调度、完整版、消融** | 有依据，需补充 | 见第一部分：研究要求和单 Agent 对比[1]，并且要按同等花费对比[4][5] |

---

## 三、建议改动（等负责人决定）

**R1 补一个"同等花费"的对照组，成绩表加上花费维度。**
新增基线 B5："单 Agent 每次从头重新生成，不带报错，直到用完和完整版相同的 token 预算"。汇总表除了通过率，再给出"每通过一题平均花多少 token / 多少钱"。
依据：[4][5]。这样答辩时能回答"你们的提升是不是只是因为多花了钱"。

**R2 诊断 Agent 固定用强档模型（deepseek-v4-pro）。**
依据：修正效果的瓶颈在反馈质量，用更强的模型写反馈提升明显[4]。原设计没有规定诊断用哪一档。

**R3 升级阶梯里加一级"从头重写"。**
在"换更强的 Agent"这一步，新 Agent 不接着改旧代码，而是只拿需求和接口从头写一份，同时附上之前失败的要点。
依据：同等花费下，重新生成有时比反复修正更好[4]；而且长时间在同一份错误代码上打转，正是我们用"错误签名"检测的情况。

**R4 每个编码 Agent 都能看到完整的规划结果。**
包括所有模块的划分、职责、接口，而不只是它自己那个模块和直接依赖。
依据：并行写代码时，各 Agent 的假设冲突是主要失败原因之一[19]。成本是提示词稍微变长。

**R5（可选，阶段 5 做）失败原因按 MAST 分类统计。**
MAST 把多 Agent 失败分成三大类、14 种[1]。我们的事件流里信息足够，可以在实验报告里统计"系统设计问题 / Agent 配合问题 / 验证问题"各占多少，作为答辩里"我们分析了失败原因"的材料。

**不改、但答辩时要说清的：**
- 升级阶梯四级的先后顺序是我们的设计（W7）；
- 调度器三个权重是经验值，阶段 6 做参数扫描（W8）；
- 题目少，调度器的学习效果可能有限，这一点要在报告里如实写（W8）。

---

## 参考来源

1. Cemri et al., [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/html/2503.13657v3)（NeurIPS 2025，MAST 分类）
2. （同 1）
3. Huang et al., [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/pdf/2310.01798)（ICLR 2024）
4. Olausson et al., [Is Self-Repair a Silver Bullet for Code Generation?](https://arxiv.org/abs/2306.09896)（ICLR 2024）
5. Chen et al., [Teaching Large Language Models to Self-Debug](https://arxiv.org/pdf/2304.05128)（ICLR 2024）；同等计算量比较的后续工作见 [BESTER](https://arxiv.org/html/2407.19055v1)
6. Zelikman et al., [Parsel](https://arxiv.org/abs/2212.10561v3)（NeurIPS 2023）
7. Bairi et al., [CodePlan: Repository-level Coding using LLMs and Planning](https://www.microsoft.com/en-us/research/publication/codeplan-repository-level-coding-using-llms-and-planning/)（FSE 2024）
8. Hong et al., [MetaGPT](https://arxiv.org/html/2308.00352v7)
9. Huang et al., [AgentCoder](https://arxiv.org/html/2312.13010v2)
10. Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
11. [SWR-Bench](https://arxiv.org/pdf/2509.01494)
12. [Uncovering Systematic Failures of LLMs in Verifying Code Against Natural Language Specifications](https://arxiv.org/pdf/2508.12358)
13. Olewicki et al., [Impact of LLM-based Review Comment Generation in Practice](https://www.arxiv.org/pdf/2411.07091)
14. Chen, Zaharia, Zou, [FrugalGPT](https://arxiv.org/pdf/2305.05176)
15. OWASP LLM06:2025 Excessive Agency，二手解读：[Invicti](https://www.invicti.com/blog/web-security/owasp-top-10-risks-llm-security-2025)
16. 路由即老虎机问题：[NeuralUCB 路由](https://arxiv.org/html/2603.30035v1)、[OrcaRouter](https://arxiv.org/pdf/2605.30736)
17. Shinn et al., [Reflexion](https://arxiv.org/abs/2303.11366v4)（NeurIPS 2023）
18. Zhao et al., [ExpeL](https://arxiv.org/html/2308.10144v3)（AAAI 2024）；批评见 [ICLR 2026 MemAgents Workshop 论文](https://www.alphaxiv.org/abs/2603.24639.md)
19. Cognition, [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)；后续 [Multi-Agents: What's Actually Working](https://cognition.com/blog/multi-agents-working)
20. Anthropic, [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
21. 其他参考：MapCoder（[ACL 2024](https://arxiv.org/html/2405.11403v1)，回忆—规划—编码—调试四个 Agent）、AlphaCodium（[流程工程](https://ar5iv.labs.arxiv.org/html/2401.08500)）、Agentless（[简单三段式流程](https://arxiv.org/html/2407.01489v2)）
