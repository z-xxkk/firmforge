# 阶段 4 新增 10 道题：清单（草稿，待负责人审核）

起草：Claude Code，2026-10-25。

## 为什么换掉路线图附录 A 里剩下的题

阶段 3 评测里 b2 和 full 都是 10 题全过，题目对现在的模型偏简单，分不出高低（"天花板"）。
附录 A 剩下的 10 道（crc8_maxim、moving_average、bit_utils、slip 等）大多是难度 1–2，加进去只会让天花板更明显，
升级阶梯和经验库也没有机会起作用（它们只在 Agent 失败时才派上用场）。

所以新题按这三条挑：
1. **难度 3 为主**：状态多、边界多、有"不按规矩就会出错"的细节；
2. **规则有正式出处**：官方规范、数据手册或协议原始文档，先读原文再定规则；
3. **适合拆成多个模块**：让多 Agent 的分工有用武之地。

## 清单

| # | id | 类别 | 难度 | 要做什么 | 主要依据 | 批次 |
|---|---|---|---|---|---|---|
| 11 | xmodem_rx | 协议 | 3 | XMODEM 接收端：握手、CRC/校验和两种模式、128 与 1024 字节块、重复块、失步取消、重试上限 | Forsberg《XMODEM/YMODEM Protocol Reference》[1] | 第四批 |
| 12 | mavlink_parser | 协议 | 3 | 无人机常用的 MAVLink v1/v2 字节流解析：两种帧头、CRC-16/MCRF4XX + CRC_EXTRA、v2 的截断负载与签名、坏帧后重新同步 | mavlink.io 官方序列化文档 [2] 与 C 参考实现 [3] | 第四批 |
| 13 | onewire_ds18b20 | 驱动 | 3 | 1-Wire 总线上找出所有 DS18B20 温度传感器（ROM 搜索算法），逐个读温度，CRC8 校验 | Maxim 应用笔记 AN187 [4]、DS18B20 数据手册 [5] | 第四批 |
| 14 | isotp | 协议 | 3 | 汽车诊断用的 ISO-TP（CAN 上的分段传输）：单帧、首帧、连续帧、流控、序号检查 | ISO 15765-2 本身收费，只能依据 Linux 内核文档 [6]、Zephyr 文档 [7] 等二手资料 | 第五批 |
| 15 | sd_spi_init | 驱动 | 3 | SD 卡 SPI 模式初始化：CMD0、CMD8、ACMD41 循环、CMD58，CRC7，响应轮询上限，区分 SDSC / SDHC | SD 协会《Physical Layer Simplified Specification》（免费版）| 第五批 |
| 16 | at_parser | 状态机 | 3 | 4G/NB-IoT 模组的 AT 指令响应解析：OK / ERROR / +CME ERROR、信息行（+CSQ）、夹在中间的主动上报（URC） | 3GPP TS 27.007 [8]、ITU-T V.250 | 第五批 |
| 17 | pid_ctrl | 状态机 | 3 | 定点数 PID：积分限幅（抗饱和）、输出限幅、微分取测量值（避免设定值突变时冲击） | Beauregard《Improving the Beginner's PID》系列 [9]、Åström《Feedback Systems》 | 第六批 |
| 18 | stepper_ramp | 状态机 | 3 | 步进电机梯形加减速：实时算出每一步的定时器间隔，短距离时走三角形曲线 | Atmel 应用笔记 AVR446 [10] | 第六批 |
| 19 | mem_pool | 数据结构 | 3 | 固定块内存池（替代 malloc）：O(1) 分配释放、对齐、发现重复释放和野指针 | Zephyr 内存块（k_mem_slab）文档、FreeRTOS 文档 | 第六批 |
| 20 | flash_kv | 数据结构 | 3 | 在 Flash 上存键值对：追加写、每条记录带 CRC、最新的一条有效、写满后整理、写到一半断电也能恢复 | Zephyr NVS / ZMS 设计文档 [11] | 第六批 |

做完后 20 道题的分布：协议 6、状态机 6、数据结构 4、驱动 4；难度 1 的 3 道、2 的 3 道、3 的 14 道。

## 需要你知道的

- **isotp 和 flash_kv 的依据弱一些**：ISO 15765-2 原文要付费，只能用 Linux 内核、Zephyr 这类权威开源项目的文档；flash_kv 没有统一标准，规则里 ★ 会比较多。如果你觉得不妥，可以换成依据更硬的题（备选：LIN 总线帧解析、I2C EEPROM 驱动）。
- 每批照旧：先给你规则清单审核，确认后我写代码，再给你隐藏测试清单审核。
- 这份清单替换了路线图附录 A 的后 10 道题，你同意后我把它记进 DECISIONS 并改 ROADMAP。

## 参考来源

1. Chuck Forsberg 编，[XMODEM/YMODEM Protocol Reference](https://techheap.packetizer.com/communications/modems/xmodem-ymodem_reference.html)（本次读的是 1986-09-11 版的完整文本）
2. MAVLink，[Packet Serialization](https://mavlink.io/en/guide/serialization.html)
3. MAVLink，[c_library_v1 checksum.h](https://github.com/mavlink/c_library_v1/blob/master/checksum.h)、[minimal.xml（HEARTBEAT 定义）](https://github.com/mavlink/mavlink/blob/master/message_definitions/v1.0/minimal.xml)
4. Maxim，[Application Note 187: 1-Wire Search Algorithm](https://www.analog.com/media/en/technical-documentation/app-notes/1wire-search-algorithm.pdf)（2002-03-28）
5. Maxim，[DS18B20 数据手册](https://www.analog.com/media/en/technical-documentation/data-sheets/DS18B20.pdf)
6. Linux 内核文档，[ISO 15765-2 (ISO-TP)](https://docs.kernel.org/6.13/networking/iso15765-2.html)
7. Zephyr，[ISO-TP Transport Protocol](https://docs.zephyrproject.org/latest/services/connectivity/canbus/isotp.html)
8. 3GPP TS 27.007 第 8.5 节 +CSQ（[iTecSpec 镜像](https://itecspec.com/3gpp/27.007/s/8.5)）
9. Brett Beauregard，Improving the Beginner's PID（[Reset Windup 镜像](https://www.datsi.fi.upm.es/docencia/DMC/guia_pid/improving-the-beginners-pid-reset-windup.htm)、[Derivative Kick 镜像](https://www.datsi.fi.upm.es/docencia/DMC/guia_pid/improving-the-beginners-pid-derivative-kick)）
10. Atmel，[AVR446: Linear speed control of stepper motor](https://www.microchip.com/en-us/application-notes/an8017)
11. Zephyr，[Zephyr Memory Storage (ZMS)](https://docs.zephyrproject.org/latest/services/storage/zms/zms.html)
