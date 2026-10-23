/* ringbuf 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "ringbuf.h"

static ringbuf_t rb;

void setUp(void) { memset(&rb, 0xA5, sizeof rb); }
void tearDown(void) {}

static void test_init_rejects_null_rb(void)
{
    uint8_t storage[4];
    TEST_ASSERT_FALSE(ringbuf_init(NULL, storage, sizeof storage));
}

static void test_init_rejects_null_storage(void)
{
    TEST_ASSERT_FALSE(ringbuf_init(&rb, NULL, 4));
}

static void test_init_rejects_zero_capacity(void)
{
    uint8_t storage[4];
    TEST_ASSERT_FALSE(ringbuf_init(&rb, storage, 0));
}

static void test_init_gives_empty_buffer(void)
{
    uint8_t storage[7];
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_count(&rb));
    TEST_ASSERT_EQUAL_size_t(7, ringbuf_free(&rb));
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
    TEST_ASSERT_FALSE(ringbuf_is_full(&rb));
}

static void test_put_get_single_byte(void)
{
    uint8_t storage[4];
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 0x5A));
    TEST_ASSERT_EQUAL_size_t(1, ringbuf_count(&rb));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_HEX8(0x5A, out);
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
}

static void test_fifo_order(void)
{
    uint8_t storage[8];
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    for (uint8_t i = 1; i <= 5; i++) {
        TEST_ASSERT_TRUE(ringbuf_put(&rb, i));
    }
    for (uint8_t i = 1; i <= 5; i++) {
        TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
        TEST_ASSERT_EQUAL_UINT8(i, out);
    }
}

static void test_full_at_exact_capacity(void)
{
    uint8_t storage[4];
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    for (uint8_t i = 0; i < 3; i++) {
        TEST_ASSERT_TRUE(ringbuf_put(&rb, i));
    }
    TEST_ASSERT_FALSE(ringbuf_is_full(&rb));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 3));
    TEST_ASSERT_TRUE(ringbuf_is_full(&rb));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_count(&rb));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_free(&rb));
}

static void test_put_when_full_keeps_old_data(void)
{
    uint8_t storage[3];
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 10));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 20));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 30));
    TEST_ASSERT_FALSE(ringbuf_put(&rb, 40));
    TEST_ASSERT_EQUAL_size_t(3, ringbuf_count(&rb));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(10, out);
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(20, out);
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(30, out);
}

static void test_capacity_one(void)
{
    uint8_t storage[1];
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, 1));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 7));
    TEST_ASSERT_TRUE(ringbuf_is_full(&rb));
    TEST_ASSERT_FALSE(ringbuf_put(&rb, 8));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(7, out);
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 9));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(9, out);
}

static void test_get_when_empty_fails(void)
{
    uint8_t storage[4];
    uint8_t out = 0x33;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_FALSE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_HEX8(0x33, out);
}

static void test_get_null_out_keeps_data(void)
{
    uint8_t storage[4];
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 1));
    TEST_ASSERT_FALSE(ringbuf_get(&rb, NULL));
    TEST_ASSERT_EQUAL_size_t(1, ringbuf_count(&rb));
}

static void test_wraparound_non_power_of_two(void)
{
    uint8_t storage[5];   /* 恰好 5 字节，越界访问会被 ASan 抓到 */
    uint8_t out = 0;
    uint8_t next_in = 0, next_out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    for (int round = 0; round < 20; round++) {
        for (int k = 0; k < 3; k++) {
            TEST_ASSERT_TRUE(ringbuf_put(&rb, next_in++));
        }
        for (int k = 0; k < 3; k++) {
            TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
            TEST_ASSERT_EQUAL_UINT8(next_out++, out);
        }
    }
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
}

static void test_null_queries(void)
{
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_count(NULL));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_free(NULL));
    TEST_ASSERT_TRUE(ringbuf_is_empty(NULL));
    TEST_ASSERT_FALSE(ringbuf_is_full(NULL));
}

static void test_put_get_null_rb(void)
{
    uint8_t out = 0;
    TEST_ASSERT_FALSE(ringbuf_put(NULL, 1));
    TEST_ASSERT_FALSE(ringbuf_get(NULL, &out));
}

static void test_write_partial_when_space_short(void)
{
    uint8_t storage[4];
    const uint8_t data[6] = {1, 2, 3, 4, 5, 6};
    uint8_t out[6] = {0};
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_write(&rb, data, sizeof data));
    TEST_ASSERT_TRUE(ringbuf_is_full(&rb));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_read(&rb, out, sizeof out));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(data, out, 4);
}

static void test_read_partial_when_data_short(void)
{
    uint8_t storage[8];
    const uint8_t data[3] = {9, 8, 7};
    uint8_t out[5] = {0};
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_EQUAL_size_t(3, ringbuf_write(&rb, data, sizeof data));
    TEST_ASSERT_EQUAL_size_t(3, ringbuf_read(&rb, out, sizeof out));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(data, out, 3);
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
}

static void test_write_read_across_wrap(void)
{
    uint8_t storage[5];
    const uint8_t a[3] = {1, 2, 3};
    const uint8_t b[4] = {4, 5, 6, 7};
    const uint8_t expect[5] = {3, 4, 5, 6, 7};
    uint8_t out[5] = {0};
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_EQUAL_size_t(3, ringbuf_write(&rb, a, sizeof a));
    TEST_ASSERT_EQUAL_size_t(2, ringbuf_read(&rb, out, 2));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_write(&rb, b, sizeof b));
    TEST_ASSERT_TRUE(ringbuf_is_full(&rb));
    TEST_ASSERT_EQUAL_size_t(5, ringbuf_read(&rb, out, sizeof out));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(expect, out, 5);
}

static void test_write_read_null_and_zero_len(void)
{
    uint8_t storage[4];
    uint8_t buf[2] = {1, 2};
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_write(NULL, buf, 2));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_write(&rb, NULL, 2));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_write(&rb, buf, 0));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_count(&rb));
    TEST_ASSERT_EQUAL_size_t(2, ringbuf_write(&rb, buf, 2));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_read(NULL, buf, 2));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_read(&rb, NULL, 2));
    TEST_ASSERT_EQUAL_size_t(0, ringbuf_read(&rb, buf, 0));
    TEST_ASSERT_EQUAL_size_t(2, ringbuf_count(&rb));
}

static void test_clear_and_reuse(void)
{
    uint8_t storage[4];
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_init(&rb, storage, sizeof storage));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 1));
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 2));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    ringbuf_clear(&rb);
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_free(&rb));
    for (uint8_t i = 0; i < 4; i++) {
        TEST_ASSERT_TRUE(ringbuf_put(&rb, (uint8_t)(i + 50)));
    }
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(50, out);
    ringbuf_clear(NULL);   /* 不能崩溃 */
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_rejects_null_rb);
    RUN_TEST(test_init_rejects_null_storage);
    RUN_TEST(test_init_rejects_zero_capacity);
    RUN_TEST(test_init_gives_empty_buffer);
    RUN_TEST(test_put_get_single_byte);
    RUN_TEST(test_fifo_order);
    RUN_TEST(test_full_at_exact_capacity);
    RUN_TEST(test_put_when_full_keeps_old_data);
    RUN_TEST(test_capacity_one);
    RUN_TEST(test_get_when_empty_fails);
    RUN_TEST(test_get_null_out_keeps_data);
    RUN_TEST(test_wraparound_non_power_of_two);
    RUN_TEST(test_null_queries);
    RUN_TEST(test_put_get_null_rb);
    RUN_TEST(test_write_partial_when_space_short);
    RUN_TEST(test_read_partial_when_data_short);
    RUN_TEST(test_write_read_across_wrap);
    RUN_TEST(test_write_read_null_and_zero_len);
    RUN_TEST(test_clear_and_reuse);
    return UNITY_END();
}
