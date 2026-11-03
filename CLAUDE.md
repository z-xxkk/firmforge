# FirmForge 项目规则（Claude Code 每次进入本目录都会读这个文件）

## 项目是什么

这是第五届"未来杯"技术大赛赛题一（Agent 编排创新应用）的参赛作品。

FirmForge 是一个**面向嵌入式固件开发的多 Agent 编排与验证平台**。用户输入一段嵌入式 C 模块的需求，平台完成以下流程：

1. 拆解任务；
2. 调度多个 LLM Agent 分工编写代码和测试；
3. 用编译器、资源预算、静态分析、单元测试等**客观判定器**逐层检查；
4. 检查失败就进入修正循环；
5. 最终交付通过检查的代码和一份验证报告。

- 目标芯片：STM32F103（Cortex-M3）
- 大模型：DeepSeek，走 OpenAI 兼容接口
- 产品形态：Python 核心库 + 命令行 + 桌面软件（PySide6）

**完整设计在 `docs/ARCHITECTURE.md`，开发顺序在 `docs/ROADMAP.md`，已定的设计决定在 `docs/DECISIONS.md`。** 开始任何工作前，先读这三份文件里与当前阶段相关的部分。

## 分工

- **项目负责人（用户）**：提出想法、做设计决定、验收。用户不写代码。
- **Claude Code（你）**：按 ROADMAP 一个阶段一个阶段地实现代码和测试。
- **遇到设计文档没覆盖的取舍时**：停下来，列出 2–3 个方案和各自利弊，请用户选。不要自己拍板。用户选定后，把结论追加到 `docs/DECISIONS.md`。
- **设计文档本身有错或行不通时**：先说明问题和建议的改法，经用户同意后再改文档，然后才改代码。

## 硬性规则

1. **核心库只用标准库。** `src/firmforge/` 中除 `gui/` 子包以外的代码只使用 Python 标准库（Python ≥ 3.11）。
   - LLM 调用用 `urllib` 自己实现 OpenAI 兼容接口，不引入 `openai` 包。
   - 配置文件用 TOML，读取用 `tomllib`。
   - 存储用 `sqlite3`。
   - PySide6 只允许出现在 `src/firmforge/gui/`。
   - 例外：读取 PDF 资料（芯片手册等）允许使用 `pypdf`，只能出现在读取资料的那个子包里；没装时该功能明确报"不可用"，不能假装读到了（DECISIONS 第 73 条）。
2. **核心与界面分离。**
   - `cli.py` 和 `gui/` 只调用核心库的公开接口。
   - 核心库不 import `cli` 或 `gui`。
   - 核心库通过事件（见 ARCHITECTURE §2.8）向界面报告进度。
3. **跨平台（Linux + Windows）。**
   - 路径用 `pathlib`。
   - `subprocess` 一律传参数列表，不用 `shell=True`。
   - 读写文本一律 `encoding="utf-8"`。
   - 不调用 `rm`、`cp`、`sed` 这类 Linux 专有命令，用 `shutil` 和 `pathlib` 代替。
   - 外部工具命令全部经过 `config.Toolchain` 获取，并且可以用环境变量覆盖。
4. **工具缺失时不能假装通过。**
   - 判定器用的工具链（gcc、arm-none-eabi-gcc、cppcheck、gcov）装在 Docker 镜像里，核心代码不假设宿主机上有它们。
   - 某个工具缺失时，对应的层或子检查标记为 `skipped` 并写明原因，绝不判为通过。
5. **隐藏测试隔离。**
   - 任务目录下的 `hidden_tests/` 只有评测流程（`eval` 和 `check-task`）可以读。
   - 任何 Agent 的提示词、上下文、工作目录都不能包含隐藏测试的内容。
   - 发现泄露路径就当 bug 修，并补一个测试防止复发。
6. **不泄露密钥。**
   - API key 只从环境变量或 `.env` 读取，不出现在代码、日志、提交、报告中。
   - 记录 LLM 请求时，请求头要脱敏。
7. **每个阶段都要有测试。**
   - 用标准库 `unittest`，测试文件放在 `tests/`。
   - 需要调用 LLM 的逻辑用 `FakeLLM`（预设回复）来测，单元测试不能依赖网络。
8. **语言约定。** 注释、日志、用户可见的文字用中文；标识符用英文。
9. **不夸大。** README、报告、提交信息只写已经实现并且测试过的功能。

## 常用命令（随实现补充，保持与实际一致）

```bash
python -m unittest discover -s tests -v       # 全部单元测试
firmforge doctor                               # 检查工具链是否可用
firmforge check-task --all                     # 自检评测任务：参考实现要通过，变异体要在预期层失败
firmforge verify <workdir> --task tasks/<id>   # 对一个工作目录跑判定器
firmforge models                               # 列出 DeepSeek 可用型号（需要 .env 里的 key）
firmforge run --task tasks/<id> --config b2    # 单 Agent 跑一个任务，生成 .ff_runs/<run_id>/
firmforge run --task tasks/<id> --config full --human-review   # 多 Agent；规划后停下等确认
firmforge eval --configs b1,b2,b3,b4,full,b5 --tasks all --runs 1 --out results/<实验名>   # b5 要排在 full 后面
firmforge replay .ff_runs/<run_id> --speed 4   # 终端回放
firmforge agents                               # 列出 Agent：角色、版本、档位、思考强度、技能、工具、记忆、历史表现
firmforge run --task tasks/<id> --config full --tier strong --think max --workflow module_dev@1.0.0
firmforge mcp                                  # MCP 服务端（stdio）；--selftest 自检
firmforge bench --out results/bench            # 性能测试（假模型，不花钱）
firmforge stats .ff_runs                       # 从运行记录统计延迟
firmforge gui                                  # 桌面软件（先 pip install -e .[gui]）
firmforge rules build --scopes i2c,mpu6050      # 查资料提炼规则（调用模型，需人工确认）
firmforge firmware --spec 需求.md                # 嵌入式新流程：规则 → 设计 → 编码、测试 → 上板
firmforge check-board-tasks [题目 id]            # 上板评测题自检（没有板子时编译标准答案和变异体）
docker build -t firmforge-gui -f docker/Dockerfile.gui .   # 界面测试与截图用的镜像（offscreen）
docker build -t firmforge -f docker/Dockerfile .
docker run --rm --env-file .env --user "$(id -u):$(id -g)" -v "$PWD":/work -w /work firmforge <子命令>
```

## 每完成一个阶段

1. 跑全部测试，贴出结果。
2. 按 ROADMAP 中该阶段的"验收"逐条演示，并给出用户可以自己复制运行的命令。
3. 更新 `README.md` 的"当前进度"一节。
4. 用 3–5 句话向用户解释本阶段的核心设计（帮助用户准备答辩），并提出 1–2 个需要用户拍板的问题（如果有）。
5. 提交 git，提交信息用中文写明完成了什么。
