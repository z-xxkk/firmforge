#ifndef SD_CARD_H
#define SD_CARD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SD_BLOCK_SIZE        512u
#define SD_INIT_CLOCK_BYTES  10u      /* 上电时片选拉高发多少个 FFh（80 个时钟） */
#define SD_RESPONSE_POLLS    8u       /* 等回应最多读几个字节 */
#define SD_CMD0_TRIES        10u      /* CMD0 最多试几次 */
#define SD_ACMD41_TRIES      1000u    /* ACMD41 最多发几次 */
#define SD_TOKEN_POLLS       1000u    /* 读块时等数据起始标志最多读几个字节 */

typedef enum {
    SD_TYPE_NONE = 0,     /* 没有初始化成功 */
    SD_TYPE_V1,           /* 1.x 版卡（标准容量，字节地址） */
    SD_TYPE_V2_SC,        /* 2.0 版标准容量卡（字节地址） */
    SD_TYPE_V2_HC         /* 2.0 版大容量卡 SDHC/SDXC（块号寻址） */
} sd_type_t;

typedef enum {
    SD_OK = 0,
    SD_ERR_PARAM,         /* 参数错误或卡未就绪 */
    SD_ERR_NO_CARD,       /* CMD0 一直没有得到 01h */
    SD_ERR_NO_RESPONSE,   /* 其他命令在规定字节数内没有回应 */
    SD_ERR_VOLTAGE,       /* CMD8 的回显不对：电压不支持 */
    SD_ERR_UNSUPPORTED,   /* 老式 MMC 卡，本驱动不支持 */
    SD_ERR_TIMEOUT,       /* ACMD41 发满次数仍未就绪 */
    SD_ERR_CMD,           /* 命令回应里有错误位 */
    SD_ERR_READ           /* 读块时收到错误标志或等不到数据 */
} sd_status_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t type;         /* sd_type_t */
    bool ready;
} sd_card_t;

/* CRC7（多项式 X^7 + X^3 + 1，初值 0）：返回 7 位结果，命令最后一个字节 = (crc << 1) | 1 */
uint8_t     sd_crc7(const uint8_t *data, size_t len);

sd_status_t sd_init(sd_card_t *card);
sd_type_t   sd_type(const sd_card_t *card);
sd_status_t sd_read_block(const sd_card_t *card, uint32_t block, uint8_t buf[SD_BLOCK_SIZE]);

#endif /* SD_CARD_H */
