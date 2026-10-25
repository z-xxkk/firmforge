# AT 指令响应解析 at_parser

实现 `include/at_parser.h` 中声明的全部函数：解析 4G / NB-IoT 模组通过串口发回的 AT 指令响应，分出"信息文本""主动上报"和"命令结束"。

## 约束

- 不能使用动态内存、标准输入输出函数（包括 sscanf、printf），不能用递归。
- 不要依赖 `<string.h>` 以外的库函数；头文件由任务锁定，不能修改。

## 用法

1. `at_init(p)`；
2. 要发命令时调用 `at_begin(p, "AT+CSQ", "+CSQ")`：第二个参数是这条命令的**信息文本前缀**（没有前缀的命令传 "" 或 NULL），然后调用方自己把命令和 `\r` 发给模组；
3. 串口每收到一个字节就调用 `at_feed`，按返回的事件处理；
4. 命令迟迟不结束时，调用方计时后调用 `at_timeout`。

## 资料（摘自 ITU-T V.250 与 3GPP TS 27.007）

- 响应分两种：**信息文本**和**结果码**；结果码又分**最终**（命令结束）、**中间**和**主动上报**（与命令无关的事件，例如 RING）。
- 默认格式（V1）：信息文本是 `<CR><LF>文本<CR><LF>`，结果码是 `<CR><LF>结果<CR><LF>`。
- 最终结果码：OK、ERROR、NO CARRIER、NO DIALTONE、BUSY、NO ANSWER；打开 +CMEE 后，与模组功能有关的错误用 `+CME ERROR: <err>` 代替 ERROR（+CMEE=1 时 err 是数字，例如 10 = SIM not inserted；+CMEE=2 时是文字）。
- 扩展格式以 `+` 开头：`+名称: 值`。
- 模组默认开启回显：会把收到的命令原样发回来。
- `+CSQ: <rssi>,<ber>`：rssi 0 = −113 dBm 或更低，1 = −111 dBm，2–30 = −109…−53 dBm（每级 2 dBm），31 = −51 dBm 或更高，99 = 未知；ber 0–7，99 = 未知。
- 同一个前缀可能有两种身份：`+CREG:` 既是 `AT+CREG?` 的信息文本，也是网络状态变化时的主动上报。

## 行的切分

- 遇到 CR（0Dh）或 LF（0Ah）就结束一行；空行忽略。这样 `<CR><LF>` 和只发 LF 的模组都能处理。
- 一行最多 `AT_MAX_LINE`（128）个字符。超过时丢掉这一行直到行尾，在行尾返回 `AT_EVT_LINE_TOO_LONG`（不当作任何别的东西）。
- 以下"这一行"都指去掉行尾之后的内容，按**完全相同**比较（区分大小写）。

## 每一行怎么归类

**有命令在等结果时（`at_begin` 之后、命令结束之前）**，按顺序判断：

| 这一行 | 事件 | 命令是否结束 |
|---|---|---|
| `OK` | `AT_EVT_OK` | 结束 |
| `ERROR` | `AT_EVT_ERROR` | 结束 |
| 以 `+CME ERROR:` 开头 | `AT_EVT_CME_ERROR`；冒号后（跳过空格）全是数字时错误码就是这个数，否则（文字形式）错误码为 −1 | 结束 |
| `NO CARRIER` / `BUSY` / `NO ANSWER` / `NO DIALTONE` | 对应的 `AT_EVT_...` | 结束 |
| 与 `at_begin` 给的命令完全相同 | 回显，`AT_EVT_NONE` | 否 |
| 以"前缀 + 冒号"开头（前缀不为空） | `AT_EVT_INFO` | 否 |
| 以 `+` 开头（前缀不同），或 `RING` | `AT_EVT_URC`（夹在命令中间的主动上报） | 否 |
| 其他 | `AT_EVT_INFO`（例如 AT+CGSN 回的一串数字没有前缀） | 否 |

**没有命令在等时**：

| 这一行 | 事件 |
|---|---|
| 上表中的任何最终结果码（OK、ERROR、+CME ERROR、NO CARRIER 等） | 忽略，`AT_EVT_NONE`（多半是上一条命令超时后才到的残留） |
| 以 `+` 开头，或 `RING` | `AT_EVT_URC` |
| 其他 | 忽略，`AT_EVT_NONE` |

返回 `AT_EVT_INFO` 或 `AT_EVT_URC` 时，`at_line(p)` 返回这一行的内容（以 `\0` 结尾），保留到下一次返回 INFO 或 URC 为止。

## 函数行为

| 函数 | 正常行为 | 异常 / 边界 |
|---|---|---|
| `at_init(p)` | 清空所有状态，没有命令在等 | p 为 NULL 什么都不做 |
| `at_begin(p, command, prefix)` | 记下命令和前缀，进入"有命令在等"，返回 true | p 或 command 为 NULL、command 为空串或超过 `AT_MAX_COMMAND`、prefix 超过 `AT_MAX_PREFIX`、上一条命令还没结束：返回 false，什么都不改。prefix 为 NULL 当作 "" |
| `at_feed(p, byte)` | 见上 | p 为 NULL 返回 `AT_EVT_NONE` |
| `at_timeout(p)` | 有命令在等时结束它，返回 `AT_EVT_TIMEOUT` | 没有命令在等或 p 为 NULL：返回 `AT_EVT_NONE` |
| `at_busy(p)` | 有命令在等返回 true | p 为 NULL 返回 false |
| `at_line(p)` | 见上；还没有过返回 "" | p 为 NULL 返回 "" |
| `at_cme_error(p)` | 最近一次 +CME ERROR 的错误码；还没有过为 0 | p 为 NULL 返回 0 |
| `at_parse_csq(line, &rssi, &ber)` | 格式为 `+CSQ:`、若干空格、数字、`,`、数字，到此结束，且 rssi 为 0–31 或 99、ber 为 0–7 或 99：写出两个数，返回 true | 其他情况或任何指针为 NULL：返回 false，不修改输出 |
| `at_rssi_dbm(rssi, &dbm)` | rssi 为 0–31：dbm = −113 + 2 × rssi，返回 true | rssi 为 99 或超出范围、dbm 为 NULL：返回 false |

## 例子

```
调用方：at_begin(p, "AT+CSQ", "+CSQ")，发 "AT+CSQ\r"
模组：  AT+CSQ\r                  → NONE（回显）
        \r\n+CSQ: 20,99\r\n        → INFO，at_line = "+CSQ: 20,99"
        \r\nOK\r\n                 → OK，命令结束

（没有命令时）
模组：  \r\nRING\r\n               → URC，at_line = "RING"
        \r\n+CREG: 5\r\n           → URC

调用方：at_begin(p, "AT+CREG?", "+CREG")
模组：  \r\n+CREG: 0,1\r\n         → INFO（这时 +CREG 是信息文本）
        \r\n+CMTI: "SM",3\r\n      → URC（夹在中间的新短信通知）
        \r\n+CME ERROR: 10\r\n     → CME_ERROR，错误码 10，命令结束
```
