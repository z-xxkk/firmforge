/* mem_pool 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "mem_pool.h"
#include "unity.h"

#define A MEM_POOL_ALIGN

static mem_pool_t pool;
static union { void *align; uint8_t b[8192]; } mem;          /* 按指针对齐的缓冲区 */
static union { void *align; uint8_t b[256]; } other;

void setUp(void) { memset(&pool, 0xA5, sizeof pool); }
void tearDown(void) {}

static uint8_t *base(void) { return mem.b; }

static void test_alloc_order_lifo(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 4 * 4 * A, 4 * A));
    TEST_ASSERT_EQUAL_UINT(4, mem_pool_capacity(&pool));
    uint8_t *p0 = mem_pool_alloc(&pool), *p1 = mem_pool_alloc(&pool);
    TEST_ASSERT_EQUAL_PTR(base(), p0);
    TEST_ASSERT_EQUAL_PTR(base() + 4 * A, p1);
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, p0));
    TEST_ASSERT_EQUAL_PTR(p0, mem_pool_alloc(&pool));          /* 刚释放的最先返回 */
    TEST_ASSERT_EQUAL_PTR(base() + 8 * A, mem_pool_alloc(&pool));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, p1));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, p0));
    TEST_ASSERT_EQUAL_PTR(p0, mem_pool_alloc(&pool));
    TEST_ASSERT_EQUAL_PTR(p1, mem_pool_alloc(&pool));
}

static void test_exhaustion_returns_null(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 3 * 2 * A + A, 2 * A));   /* 边角不用 */
    TEST_ASSERT_EQUAL_UINT(3, mem_pool_capacity(&pool));
    TEST_ASSERT_NOT_NULL(mem_pool_alloc(&pool));
    TEST_ASSERT_NOT_NULL(mem_pool_alloc(&pool));
    TEST_ASSERT_NOT_NULL(mem_pool_alloc(&pool));
    TEST_ASSERT_NULL(mem_pool_alloc(&pool));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_available(&pool));
    TEST_ASSERT_EQUAL_UINT(3, mem_pool_used(&pool));
}

static void test_block_size_equal_to_pointer(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 10 * A, A));
    for (int i = 0; i < 10; i++) {
        TEST_ASSERT_EQUAL_PTR(base() + (size_t)i * A, mem_pool_alloc(&pool));
    }
    TEST_ASSERT_NULL(mem_pool_alloc(&pool));
}

static void test_init_rejections(void)
{
    TEST_ASSERT_FALSE(mem_pool_init(NULL, mem.b, 64, 2 * A));
    TEST_ASSERT_FALSE(mem_pool_init(&pool, NULL, 64, 2 * A));
    TEST_ASSERT_FALSE(mem_pool_init(&pool, mem.b, 64, A / 2));               /* 放不下指针 */
    TEST_ASSERT_FALSE(mem_pool_init(&pool, mem.b, 64, A + 1));               /* 不是对齐单位的倍数 */
    TEST_ASSERT_FALSE(mem_pool_init(&pool, mem.b + 1, 64, 2 * A));           /* 缓冲区没对齐 */
    TEST_ASSERT_FALSE(mem_pool_init(&pool, mem.b, 2 * A - 1, 2 * A));        /* 0 块 */
    TEST_ASSERT_FALSE(mem_pool_init(&pool, mem.b, (MEM_POOL_MAX_BLOCKS + 1) * A, A));
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, MEM_POOL_MAX_BLOCKS * A, A));
    TEST_ASSERT_EQUAL_UINT(MEM_POOL_MAX_BLOCKS, mem_pool_capacity(&pool));
}

static void test_bad_frees_do_not_corrupt(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 4 * 4 * A, 4 * A));
    uint8_t *p = mem_pool_alloc(&pool);
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_FOREIGN, mem_pool_free(&pool, other.b));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_FOREIGN, mem_pool_free(&pool, base() + 16 * A));     /* 紧挨着末尾 */
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_FOREIGN, mem_pool_free(&pool, base() + 16 * A + A));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_MISALIGNED, mem_pool_free(&pool, p + 1));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_MISALIGNED, mem_pool_free(&pool, p + A));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_DOUBLE_FREE, mem_pool_free(&pool, base() + 4 * A)); /* 从没分配过 */
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, NULL));
    TEST_ASSERT_EQUAL_UINT(1, mem_pool_used(&pool));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, p));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_DOUBLE_FREE, mem_pool_free(&pool, p));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_used(&pool));
    /* 池没被破坏：4 块各分到一次，互不相同 */
    uint8_t *got[4];
    for (int i = 0; i < 4; i++) {
        got[i] = mem_pool_alloc(&pool);
        TEST_ASSERT_NOT_NULL(got[i]);
        for (int j = 0; j < i; j++) {
            TEST_ASSERT_TRUE_MESSAGE(got[i] != got[j], "同一块被分配了两次");
        }
    }
    TEST_ASSERT_NULL(mem_pool_alloc(&pool));
}

static void test_peak_and_reset(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 8 * 2 * A, 2 * A));
    void *p[5];
    for (int i = 0; i < 5; i++) {
        p[i] = mem_pool_alloc(&pool);
    }
    for (int i = 0; i < 3; i++) {
        mem_pool_free(&pool, p[i]);
    }
    TEST_ASSERT_EQUAL_UINT(2, mem_pool_used(&pool));
    TEST_ASSERT_EQUAL_UINT(5, mem_pool_peak(&pool));
    mem_pool_reset(&pool);
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_used(&pool));
    TEST_ASSERT_EQUAL_UINT(8, mem_pool_available(&pool));
    TEST_ASSERT_EQUAL_UINT(5, mem_pool_peak(&pool));                   /* 历史最大值保留 */
    TEST_ASSERT_EQUAL_PTR(base(), mem_pool_alloc(&pool));              /* 顺序同刚初始化 */
    TEST_ASSERT_EQUAL_INT(MEM_POOL_ERR_DOUBLE_FREE, mem_pool_free(&pool, p[4]));   /* 重置后旧指针都是空闲的 */
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 8 * 2 * A, 2 * A));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_peak(&pool));                   /* 重新初始化才清零 */
}

static void test_data_survives_in_allocated_blocks(void)
{
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, 16 * 8 * A, 8 * A));
    uint8_t *blk[16];
    for (int i = 0; i < 16; i++) {
        blk[i] = mem_pool_alloc(&pool);
        memset(blk[i], 0x40 + i, 8 * A);
    }
    for (int i = 0; i < 16; i += 2) {
        mem_pool_free(&pool, blk[i]);                                   /* 释放一半 */
    }
    for (int i = 1; i < 16; i += 2) {
        for (size_t k = 0; k < 8 * A; k++) {
            TEST_ASSERT_EQUAL_HEX8(0x40 + i, blk[i][k]);                 /* 没释放的块内容不变 */
        }
    }
}

static void test_random_stress_against_ledger(void)
{
    enum { N = 100 };
    const size_t bs = 3 * A;
    static uint8_t *held[N];
    size_t nheld = 0, peak = 0;
    unsigned seed = 2024;
    TEST_ASSERT_TRUE(mem_pool_init(&pool, mem.b, N * bs, bs));
    for (int it = 0; it < 20000; it++) {
        seed = seed * 1664525u + 1013904223u;
        if ((seed >> 16) % 3u != 0u && nheld < N) {
            uint8_t *p = mem_pool_alloc(&pool);
            TEST_ASSERT_NOT_NULL(p);
            TEST_ASSERT_TRUE(p >= base() && p < base() + N * bs);
            TEST_ASSERT_EQUAL_UINT(0, (size_t)(p - base()) % bs);
            for (size_t j = 0; j < nheld; j++) {
                TEST_ASSERT_TRUE_MESSAGE(held[j] != p, "分出去的块重复了");
            }
            held[nheld++] = p;
            if (nheld > peak) peak = nheld;
        } else if (nheld > 0) {
            size_t k = (seed >> 8) % nheld;
            TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(&pool, held[k]));
            held[k] = held[--nheld];
        }
        if (nheld == N) {
            TEST_ASSERT_NULL(mem_pool_alloc(&pool));
        }
    }
    TEST_ASSERT_EQUAL_UINT(nheld, mem_pool_used(&pool));
    TEST_ASSERT_EQUAL_UINT(N - nheld, mem_pool_available(&pool));
    TEST_ASSERT_EQUAL_UINT(peak, mem_pool_peak(&pool));
}

static void test_null_pool(void)
{
    TEST_ASSERT_NULL(mem_pool_alloc(NULL));
    TEST_ASSERT_EQUAL_INT(MEM_POOL_OK, mem_pool_free(NULL, mem.b));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_used(NULL));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_available(NULL));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_peak(NULL));
    TEST_ASSERT_EQUAL_UINT(0, mem_pool_capacity(NULL));
    mem_pool_reset(NULL);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_alloc_order_lifo);
    RUN_TEST(test_exhaustion_returns_null);
    RUN_TEST(test_block_size_equal_to_pointer);
    RUN_TEST(test_init_rejections);
    RUN_TEST(test_bad_frees_do_not_corrupt);
    RUN_TEST(test_peak_and_reset);
    RUN_TEST(test_data_survives_in_allocated_blocks);
    RUN_TEST(test_random_stress_against_ledger);
    RUN_TEST(test_null_pool);
    return UNITY_END();
}
