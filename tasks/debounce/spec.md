# 按键消抖 debounce

实现 `include/debounce.h` 中声明的全部函数。调用方每个 tick（例如每 1 ms）读一次按键原始电平，调用一次 `debounce_update`。

## 约束

- 不能使用动态内存和 printf 系列函数。
- `debounce_t` 的字段由头文件给出，实现只能使用这些字段，头文件不能修改。

## 函数行为

**`debounce_init(d, threshold_ticks, initial_pressed)`**
- 稳定状态设为 `initial_pressed`，计数清零。
- `threshold_ticks` 为 0 时按 1 处理。
- d 为 NULL 时什么都不做。

**`debounce_update(d, raw_pressed)`**，每个 tick 调用一次：
- 原始输入与稳定状态**不同**：计数加 1；计数**达到** threshold 时，稳定状态翻转为原始输入，计数清零，返回 `BTN_EVT_PRESS`（翻转为按下）或 `BTN_EVT_RELEASE`（翻转为松开）；未达到时返回 `BTN_EVT_NONE`。
- 原始输入与稳定状态**相同**：计数清零，返回 `BTN_EVT_NONE`。
- d 为 NULL：返回 `BTN_EVT_NONE`。

**`debounce_is_pressed(d)`**：返回当前稳定状态；d 为 NULL 时返回 false。

## 例子（threshold = 3，初始松开）

| tick | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| raw | 1 | 1 | 0 | 1 | 1 | 1 |
| 返回 | NONE | NONE | NONE | NONE | NONE | PRESS |

第 3 个 tick 出现抖动，计数清零，所以要到第 6 个 tick 才连续满 3 次。
