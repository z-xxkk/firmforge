#ifndef FLASH_KV_H
#define FLASH_KV_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define KV_SECTOR_SIZE    1024u
#define KV_HEADER_SIZE    4u       /* 扇区头：'K' 'V' + 2 字节序号（低字节在前） */
#define KV_ENTRY_HEADER   6u       /* 条目头：id、长度、CRC，各 2 字节，低字节在前 */
#define KV_MAX_VALUE      64u
#define KV_ID_INVALID     0xFFFFu

typedef enum {
    KV_OK = 0,
    KV_ERR_PARAM,            /* 参数错误 */
    KV_ERR_NOT_MOUNTED,      /* 还没有挂载成功 */
    KV_ERR_NOT_FOUND,        /* 没有这个 id，或已删除 */
    KV_ERR_BUF_TOO_SMALL,    /* 读缓冲区放不下，*len 给出实际长度 */
    KV_ERR_NO_SPACE,         /* 整理之后仍然放不下 */
    KV_ERR_FLASH             /* Flash 读写擦失败 */
} kv_status_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    bool mounted;
    uint8_t active;          /* 正在使用的扇区：0 或 1 */
    uint16_t seq;            /* 正在使用的扇区的序号 */
    uint32_t write_off;      /* 正在使用的扇区里下一条写在哪（扇区内偏移） */
} kv_t;

/* CRC-16/CCITT-FALSE：多项式 1021h，初值 FFFFh，不反射，结果不异或（"123456789" → 29B1h） */
uint16_t    kv_crc16(const uint8_t *data, size_t len, uint16_t crc);

kv_status_t kv_mount(kv_t *kv);
kv_status_t kv_write(kv_t *kv, uint16_t id, const uint8_t *data, size_t len);
kv_status_t kv_read(kv_t *kv, uint16_t id, uint8_t *buf, size_t cap, size_t *len);
kv_status_t kv_delete(kv_t *kv, uint16_t id);
size_t      kv_free_space(const kv_t *kv);

#endif /* FLASH_KV_H */
