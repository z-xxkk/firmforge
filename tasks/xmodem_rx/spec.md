# XMODEM 接收端 xmodem_rx

实现 `include/xmodem_rx.h` 中声明的全部函数：按 XMODEM 协议（含 CRC-16 扩展和 1024 字节块扩展）接收一个文件，存进调用方提供的缓冲区。

## 约束

- 不能使用动态内存、标准输入输出函数，不能用递归。
- 头文件由任务锁定，不能修改。

## 用法：事件驱动

模块**不自己等待、不自己计时**。调用方：

1. 调用 `xmodem_rx_init` 绑定接收缓冲区；
2. 调用 `xmodem_rx_start`，把返回的回复发给对方；
3. 串口每收到一个字节就调用 `xmodem_rx_feed`；
4. 一段时间没收到数据就调用 `xmodem_rx_timeout`（协议建议：握手阶段 3 秒、等块开始 10 秒、块内每字节 1 秒，计时由调用方负责）；
5. `feed`、`timeout`、`start` 返回"该回复什么"，调用方照做：`XMODEM_REPLY_CAN` 表示连发 2 个 CAN（18h），`XMODEM_REPLY_NONE` 表示什么都不发；
6. `xmodem_rx_status` 不再是 `XMODEM_RUNNING` 时，接收结束。

## 协议要点（摘自 Chuck Forsberg 编《XMODEM/YMODEM Protocol Reference》）

| 内容 | 说明 |
|---|---|
| 控制字符 | SOH 01h、STX 02h、EOT 04h、ACK 06h、NAK 15h、CAN 18h、C 43h |
| 128 字节块 | SOH、块号、255 − 块号、128 字节数据、校验 |
| 1024 字节块 | STX、块号、255 − 块号、1024 字节数据、校验；两种块可以混着来，块号照常每块加 1 |
| 块号 | 第一块是 1，每块加 1，FFh 之后是 **00h**（不是 01h） |
| CRC 模式的校验 | CRC-16，生成多项式 X^16 + X^12 + X^5 + 1（1021h），初值 0，只算数据字节；2 字节，**高字节在前** |
| 校验和模式的校验 | 数据字节之和，丢掉进位，1 字节 |
| 块内容 | 数据里可以出现任何字节值（包括 01h、18h 等），不当作控制字符 |
| 最后一块 | 不会有短块，不足的部分由发送方填充 |

## 行为规则

**握手**
- `xmodem_rx_start` 返回 `XMODEM_REPLY_C`，请求 CRC 模式。
- 还没收到任何块头时超时：一共发 `XMODEM_C_TRIES`（4）次 C（含开始那次）；4 次 C 之后再超时，就切到校验和模式并回 NAK。握手阶段的这几次超时**不计入**错误次数；切到校验和模式之后、收到第一个块头之前的超时照常计错误并回 NAK。
- 在 CRC 模式下收到第一个块头（SOH 或 STX），之后都按 CRC 模式；切到校验和模式之后收到的块都按校验和模式。

**收块**（等块头时）
| 收到 | 应该怎样 |
|---|---|
| SOH / STX | 开始接收一块（之后依次是块号、反码、数据、校验） |
| EOT | 回 ACK，状态变为 `XMODEM_DONE` |
| CAN，紧接着又一个 CAN | 状态变为 `XMODEM_CANCELLED`，不回复 |
| CAN，下一个字节不是 CAN | 忽略那个 CAN，照常处理下一个字节 |
| 其他字节 | 忽略，不回复（等超时再 NAK） |

**一块收齐后**
| 情况 | 应该怎样 |
|---|---|
| 块号 + 反码 ≠ FFh，或校验不对 | 回 NAK，错误次数加 1 |
| 块号是期望的 | 数据追加进缓冲区，回 ACK，错误次数清零 |
| 块号是上一块的（已经收到过至少一块） | 对方没收到 ACK 而重发：回 ACK，丢掉数据 |
| 其他块号（包括还没收到任何块时来了块号 0） | 失步：回 CAN，状态变为 `XMODEM_ERR_SYNC` |
| 块号是期望的，但缓冲区剩余空间放不下这一块 | 回 CAN，状态变为 `XMODEM_ERR_OVERFLOW`，已收到的数据保留 |

**超时**
- 块收到一半时超时：丢掉这一块，回 NAK，错误次数加 1。
- 等块头时超时（握手阶段见上）：回 NAK，错误次数加 1。

**错误上限**
- 连续错误（校验错、反码错、超时）达到 `XMODEM_MAX_ERRORS`（10）次时，这一次不回 NAK，改回 CAN，状态变为 `XMODEM_ERR_RETRIES`。

**其他**
- `xmodem_rx_length` 返回已收到的字节数，按整块计（包含发送方的填充字节）。
- 状态不是 `XMODEM_RUNNING` 后，`feed`、`timeout`、`start` 都返回 `XMODEM_REPLY_NONE`，不再改变任何东西。
- `xmodem_rx_init`：x 为 NULL 返回 false；buf 为 NULL 或 cap 为 0 返回 false，状态为 `XMODEM_ERR_PARAM`；否则返回 true，状态为 `XMODEM_RUNNING`。
- 任何函数的 x 为 NULL：`feed`、`timeout`、`start` 返回 `XMODEM_REPLY_NONE`，`status` 返回 `XMODEM_ERR_PARAM`，`length` 返回 0。

## 例子（CRC 模式，3 个块，含两次线路干扰）

```
接收方                         发送方
start → C
                               SOH 01 FE 数据×128 CRC高 CRC低
ACK
                               SOH 02 FD 数据×128 坏的CRC
NAK
                               SOH 02 FD 数据×128 CRC高 CRC低
ACK                            （这个 ACK 被线路干扰，发送方没收到）
                               SOH 02 FD …（重发第 2 块）
ACK（重复块，丢掉数据）
                               SOH 03 FC 数据×128 CRC高 CRC低
ACK
                               EOT
ACK → 状态 XMODEM_DONE，长度 384
```
