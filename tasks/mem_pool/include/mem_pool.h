#ifndef MEM_POOL_H
#define MEM_POOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MEM_POOL_ALIGN       (sizeof(void *))   /* 对齐单位：STM32 上 4 字节，电脑上 8 字节 */
#define MEM_POOL_MAX_BLOCKS  256u

typedef enum {
    MEM_POOL_OK = 0,
    MEM_POOL_ERR_FOREIGN,     /* 指针不在本池的缓冲区内 */
    MEM_POOL_ERR_MISALIGNED,  /* 指针在缓冲区内，但不在某一块的起点 */
    MEM_POOL_ERR_DOUBLE_FREE  /* 这一块本来就是空闲的 */
} mem_pool_status_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t *buf;
    size_t block_size;
    size_t num_blocks;
    void *free_list;                              /* 空闲块链表：存在空闲块的开头 */
    size_t used;
    size_t peak;
    uint8_t in_use[MEM_POOL_MAX_BLOCKS / 8u];     /* 每块一位：1 = 已分配 */
} mem_pool_t;

bool              mem_pool_init(mem_pool_t *pool, void *buf, size_t buf_size, size_t block_size);
void             *mem_pool_alloc(mem_pool_t *pool);
mem_pool_status_t mem_pool_free(mem_pool_t *pool, void *ptr);
void              mem_pool_reset(mem_pool_t *pool);
size_t            mem_pool_used(const mem_pool_t *pool);
size_t            mem_pool_available(const mem_pool_t *pool);
size_t            mem_pool_peak(const mem_pool_t *pool);
size_t            mem_pool_capacity(const mem_pool_t *pool);

#endif /* MEM_POOL_H */
