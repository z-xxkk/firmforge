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

| 任务 | 实测 flash / RAM / 最大栈帧（字节） | 预算 |
|---|---|---|
| ringbuf | 272 / 0 / 24 | 544 / 0 / 48 |
| crc16_modbus | 128 / 0 / 16（查表写法 flash 632） | 1264 / 0 / 32 |
| debounce | 72 / 0 / 0 | 144 / 0 / 32 |

**阶段 2（接入 DeepSeek + 单 Agent 基线 + 评测脚本）：代码完成，单元测试通过；还没有用真实 API 跑过，等待负责人验收。**

- LLM 客户端 `firmforge.llm`：标准库 urllib 实现的 OpenAI 兼容接口，429 / 5xx / 网络错误指数退避重试；
  FakeLLM（单元测试用，不联网）；调用日志 `llm_calls.jsonl` + `llm_calls/<n>.json`，Authorization 显示为 `***`。
- 文件块协议与路径白名单：Agent 只能写 `src/`、`tests/` 下的 `.c` / `.h`，其他路径拒绝并记录。
- 运行目录 `.ff_runs/<run_id>/`、事件流 `events.jsonl`、SQLite（runs、node_attempts 两张表）。
- 单 Agent 基线：B1 一次生成不修正；B2 判定报错直接回喂、最多 3 轮修正；同一个 Agent 同时写实现和测试。
- 评测：`eval` 输出 `results.csv`、`summary.md`；最终是否通过只看 L1–L4 + 隐藏测试，在临时目录判定。
- 命令：`models`、`run`、`eval`（含 `--estimate`）、`replay`。
- 单元测试 106 个（本机 1 个因缺交叉工具链跳过；Docker 中全部通过），其中包括"发给 LLM 的消息和运行目录里都不含隐藏测试内容"。

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

- 栈检查只看单个函数的栈帧，没有分析调用链的最坏情况。
- 在 Linux 上用 `docker run -v` 运行时要加 `--user "$(id -u):$(id -g)"`，否则容器内生成的 `.ff_build/` 属于 root。
- 国内网络下拉取基础镜像需要给 Docker 守护进程配置代理（`/etc/systemd/system/docker.service.d/http-proxy.conf`）。

## 开源组件

- [Unity](https://github.com/ThrowTheSwitch/Unity) v2.6.1，MIT 许可，原样放在 `third_party/unity/`。

## 人工智能使用说明

代码由 Claude Code 按设计文档实现，项目负责人负责想法、设计决定和验收（见 `docs/DECISIONS.md` 第 5 条）。
