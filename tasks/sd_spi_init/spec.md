# SD 卡 SPI 模式初始化 sd_spi_init

实现 `include/sd_card.h` 中声明的全部函数：通过 SPI 初始化一张 SD 卡、识别它的类型，并读出 512 字节的数据块。

## 约束

- 不能使用动态内存、标准输入输出函数，不能用递归。
- 只能通过 `include/hal_sd_spi.h` 里的两个函数访问卡，不能自己实现它们（由使用方提供；写单元测试时请在 `tests/` 里自己写一个模拟的 SD 卡）。
- 两个头文件都由任务锁定，不能修改。

## SPI 接口

- `hal_sd_select(true)` 拉低片选（选中卡），`hal_sd_select(false)` 拉高片选。
- `hal_sd_xfer(out)`：发出一个字节的同时收回一个字节。想读数据时发 FFh。

## 资料（摘自 ChaN《How to Use MMC/SDC》与 Linux 内核 SD 驱动）

**命令格式**：6 字节——`40h + 命令号`、4 字节参数（**高字节在前**）、`(CRC7 << 1) | 1`。CRC7 的多项式是 X^7 + X^3 + 1，初值 0，对前 5 字节从最高位开始算。核对用：CMD0（参数 0）的最后一个字节是 95h，CMD8（参数 000001AAh）的是 87h。

**回应**：命令发完后，一直发 FFh 并读，读到**最高位为 0** 的字节就是回应 R1。R1 各位：bit0 空闲、bit1 擦除复位、bit2 非法命令、bit3 CRC 错、bit4 擦除序列错、bit5 地址错、bit6 参数错。CMD8 和 CMD58 的回应在 R1 之后还有 4 字节（高字节在前）。

**用到的命令**

| 命令 | 参数 | 作用 |
|---|---|---|
| CMD0 | 0 | 复位，进入 SPI 模式；回应 01h |
| CMD8 | 000001AAh | 检查电压；2.0 版卡回应 R1 = 01h，后 4 字节的最低 12 位回显 1AAh；1.x 版卡回"非法命令" |
| CMD55 | 0 | 下一条是"应用命令" |
| ACMD41 | 2.0 版卡 40000000h（HCS 位），1.x 版卡 0 | 开始初始化；回应 01h 表示还在初始化，00h 表示就绪。必须紧跟在 CMD55 后面 |
| CMD58 | 0 | 读 OCR；后 4 字节第 1 字节的 bit6（OCR 的 bit30，CCS）为 1 是大容量卡 |
| CMD16 | 512 | 把块长度设为 512 |
| CMD17 | 地址 | 读一个块：大容量卡给块号，其他卡给字节地址（块号 × 512） |

**读块的数据**：CMD17 回应 00h 之后，一直读，直到读到数据起始标志 FEh，接着是 512 字节数据和 2 字节 CRC16。如果读到的是 `0000xxxx` 形式的字节，那是错误标志。

## 函数行为

**`sd_crc7(data, len)`**：按上面的算法返回 7 位 CRC；data 为 NULL 返回 0。

**发每一条命令的步骤（所有命令都一样）**
1. 拉低片选，发 1 个 FFh；
2. 发 6 字节命令，**每条命令都带正确的 CRC7**；
3. 最多读 `SD_RESPONSE_POLLS`（8）个字节等回应；
4. CMD8、CMD58 收到回应后再读 4 字节；
5. 拉高片选，再发 1 个 FFh（读块时在数据读完之后才做这一步）。

8 个字节内没有回应：CMD0 时按下面的重试规则处理；其他命令返回 `SD_ERR_NO_RESPONSE`。

**`sd_init(card)`**
1. card 为 NULL：返回 `SD_ERR_PARAM`。
2. 片选拉高，发 `SD_INIT_CLOCK_BYTES`（10）个 FFh。
3. CMD0，回应不是 01h（包括没有回应）就重试，最多 `SD_CMD0_TRIES`（10）次；仍不是 01h 返回 `SD_ERR_NO_CARD`。
4. CMD8：回应带"非法命令"位 → 当作 1.x 版卡；回应 01h 且回显正确（4 字节中第 3 字节低 4 位为 1、第 4 字节为 AAh）→ 2.0 版卡；回应 01h 但回显不对 → `SD_ERR_VOLTAGE`；其他回应 → `SD_ERR_CMD`。
5. 反复 CMD55 + ACMD41，最多 `SD_ACMD41_TRIES`（1000）轮，直到 ACMD41 回应 00h：
   - CMD55 或 ACMD41 的回应除空闲位外还有其他位：1.x 版卡且带"非法命令"位 → 老式 MMC 卡，返回 `SD_ERR_UNSUPPORTED`（CMD55 回应已经有错误位时不再发 ACMD41）；否则返回 `SD_ERR_CMD`；
   - 1000 轮仍未就绪：返回 `SD_ERR_TIMEOUT`。
6. 2.0 版卡发 CMD58：回应不是 00h 返回 `SD_ERR_CMD`；CCS 为 1 是 `SD_TYPE_V2_HC`，否则是 `SD_TYPE_V2_SC`。1.x 版卡是 `SD_TYPE_V1`，不发 CMD58。
7. 不是大容量卡时发 CMD16（512），回应不是 00h 返回 `SD_ERR_CMD`。
8. 就绪，返回 `SD_OK`。任何一步失败，卡都处于未就绪状态（类型 `SD_TYPE_NONE`）。

**`sd_type(card)`**：返回卡的类型；card 为 NULL 或未就绪返回 `SD_TYPE_NONE`。

**`sd_read_block(card, block, buf)`**
- card、buf 为 NULL，或卡未就绪：返回 `SD_ERR_PARAM`，不碰总线。
- 发 CMD17（大容量卡参数 = block，其他卡参数 = block × 512），回应不是 00h 返回 `SD_ERR_CMD`。
- 最多读 `SD_TOKEN_POLLS`（1000）个字节等 FEh；读到 FEh 就读 512 字节到 buf，再读掉 2 字节 CRC（SPI 模式下不校验），返回 `SD_OK`；读到错误标志或等满 1000 个字节都没有 FEh，返回 `SD_ERR_READ`。
- 无论成功失败，最后都拉高片选并发 1 个 FFh。
