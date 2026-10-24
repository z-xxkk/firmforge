#ifndef W25Q_H
#define W25Q_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define W25Q_CAPACITY        (8u * 1024u * 1024u)   /* 8 MB */
#define W25Q_PAGE_SIZE       256u
#define W25Q_SECTOR_SIZE     4096u
#define W25Q_MAX_BUSY_POLLS  100000u                /* 等待不忙时最多读状态寄存器的次数 */

typedef enum {
    W25Q_OK = 0,
    W25Q_ERR_PARAM,     /* 参数错误或驱动未就绪 */
    W25Q_ERR_BUS,       /* SPI 失败 */
    W25Q_ERR_ID,        /* 身份不是 W25Q64JV */
    W25Q_ERR_TIMEOUT    /* 等待不忙超时 */
} w25q_status_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    bool ready;
} w25q_t;

w25q_status_t w25q_init(w25q_t *dev);
w25q_status_t w25q_read(const w25q_t *dev, uint32_t addr, uint8_t *buf, size_t len);
w25q_status_t w25q_write(const w25q_t *dev, uint32_t addr, const uint8_t *data, size_t len);
w25q_status_t w25q_erase_sector(const w25q_t *dev, uint32_t addr);

#endif /* W25Q_H */
