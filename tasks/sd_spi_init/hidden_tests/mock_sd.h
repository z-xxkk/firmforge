/* 假 SD 卡：按 SPI 时序逐字节模拟。只给隐藏测试用。 */
#ifndef MOCK_SD_H
#define MOCK_SD_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    CARD_NONE,          /* 没插卡：永远读到 FFh */
    CARD_V2_HC,
    CARD_V2_SC,
    CARD_V1,
    CARD_MMC,
    CARD_BAD_VOLTAGE    /* CMD8 回显的电压位不对 */
} card_kind_t;

typedef struct {
    uint8_t index;
    uint32_t arg;
    bool crc_ok;
} mock_cmd_t;

#define MOCK_LOG_MAX 4096

void mock_card(card_kind_t kind);
void mock_set_resp_delay(unsigned bytes);        /* 回应前先出几个 FFh（0–8） */
void mock_set_busy_acmd41(unsigned times);       /* ACMD41 要回几次 01h 才就绪 */
void mock_set_cmd0_silent(unsigned times);       /* 前几次 CMD0 不回应 */
void mock_set_token_delay(unsigned bytes);       /* 读块时数据起始标志前的 FFh 个数 */
void mock_set_error_token(uint8_t token);        /* 读块时用这个错误标志代替数据（0 = 不用） */
uint8_t *mock_block(uint32_t block);             /* 卡上第 block 块的内容 */

extern mock_cmd_t mock_log[MOCK_LOG_MAX];
extern unsigned mock_log_count;
extern unsigned mock_clocks_before_first_select;   /* 第一次选中卡之前送了多少个字节的时钟 */
extern unsigned mock_bytes_while_deselected;
extern unsigned mock_xfers;
extern unsigned mock_selects;
extern bool mock_selected;
extern unsigned mock_cmd_while_busy;               /* 卡还在回应时主机就发了新命令 */

#endif /* MOCK_SD_H */
