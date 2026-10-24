# 按键事件识别 button_events

实现 `include/button_events.h` 中声明的全部函数，从按键的按下 / 松开状态里识别出**单击、双击、长按**。

## 约束

- 不能使用动态内存和标准输入输出函数，不能用递归，不使用回调函数。
- 状态结构体由调用方提供；字段由头文件给出，实现只能使用这些字段，头文件不能修改。
- 本模块**不做消抖**：调用方传入的已经是消抖后的稳定状态。

## 用法

1. `button_init(b, click_ticks, press_ticks)`：
   - `click_ticks`：双击窗口，松开后在这么多个 tick 内再次按下才算连击；
   - `press_ticks`：长按阈值，连续按住达到这么多个 tick 算长按；
   - 任一个为 0 时用默认值：`click_ticks = 400`，`press_ticks = 800`（按 1 ms 一个 tick）。
2. 调用方每个 tick 调用一次 `button_update(b, pressed)`，`pressed` 为 true 表示这个 tick 按键是按下的。函数返回这个 tick 产生的事件（每次最多一个）。

## 判定规则

把"按下后、松开前"连续按住的 tick 数记为按住时长；按下的那个 tick 算第 1 个。

| 情况 | 应该怎样 |
|---|---|
| 按住时长**达到** press_ticks | 在达到的那个 tick 返回 `BUTTON_EVT_LONG_PRESS_START` |
| 长按之后松开 | 在松开的那个 tick 返回 `BUTTON_EVT_LONG_PRESS_END`，**不报单击** |
| 短按（按住时长不到 press_ticks）后松开 | 松开的那个 tick 返回 NONE，开始等待 |
| 等待中：松开之后的第 1 到第 click_ticks 个 tick 里一直没有再按下 | 在松开后的**第 click_ticks 个 tick** 返回 `BUTTON_EVT_SINGLE_CLICK` |
| 等待中：在松开之后的第 1 到第 click_ticks 个 tick 里再次按下，然后短按松开 | 在**第二次松开的那个 tick** 返回 `BUTTON_EVT_DOUBLE_CLICK`，不再等待 |
| 第二次按下后一直按住，按住时长达到 press_ticks | 返回 `BUTTON_EVT_LONG_PRESS_START`，前面那次单击**丢弃不报**；之后松开返回 `BUTTON_EVT_LONG_PRESS_END` |
| 报出双击之后再按下 | 当作新一轮的第一次按下 |
| 其他所有 tick | 返回 `BUTTON_EVT_NONE` |
| b 为 NULL | `button_init` 什么都不做；`button_update` 返回 NONE |

## 例子（click_ticks = 3，press_ticks = 5）

| tick | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| pressed | 1 | 1 | 0 | 0 | 0 | 0 | 0 |
| 返回 | NONE | NONE | NONE（松开） | NONE | NONE | SINGLE_CLICK | NONE |

第 3 个 tick 松开，之后第 1、2、3 个 tick（即 tick 4、5、6）没有再按下，所以在 tick 6 报单击。
