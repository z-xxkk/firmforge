#include "mem_pool.h"

static void build_free_list(mem_pool_t *pool)
{
    pool->free_list = NULL;
    for (size_t i = pool->num_blocks; i > 0u; i--) {        /* 从最后一块往前串：先分配地址小的 */
        void *blk = pool->buf + (i - 1u) * pool->block_size;
        *(void **)blk = pool->free_list;
        pool->free_list = blk;
    }
    for (size_t i = 0; i < sizeof pool->in_use; i++) {
        pool->in_use[i] = 0;
    }
    pool->used = 0;
}

bool mem_pool_init(mem_pool_t *pool, void *buf, size_t buf_size, size_t block_size)
{
    if (pool == NULL || buf == NULL || block_size < sizeof(void *) || block_size % MEM_POOL_ALIGN != 0u
        || (uintptr_t)buf % MEM_POOL_ALIGN != 0u) {
        return false;
    }
    size_t n = buf_size / block_size;
    if (n == 0u || n > MEM_POOL_MAX_BLOCKS) {
        return false;
    }
    pool->buf = (uint8_t *)buf;
    pool->block_size = block_size;
    pool->num_blocks = n;
    pool->peak = 0;
    build_free_list(pool);
    return true;
}

void *mem_pool_alloc(mem_pool_t *pool)
{
    if (pool == NULL || pool->free_list == NULL) {
        return NULL;
    }
    void *blk = pool->free_list;
    pool->free_list = *(void **)blk;
    size_t idx = (size_t)((uint8_t *)blk - pool->buf) / pool->block_size;
    pool->in_use[idx / 8u] = (uint8_t)(pool->in_use[idx / 8u] | (1u << (idx % 8u)));
    pool->used++;
    if (pool->used > pool->peak) {
        pool->peak = pool->used;
    }
    return blk;
}

mem_pool_status_t mem_pool_free(mem_pool_t *pool, void *ptr)
{
    if (pool == NULL || ptr == NULL) {
        return MEM_POOL_OK;                        /* 与 free(NULL) 一样什么都不做 */
    }
    uintptr_t start = (uintptr_t)pool->buf;
    uintptr_t p = (uintptr_t)ptr;
    uintptr_t size = (uintptr_t)(pool->num_blocks * pool->block_size);
    if (p < start || p - start > size) {   /* 变异：范围检查差一 */
        return MEM_POOL_ERR_FOREIGN;
    }
    if ((p - start) % pool->block_size != 0u) {
        return MEM_POOL_ERR_MISALIGNED;
    }
    size_t idx = (size_t)(p - start) / pool->block_size;
    uint8_t bit = (uint8_t)(1u << (idx % 8u));
    if ((pool->in_use[idx / 8u] & bit) == 0u) {
        return MEM_POOL_ERR_DOUBLE_FREE;
    }
    pool->in_use[idx / 8u] = (uint8_t)(pool->in_use[idx / 8u] & (uint8_t)~bit);
    *(void **)ptr = pool->free_list;               /* 后进先出：刚释放的块下次最先分配 */
    pool->free_list = ptr;
    pool->used--;
    return MEM_POOL_OK;
}

void mem_pool_reset(mem_pool_t *pool)
{
    if (pool != NULL && pool->buf != NULL) {
        build_free_list(pool);                     /* 历史最大值保留 */
    }
}

size_t mem_pool_used(const mem_pool_t *pool)
{
    return pool == NULL ? 0u : pool->used;
}

size_t mem_pool_available(const mem_pool_t *pool)
{
    return pool == NULL ? 0u : pool->num_blocks - pool->used;
}

size_t mem_pool_peak(const mem_pool_t *pool)
{
    return pool == NULL ? 0u : pool->peak;
}

size_t mem_pool_capacity(const mem_pool_t *pool)
{
    return pool == NULL ? 0u : pool->num_blocks;
}
