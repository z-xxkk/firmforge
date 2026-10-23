/* Agent 风格的单元测试（样例） */
#include "unity.h"
#include "ringbuf.h"

static ringbuf_t rb;
static uint8_t storage[4];

void setUp(void) { (void)ringbuf_init(&rb, storage, sizeof storage); }

static void test_starts_empty(void)
{
    TEST_ASSERT_TRUE(ringbuf_is_empty(&rb));
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_free(&rb));
}

static void test_put_get(void)
{
    uint8_t out = 0;
    TEST_ASSERT_TRUE(ringbuf_put(&rb, 42));
    TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
    TEST_ASSERT_EQUAL_UINT8(42, out);
}

static void test_full(void)
{
    const uint8_t data[5] = {1, 2, 3, 4, 5};
    TEST_ASSERT_EQUAL_size_t(4, ringbuf_write(&rb, data, 5));
    TEST_ASSERT_TRUE(ringbuf_is_full(&rb));
    TEST_ASSERT_FALSE(ringbuf_put(&rb, 9));
}

static void test_read_more_than_available(void)
{
    const uint8_t data[2] = {7, 8};
    uint8_t out[4] = {0};
    TEST_ASSERT_EQUAL_size_t(2, ringbuf_write(&rb, data, 2));
    TEST_ASSERT_EQUAL_size_t(2, ringbuf_read(&rb, out, 4));
    TEST_ASSERT_EQUAL_UINT8_ARRAY(data, out, 2);
}

static void test_wrap(void)
{
    uint8_t out = 0;
    for (uint8_t i = 0; i < 10; i++) {
        TEST_ASSERT_TRUE(ringbuf_put(&rb, i));
        TEST_ASSERT_TRUE(ringbuf_get(&rb, &out));
        TEST_ASSERT_EQUAL_UINT8(i, out);
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_starts_empty);
    RUN_TEST(test_put_get);
    RUN_TEST(test_full);
    RUN_TEST(test_read_more_than_available);
    RUN_TEST(test_wrap);
    return UNITY_END();
}
