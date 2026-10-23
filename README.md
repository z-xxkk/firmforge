# FirmForge

面向嵌入式固件开发的多 Agent 编排与验证平台（第五届"未来杯"技术大赛赛题一参赛作品）。
目标芯片 STM32F103（Cortex-M3）。设计文档见 `docs/ARCHITECTURE.md`，开发顺序见 `docs/ROADMAP.md`。

## 当前进度

**阶段 1（判定器 + 首批 3 个任务 + Docker）：代码完成，等待 Docker 环境验收。**

已实现并有单元测试覆盖：

- 判定器 `firmforge.verifier`：L1 主机编译、L2 交叉编译、L3 资源预算（flash / RAM / 单函数栈帧 / 动态栈）、
  L4 静态检查（禁用符号 + cppcheck）、L5 单元测试（Unity + ASan/UBSan + 超时 + gcov 覆盖率）、隐藏测试层；
  JSON 报告、错误签名、给 Agent 看的报告（剔除隐藏测试层）。
- 任务自检 `check_task`：参考实现必须通过，每个变异体必须在预期层第一个失败。
- 评测任务：`ringbuf`、`crc16_modbus`、`debounce`（各含 spec、锁定的头文件、隐藏测试、参考实现、变异体）。
- 命令行：`doctor`、`tasks`、`check-task`、`verify`。
- `docker/Dockerfile`。

在没有 arm-none-eabi 工具链和 cppcheck 的机器上（开发机现状）已验证：L1、L4 禁用符号检查（改用主机 nm）、
L5、隐藏测试；7 个变异体中 6 个在预期层失败。以下内容**尚未实际运行过**，要在 Docker 里验收：

- L2 交叉编译、L3 资源预算的真实工具链路径（L3 的统计逻辑已用主机 gcc + size 测过）；
- cppcheck 子检查；
- `crc16_modbus` 的"查表数组没加 const"变异体（预期在资源预算层失败）；
- 各任务的预算目前是估计值，要用 `check-task` 在容器里实测后校准；
- `docker build` 和 `pip install -e .`（开发机没有 Docker 和 pip）。

## 快速开始

```bash
docker build -t firmforge -f docker/Dockerfile .
docker run --rm firmforge doctor
docker run --rm -v "$PWD":/work -w /work firmforge check-task --all
docker run --rm -v "$PWD":/work -w /work firmforge verify tests/fixtures/syntax_error --task tasks/ringbuf
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
- 在 Linux 上用 `docker run -v` 运行时，容器内生成的 `.ff_build/` 属于 root。

## 开源组件

- [Unity](https://github.com/ThrowTheSwitch/Unity) v2.6.1，MIT 许可，原样放在 `third_party/unity/`。

## 人工智能使用说明

代码由 Claude Code 按设计文档实现，项目负责人负责想法、设计决定和验收（见 `docs/DECISIONS.md` 第 5 条）。
