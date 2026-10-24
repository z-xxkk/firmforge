# 约束依据审计（2026-10-24）

负责人提问："这些约束是随便设计的，还是严谨参照现实工程设计的？"本文逐条核对现有约束的出处。

分类：
- **有依据**：能对应到公开标准或权威实践，并给出来源；
- **部分有依据**：方向有依据，但我们的做法比标准窄或宽；
- **我的选择**：没有找到标准，是为评测需要做的取舍，写明理由；
- **建议改**：查资料后发现现有做法和工程实践不一致。

说明：MISRA C 的正式条文是付费文档，下面的条文措辞来自静态分析工具厂商的公开文档（MathWorks、IAR 等），不是 MISRA 原文。

---

## 一、代码约束（阅卷机强制检查）

| # | 现有约束 | 判断 | 依据 |
|---|---|---|---|
| A1 | 禁用 malloc / calloc / realloc / free / aligned_alloc | 有依据 | MISRA C:2012 Rule 21.3"`<stdlib.h>` 的内存分配和释放函数不得使用"，Directive 4.12"不得使用动态内存分配"[1]；NASA/JPL《Power of 10》第 3 条"初始化之后不得使用动态内存分配"[2] |
| A2 | 禁用 printf / sprintf / fprintf / puts / putchar | 部分有依据，**建议改** | MISRA C:2012 Rule 21.6 禁止的是 `<stdio.h>` 的**全部**输入输出函数[1]，我们只列了 5 个，漏了 snprintf、scanf、getchar、fopen 等 |
| A3 | 编译开 -Wall -Wextra -Werror（主机还加 -pedantic），任何警告都算失败 | 有依据 | 《Power of 10》第 10 条：从第一天起用最严格的警告设置，"所有代码必须零警告编译"[2] |
| A4 | cppcheck 静态分析，warning 及以上算失败 | 有依据 | 《Power of 10》第 10 条：每天用至少一个强静态分析器检查，并应零警告通过[2] |
| A5 | 禁止 VLA 和 alloca（动态栈） | 有依据 | MISRA C:2012 Rule 18.8"不得使用变长数组"，理由是栈用量无法预测[3] |
| A6 | 检查单个函数的栈帧 | 部分有依据，**建议改** | 《Power of 10》的思路是：没有堆、没有递归，才能静态推出栈用量上界[2]。我们只查单个函数，既没查**递归**（MISRA Rule 17.2、《Power of 10》第 1 条都禁止[1][2]），也没算**调用链最坏情况** |
| A7 | 传入 NULL 等非法参数时不能崩溃 | 有依据 | 《Power of 10》第 7 条：被调用的函数必须检查调用方传入的所有参数[2] |
| A8 | 死循环用运行超时（10 秒）兜底 | 部分有依据 | 《Power of 10》第 2 条要求每个循环有可静态证明的上界[2]。静态证明很难自动化，我们只做运行时兜底，**不改**，在局限里说明 |
| A9 | 资源额度 = 参考实现实测 × 2（栈不低于 32 字节） | 我的选择 | 没有找到行业标准。公开经验是：栈估算加 20–30% 余量，Flash 留 10–25% 增长余量（非正式来源）[4]。我们给 100% 余量，因为额度的用途不同：不是给产品留升级空间，而是容纳 Agent 不同写法的合理差异（例如 CRC 查表写法比逐位写法大 5 倍）。**不改**，但答辩时要能说清这个区别 |
| A10 | 头文件由任务锁定，Agent 不能改接口 | 我的选择 | 评测公平性需要；与下面 C 组的最小权限原则一致 |

## 二、评测方法约束

| # | 现有约束 | 判断 | 依据 |
|---|---|---|---|
| B1 | 最终只看隐藏测试，Agent 看不到 | 有依据 | SWE-bench 的做法：判分用的 FAIL_TO_PASS / PASS_TO_PASS 测试对 Agent 不可见，两组都通过才算解决[5] |
| B2 | 用"故意改坏的版本"检验隐藏测试 | 有依据 | 变异测试（DeMillo、Lipton、Sayward，1978）：测试集能抓出所有变异体才算充分；"耦合效应"假设：抓得住简单错误，就抓得住更复杂的错误[6] |
| B3 | 隐藏测试要写得足够细 | 有依据 | EvalPlus 发现 HumanEval 每题测试不到 10 个，把测试加到 80 倍后，各模型通过率下降 19%–29%，排名也变了[7] |
| B4 | 隐藏测试只考需求文档里写明的行为 | 有依据 | OpenAI 在 SWE-bench Verified 中指出，原测试有时过于具体，甚至考了与问题无关的东西，会把正确答案判错[5]。我们的"★ 规则审核 + 规则写进需求"正是为了避免这一点 |

## 三、Agent 工作流约束

| # | 现有约束 | 判断 | 依据 |
|---|---|---|---|
| C1 | Agent 只能写 `src/`、`tests/`，路径白名单由程序检查，不靠模型自觉 | 有依据 | OWASP《LLM 应用十大风险》2025 版 LLM06"过度代理"：给 Agent 的功能、权限、自主性都不应超过任务所需；授权应在模型之外的系统里执行[8] |
| C2 | 修正轮数有上限（B2 最多 3 轮；完整版每模块最多 6 次） | 有依据（具体数字是我的选择） | Anthropic《Building effective agents》：常用停止条件（如最大迭代次数）保持控制[9]。3 和 6 这两个数没有标准，来自设计文档，可在实验里调 |
| C3 | 判定器报错回喂、订正（B2 和完整版的修正循环） | 有依据 | 同上文章的 evaluator-optimizer 模式：评估标准清晰时最有效；代码可用自动化测试验证[9] |
| C4 | 固定工作流模板 + 模板内由 Agent 干活，而不是完全自主 | 有依据 | 同上：工作流适合定义明确的任务，可预测、一致[9] |
| C5 | Agent 写的代码在 Docker 容器里编译运行 | 部分有依据，**建议加强** | 同上文章建议在沙箱中充分测试[9]。但容器内测试程序默认可以联网、可调用 system 等函数。最小权限原则[8]下，应把 system、fork、exec、socket 等也列入禁用符号 |

## 四、第一批题目的 ★ 规则

| # | ★ 规则 | 判断 | 依据 |
|---|---|---|---|
| D1 | 软件定时器：周期为 0 拒绝 | 有依据 | FreeRTOS：周期必须大于 0，内核用断言检查[10] |
| D2 | 软件定时器：运行中再次启动，从当前时刻重新倒数 | 有依据 | FreeRTOS xTimerReset：对已运行的定时器，"重新计算到期时间，使其相对于调用 xTimerReset() 的时刻"；xTimerStart 对已运行定时器的效果相同[10] |
| D3 | 软件定时器：单次响一次后停止，循环按周期一直响 | 有依据 | FreeRTOS 的 one-shot / auto-reload 定义[10] |
| D4 | 中值滤波：数据个数为偶数时取中间两个里较小的 | **建议改** | 统计上的标准定义是取中间两个的平均[11]。我原先为了避免小数选了"较小的"，偏离了通行定义 |
| D5 | 串口收包：坏帧之后不回头重扫，只从新字节找帧头 | **建议改** | OpenMV 的串口协议教程指出：只有末尾 CRC 的"帧头 + 长度"格式，出错后会吞掉后面的数据包，需要重新找同步点[12]；MIT 课程讲义也指出按长度分帧出错后难以恢复，需要重新同步[13]。我的"不回头"会丢掉本可收到的帧，而收到的字节本来就在缓冲区里，回头重扫没有额外开销 |
| D6 | 串口收包：长度超过 64 立即判坏帧 | 有依据 | 同 [12]：尽早发现长度字段错误，避免吞掉整段数据 |
| D7 | 帧格式、CRC 范围、空帧合法 | 我的选择 | 私有协议没有统一标准。格式本身合理，是题目设定 |

---

## 五、建议改动汇总（等负责人决定）

1. **A2 + C5 扩充禁用符号**：按 MISRA 21.6 禁用 `<stdio.h>` 全部输入输出函数，并按最小权限原则禁用 system、fork、exec 系列、socket 等系统调用。
2. **A6 栈检查升级**：增加"禁止递归"检查，并计算调用链最坏栈用量（GCC 的 `-fcallgraph-info=su` 可输出调用图和每个函数的栈帧；可行性要先验证），替代现在的单函数检查。
3. **D4 中值滤波偶数个**：改为取中间两个的平均，整数除法向零取整（C 语言的默认行为）。
4. **D5 串口收包重新同步**：坏帧之后，从坏帧头的下一个字节开始，在已收到的字节里重新找帧头。

不改但要在答辩时说清的：A8（循环上界只做运行时兜底）、A9（2 倍额度的用途）、C2（3 / 6 轮的具体数字）。

可选，以后再议：cppcheck 自带 MISRA 插件（`--addon=misra`），开源可用，但规则条文受版权保护，只能报规则编号[14]。可以作为"只提示、不判失败"的附加信息。

## 参考来源

1. MathWorks：[Modeling Guidelines for MISRA C:2012 Compliance](https://ch.mathworks.com/help/ecoder/ug/modeling-guidelines-for-misra-c2012-compliance.html)（Rule 21.3、21.6、17.2、Directive 4.12）
2. Holzmann, G. J., *The Power of 10: Rules for Developing Safety-Critical Code*, IEEE Computer, 2006；条文摘录见 [Wikipedia](https://en.wikipedia.org/wiki/The_Power_of_10:_Rules_for_Developing_Safety-Critical_Code)
3. MathWorks Polyspace：[MISRA C:2012 Rule 18.8](https://www.mathworks.com/help//releases/R2021a/polyspace_code_prover_access/ref/misrac2012rule18.8.html)；IAR：[C-STAT MISRAC2012-Rule-18.8](https://docs.iar.com/ewrx/5.2x/en/c-stat--static-analysis/c-stat--checks/descriptions-of-checks/misrac2012-rule-18-8.html)
4. Embedded Artistry：[How do You Reason About Flash Memory Size Overhead?](https://embeddedartistry.com/blog/2020/02/24/qa-how-do-you-reason-about-flash-memory-size-overhead/)；余量百分比来自非正式经验文章，仅作参考
5. OpenAI：[Introducing SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/)
6. [Mutation testing（Wikipedia）](https://en.wikipedia.org/wiki/Mutation_testing)；DeMillo, Lipton, Sayward, *Hints on Test Data Selection*, IEEE Computer, 1978
7. Liu et al., [Is Your Code Generated by ChatGPT Really Correct?（EvalPlus）](https://arxiv.org/abs/2305.01210v3)
8. OWASP LLM06:2025 Excessive Agency，二手解读：[Invicti](https://www.invicti.com/blog/web-security/owasp-top-10-risks-llm-security-2025)、[ByteHide](https://docs.bytehide.com/products/radar/sast/owasp/llm06-2025)
9. Anthropic：[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
10. FreeRTOS-Kernel 源码 [include/timers.h](https://pigweed.googlesource.com/third_party/github/FreeRTOS/FreeRTOS-Kernel/+/HEAD/include/timers.h?autodive=0)；论坛：[period 为 0 触发断言](https://forums.freertos.org/t/hit-an-assert-in-timer-c-file-configassert-pxtimer-xtimerperiodinticks-0/6423)
11. PyDL：[median（偶数个取中间两个的平均）](https://pydl.readthedocs.io/en/latest/api/pydl.median.html)
12. OpenMV：[Serial protocols, framing, and CRCs](https://docs.openmv.io/openmvcam/tutorial/hardware/uart/serial-protocols.html)
13. MIT 16.36 Communication Systems Engineering，[Lecture 16 讲义](https://mitocw.ups.edu.ec/courses/aeronautics-and-astronautics/16-36-communication-systems-engineering-spring-2009/lecture-notes/MIT16_36s09_lec16.pdf)
14. Cppcheck [addons/misra.py](https://git.stg.centos.org/source-git/cppcheck/blob/1aa6ed751eb212bc2eca5049a1b4599fd816aae8/f/addons/misra.py)
