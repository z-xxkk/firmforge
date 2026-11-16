# FirmForge

面向嵌入式固件开发的多 Agent 编排与验证平台（第五届"未来杯"技术大赛赛题一参赛作品）。
目标芯片 STM32F103（Cortex-M3）。设计文档见 `docs/ARCHITECTURE.md`，开发顺序见 `docs/ROADMAP.md`。

## 当前进度

**2026-11-16**

- **最终测量（docs/TEST_PLAN.md 第 11 节）**：6 道测试题 × none、full × 3 次全部跑完；full（已确认规则 + 调度器 + 经验库，冻结）通过率 39%→67%（逐题配对差 +28% ± 10%，超过两倍标准误），none 33%→61%（+28% ± 13%，勉强超过）；4 道留出题修题重判后 single 12/12、none 11/12、full 12/12（题偏简单，不能说明多 Agent 更好）。合计 22.58 元。
- **集成（DECISIONS 第 131 条）**：上板流程以前只交付一个个模块，没有主程序。现在所有模块通过后，编码 Agent 写 app_main.c 和"串口上应该看到什么"，平台编译整个工程、烧录、复位跑两轮核对（不崩溃、不重启、期望的输出次数和周期），完整工程交付到运行目录的 `firmware/`。用今天对话测试做出来的 4 个模块和手写的主程序在真板上验证过这条检查路径；还没有用真模型跑过集成步骤。
- **补测试两处修正（第 131 条）**：补完测试后至少留一次修正机会；补的测试本身有错（编译不过或诊断判为测试的错）时退回补强前的测试，模块按补强前的结果算通过并写明。
- **规则步骤（第 130 条）**：只查需求里提到的外设，片内外设有已确认规则就复用；人确认规则后马上存。对话测试实测：温度、ADC、NVS 的需求不再查资料、不再弹出无关的规则。
- **界面面板统一适配（第 133 条）**：各页面板按角色排，照 VS Code、Apple 设计规范、Android 自适应布局的做法：拉宽窗口时侧栏和右侧面板宽度不变、主区变宽；拖到很窄整块收起，按钮或 Ctrl+B（侧栏）、Ctrl+Alt+B（右侧面板）找回，恢复原来的宽度；窗口窄了自动收起侧栏，最小宽度降到 960；右侧标签放不下时收进"更多"；宽度和开关都记住，双击分隔条恢复默认。对话页历史列表默认列出软件里跑的上板运行，打开时显示当时发的需求（第 132 条）。
- **软件修复（第 127、128 条）**：产品流程编译带上温度、ADC、NVS、SPI 等驱动组件，设计允许片内外设类别；点"重启软件"不再卡死（不强杀后台线程）。

**2026-11-14**

- **4 道新外设题（第 126 条）**：NVS 掉电保存、片内温度传感器、ADC 读电压（带校准）、SPI 回环收发，复用现有跳线、不接新线；题目可以自带接线（task.toml 的 [pins]）。每题有独立核对的隐藏测试和 3 个变异体，15 道题全部上板自检符合预期。作为留出题（`--tasks holdout`），还没有给它们提炼规则、也没有用模型跑过。
- **测试更严（第 125 条）**：测试 Agent 带通用检查清单；模块通过自己的测试后，平台把代码故意改坏最多 3 处（去掉等待、改常数、改换算系数、改比较、参数检查失效）上板跑 Agent 的测试，没被发现的改动用文字说明交给测试 Agent 补测试，再用新测试重测。真板上拿复测里一份"自测全过、隐藏测试没过"的代码试：4 个改动有 3 个它的测试没发现。还没有用真模型跑过整条流程。
- **接线自检（第 124 条）**：诊断说"硬件或接线有问题"时，平台按硬件档案生成并烧录自检程序实测——I2C 器件应答、跳线通断、传感器 INT 线；全部正常就带着实测结果重新诊断、继续修，有不通才停下等人。本板 6 项实测全部正常（29 秒），故意写错两项能报出不通。还没有用真模型跑过整条流程。
- **测试题复测（docs/TEST_PLAN.md 第 10 节）**：改进后 6 道测试题 × none、full × 3 次（7.84 元）。通过率 none 33%→39%、full（前身 verified）39%→56%，6 道题样本下差值没超过两倍标准误，不算显著；自检过严降到 0，每通过一次花费降 45%/62%，用时降 30%/50%。新瓶颈：没过的 19 次里 9 次是被误诊为硬件问题提前停下。
- **夜间排查（第 123 条，docs/NIGHT_REPORT_20261115.md）**：运行记录体检脚本 `scripts/audit_runs.py`（150 次真实运行、14 项检查）和修正循环故障注入演练 `tests/test_repair_sim.py`（8 条不变量、300 个种子）；修了工具参数重复键丢参数、诊断格式不对直接放弃、设计器保存悬空跳转时抛异常三处。调优题上真跑 22 次（2.59 元），"测试编译不过却交给编码 Agent"从 178 次降到 0 次，调优运行 20 次过 19 次。测试题还没复测。
- **基线评测与第 1 项改进（第 122 条）**：最后一轮测试的基线跑完（6 道测试题 × 3 种配置 × 3 次，14.74 元，结果在 results/final_before）；分析发现多 Agent 失败的尝试里大多是 Agent 自己写的测试编译不过、却被交给不能改测试的编码 Agent。现在编译报错只在测试文件里时交给测试 Agent 改，测试 Agent 的提示词附上真有的宏。还没有用真模型验证改进效果。
- **代码更新后提示重启**：设计器面板拖出窗口的问题 11/14 17:04 就修好了，但当时开着的窗口是 16:52 启动的旧代码，所以还能拖。现在窗口每 20 秒看一次代码文件有没有变新，变新了在底部显示"软件的代码已经更新……"和"重启软件"按钮，点一下关掉重开（还有运行没结束会先问）。
- **最后一轮测试的准备（DECISIONS 第 119 条，docs/TEST_PLAN.md）**：上板评测加 full 配置（已确认规则 + 调度器在快档、强档编码 Agent 之间挑 + 经验库），调度成绩和经验库写进这次实验自己的数据库，复测时可冻结（只读）；题目分调优题 5 道、测试题 6 道（`--tasks tune` / `--tasks test`）；汇总加按题聚类的标准误、pass^3、自检准确率（流程结论和隐藏测试是否一致）、每通过一次的花费；新命令 `firmforge board-compare` 做前后逐题配对对比。还没有用真模型跑。
- **视频彩排修的两处**：打开历史评测运行时，隐藏测试判定改为显示"共 N 个用例，通过 X"那一行（以前取了报告最后一行的调试信息），流程结论和隐藏测试不一致时写明以隐藏测试为准；对话页左侧默认只列对话，评测和命令行跑出来的运行勾上"显示评测和命令行的运行"才列出。
- **团队页**：历史表现按任务类型分开显示（上板·硬件层·第一次写、修改……，调度器就是按这个分别打分的），并标明是哪个版本的成绩；改了设置没保存就切到别的 Agent 会问一声；修掉两个 bug：保存查资料 Agent 时会把"查手册原文"工具丢掉、三个角色名显示成英文。
- **设计器的检查和预演（DECISIONS 第 118 条）**：点"开始检查"才检查，结果标时间、改动后提示过期、点问题跳到步骤；上板工作流还会用真正的解释器空跑 6 种情况（不调用模型、不编译），列出每种情况走过的步骤，点一下在画布上依次点亮。设计器四周的面板固定在窗口里，不能再拖出去（画布上的卡片照样能拖）。

**2026-11-13**

- **上板流程完整接入编排（DECISIONS 第 117 条）**：工作流文件由解释器逐步执行，不再是写死的流程加几个开关。设计器里能给上板流程加审查、条件（按硬件层或逻辑层、检查有没有通过）、多个检查、模块做完等人看，也能把写测试排在编码前面；检查器放行的结构都能跑完（随机生成 40 种结构的测试）。新增工作流 `firmware_review`：硬件层模块通过后审查 Agent 再审一遍代码，必须改的问题让编码 Agent 改一次，改坏了退回通过的版本。评测仍走原来的固定路径。
- **去掉评测页（DECISIONS 第 116 条）**：桌面软件只留对话、知识库、工作流、团队、设置；评测用命令行 `firmforge board-eval`，下面旧条目里提到的评测页已不存在。
- **知识库页（DECISIONS 第 114、115 条，替换原来的硬件页；下面旧条目里的"硬件页"指它）**：全部资料、规则、手册和实测对不上的地方按固定目录排——芯片 → 开发框架（ESP-IDF v6.1，按外设）→ 外接器件（按类别）→ 开发板；芯片格显示读自 ESP-IDF 的芯片能力，板子格显示硬件档案；能搜索（型号、寄存器、函数名、原文）、看依据和手册原页、驳回已在用但错了的规则（必须写理由）。新规则的确认在对话里做。
- **平台不写死芯片（DECISIONS 第 114 条）**：芯片有哪些 GPIO、有没有 USB 串口、有哪些外设，从本机 ESP-IDF 的 `soc_caps.h` 读，ESP-IDF v6.1 支持的 13 种芯片平台都能编译（原有三种芯片的编译配置不变）；第一次上板前用 esptool 识别板子上的芯片和 Flash 大小，和硬件档案对不上就不烧录；手册（PDF、头文件、文本）拖进对话就登记进资料清单、算校验值、提取文字，等级记为"用户提供"；需要人确认的规则直接在对话里逐条确认，规则、确认要点和框出引用的手册原页并排。`firmforge doctor` 会列出 ESP-IDF 支持的芯片和插着的板子。

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
  - 评测页：设置、预估花费、题目 × 配置结果表、运行记录，读的是和命令行同一份 runs.jsonl。（2026-11-13 已去掉，第 116 条）
  - 已验证（假模型、真板子，不花钱）：从对话页写一句需求跑完整流程（查资料 → 在硬件页通过 1 条、驳回 1 条 → 设计 → 编码、测试 → 上板 → 诊断修正）；从对话页跑一道评测题，隐藏测试 6/6；评测页开始、运行中暂停、再开始接着跑；硬件页查资料提炼规则并确认入库。三种窗口大小（最小 1200×760）× 深浅两种主题逐页检查，面板都不超出窗口。还没有用真实模型从界面跑过。
  - 同一时间只能有一个运行用板子：对话页的上板运行没结束时，别的上板运行不能开始。
- 自动化找 bug（2026-11-10）：用固定种子的随机操作加不变量检查（Android Monkey、Hypothesis 状态机测试的做法），设计器跑了 12000 步、其他几页跑了 4500 步；找到并修掉 8 个 bug（步骤叠在一起、拉线报错、撤销要按两次、失效的选中状态、连点"让 AI 改"报错、设置页后台任务没结果时报错、运行没结束时关窗口程序崩溃等），每个都有回归测试。测试覆盖率从 81.3% 到 84.9%。
- 故障注入和变异测试（2026-11-10，DECISIONS 第 103 条）：
  - 故障注入 19 个场景（断网、限流、回复读到一半断开、模型一直回空或乱码、串口读到一半板子被拔掉、运行被强制结束留下写坏的记录、规则库文件损坏、没有写权限等）：凡是会崩溃、或出错后说不清原因的地方都修了，每个场景都有测试。其中模型一直不按格式交文件时多 Agent 流程会崩溃，正式评测遇到会整轮中断。
  - 变异测试（`tests/mutation.py`，只用标准库）：把判定、串口、合规检查、评测汇总的代码故意改错一处，共 292 处，看测试能不能发现。隐藏测试判定 `judge_on_board` 原来只能发现 1/26，补测试后 25/26；总体从 65% 到 84%，剩下的逐个看过，是不影响结果的改动（例如截取 300 还是 301 个字）。
  - 补测试时发现 1 个判定 bug：串口输出里有崩溃信息时，截取崩溃那一行用错了位置，截到空行会把崩溃当成没崩溃。已修。
- 界面上的三处亮点展示（2026-11-11，DECISIONS 第 105 条，按"对真正写固件的人有没有用"挑的）：
  - 对话页"过程"标签：一个模块每次尝试的检查结果、上板通过几个用例、诊断归因、交给哪个 Agent 改、两次之间改了多少行，排成一条时间线；点某一次，下面的检查、上板、代码标签换成那一次的内容（参考 GitHub Actions 在多次尝试之间切换）。
  - 硬件页规则详情"在手册里看原文"：打开引用的那一页手册，原文用框标出（参考 CoCounsel、Noxtua 点引用在旁边打开原文档并高亮）。位置来自 poppler 给的单词坐标；没装 poppler 或手册原件不在时只显示文字，并说明原因。
  - 硬件页"手册和板子对不上"：两张对照卡（MPU 模块 WHO_AM_I 手册 0x68、实测 0x70；MAX30102 测温完成时刻），数据在 `hardware/esp32c3_supermini/conflicts.json`，加载时核对原句确实在手册那一页、实测记录确实存在。
  - 运行结束时"Agent 自评和真板判定并排"、评测页的自评对照表没有做成界面功能：用户日常使用时没有隐藏测试，这两样只给评委讲道理用，放进视频和方案书。
- 对话页工程图和接线图（2026-11-13，DECISIONS 第 109、113 条）：
  - 上板流程时，右上那块用开关切换"工程图 / 接线图 / 流程图"。
  - 工程图从上到下是逻辑层 → 驱动层 → 芯片外设（带引脚）→ 外接器件；积木写测试结果，没过的标红；层间连线是上层代码里字面用到的下层接口；驱动连到它用到的外设和器件（来自引脚宏、器件宏）；器件标出上板时有没有应答。平时只显示模块名和结果，鼠标停上去看接口；"展开"后会话列表收起、对话收成窄栏，积木里列出接口原型（没过的用例用到的标 ✗），连线上写出用到的函数。
  - 接线图：这个项目用到的每个引脚接到哪个器件或外设、器件有没有应答、哪些引脚不能用、哪些空着；只画运行记录里有的连接，不画电源和地线。
  - 选中模块时，详情标签上方一行接口概要：接口、被谁用到、没过的用例用到哪些接口。
  - 只用能保证准确的来源：设计结果、项目接线、模块最后交的代码（字面扫描）、串口日志和电脑测试报告；不推断数据流向、不解释代码、不从源码推断寄存器写入。
  - 截图 `docs/screenshots/gui_project_map.png`、`gui_project_map_expanded.png`、`gui_wiring.png` 用的是按真实格式造出来的示意运行（还没有真实的多模块运行）。
- 接线归项目（2026-11-13，DECISIONS 第 108 条）：
  - 设计 Agent 在设计里给出这个项目的接线（需求写明了就照写，没写就优先沿用默认接线）；平台检查 GPIO 在芯片上存在、不用保留引脚、不重复、用到的 I2C 器件写了总线引脚，不合格就退回重做。
  - 检查通过后，编码、测试、生成 `hw_config.h`、上板都改用项目接线，运行目录里留 `wiring.json`；设计确认消息里列出接线，可以写意见让它改。
  - 上板时用测试程序"用例前复位外接器件"的应答结果实测确认：器件应答了就标"已实测确认"；没应答在对话里提示检查接线。只能确认 I2C 器件的数据线和地址，确认不了中断等其他引脚。
  - 同一个对话里再跑，设计 Agent 会拿到上一次定下的接线并沿用；跨对话复制接线还没做。
  - 设计不写接线时（包括全部评测题）沿用硬件档案的默认接线，和以前一样。
- 上板流程接入编排（2026-11-13，DECISIONS 第 110 条，轻量接入）：
  - 上板流程改为按工作流 `workflows/firmware_dev.toml` 执行：查资料提炼规则 → 确认规则 → 设计分层 → 确认设计（开了"设计后等我确认"才停）→ 每个模块：编码和写测试并行 → 合规、编译、上板测试 → 修正循环（最多 4 次）。
  - 设计器能打开、修改、试运行这个工作流，步骤箱只放执行代码支持的步骤；执行不了的改动（比如在模块流程里加审查或条件分支、把检查放到编码前面、指定角色不对的 Agent）保存前就拦下并说明原因。
  - 编码交给哪个 Agent 由调度器打分：按任务特征分组（硬件层还是逻辑层、第一次写还是修改）统计各 Agent 的成功率，加上成本、负载、探索；同一个 Agent 在同一个模块上连续两次没修好就换人；打分理由显示在"调度"标签。
  - 修好的问题存进经验库（诊断给出的改法），以后同类模块编码前检索出来附在提示词后面。
  - 评测不受影响：评测不读工作流，编码仍固定用 coder-strong，和第 1 轮一样。
  - 只用假模型测过；还没有用真实模型跑过一次按工作流的需求。
- 打开历史运行直接显示完整的对话记录（DECISIONS 第 106 条），不再自动回放；想看过程动起来点标题右边的"从头回放"。顺带修掉：对话区被长的工具查询和代码片段撑出横向滚动条；对话页选着"上板"时，设计器"试运行"拿上板题去找纯逻辑题而报错（随机点击测试找到的）。
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
| 可选依赖 | 桌面软件 `PySide6-Essentials`（≥ 6.7，本机测试用 6.12.0，界面测试镜像里是 6.9.1）；读 PDF 资料 `pypdf`（≥ 5，测试用 6.21.4）；界面上显示手册原页用 poppler 的 `pdftoppm`、`pdftotext`（Ubuntu 的 `poppler-utils` 包，测试用 24.02.0；没有时只显示文字，不影响判定） |
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

0. 系统软件包（新装的 Ubuntu 默认没有 `python3-venv`，不装的话下一步建虚拟环境会失败；`poppler-utils` 可选，用来在界面上显示手册原页）：
   ```bash
   sudo apt install git python3-venv poppler-utils
   ```
   桌面版 Ubuntu 自带桌面软件要的 Qt 运行库；在服务器版或精简环境里还要装
   `libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libglib2.0-0 fonts-noto-cjk`
   （和 `docker/Dockerfile.gui` 里的一样）。没装时桌面软件打不开，对应的单元测试会跳过。
1. 取代码，建虚拟环境，装可选依赖：
   ```bash
   git clone https://github.com/z-xxkk/firmforge.git && cd firmforge
   python3 -m venv .venv
   .venv/bin/pip install -e ".[gui,sources]"
   ```
   桌面软件的 PySide6 有 84 MB。2026-11-14 在全新的 Ubuntu 24.04 容器里照这一步装，直连 PyPI 用了 47 分钟；
   下载慢时可以换国内镜像，例如在命令后面加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
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
   本机没装判定器工具链（gcc、arm-none-eabi-gcc、cppcheck）、Qt 运行库或手册原件时，用到它们的测试会跳过并写明原因，
   不会报错；全部测试要在 Docker 镜像里跑（`docker/Dockerfile.gui`）。
7. 板子插好以后，自检上板评测题（标准答案要通过、变异体要被抓住，不调用模型、不花钱）：
   ```bash
   .venv/bin/firmforge check-board-tasks --board
   ```
8. 打开桌面软件：
   ```bash
   .venv/bin/firmforge gui
   ```
   想在桌面的"显示应用"里直接点开，运行一次 `.venv/bin/firmforge gui --install-shortcut`（`--remove-shortcut` 去掉）。

### 手册原件

芯片手册、器件手册有版权，不放进仓库。`hardware/<板子>/sources.toml` 里每份资料都写了官方下载网址（`url`）、
放到哪里（`file`，相对于 `.ff_sources/raw/`）和校验值（`sha256`）；ESP-IDF 头文件取自本机装的 v6.1 源码树
（`file` 里的 `__` 对应路径分隔符）。平台读资料前会核对校验值，对不上就报错，不会用错的文件。

要用到手册原件的功能：对话里写需求跑上板流程（默认规则模式要查资料提炼规则）、`firmforge rules build`、
评测的 unverified 配置。不需要原件的：评测题的 single、none、verified 配置（verified 用仓库里已确认的规则库）、
全部单元测试、判定器、知识库页浏览（页数、原页图片要原件才显示）。

### 没有板子时能复现什么

- 全部单元测试（假模型，不联网）：`.venv/bin/python -m unittest discover -s tests`。
- 上板评测题只编译、不上板：`firmforge check-board-tasks`（不加 `--board`）。
- 第 1 轮评测、失败分析案例和试点的原始数据都在 `docs/eval_data/`，可以用 `firmforge board-eval` 的同一份格式复核。

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
