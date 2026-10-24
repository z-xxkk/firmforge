/* median_filter 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "median_filter.h"

static median_filter_t f;

void setUp(void)
{
    memset(&f, 0x5A, sizeof f);
    median_filter_init(&f);
}

void tearDown(void) {}

static int16_t push(int16_t v)
{
    int16_t out = 0x1234;
    TEST_ASSERT_TRUE(median_filter_push(&f, v, &out));
    return out;
}

/* 测试内的对照实现：直接对最近 n 个读数排序 */
static int16_t ref_median(const int16_t *win, unsigned n)
{
    int16_t s[MEDIAN_FILTER_WINDOW];
    for (unsigned i = 0; i < n; i++) {
        s[i] = win[i];
    }
    for (unsigned i = 1; i < n; i++) {
        for (unsigned j = i; j > 0 && s[j - 1] > s[j]; j--) {
            int16_t t = s[j];
            s[j] = s[j - 1];
            s[j - 1] = t;
        }
    }
    if (n % 2u == 1u) {
        return s[n / 2u];
    }
    return (int16_t)(((int32_t)s[n / 2u - 1u] + (int32_t)s[n / 2u]) / 2);
}

static void test_init_is_empty(void)
{
    int16_t out = 77;
    TEST_ASSERT_EQUAL_UINT8(0, median_filter_count(&f));
    TEST_ASSERT_FALSE(median_filter_get(&f, &out));
    TEST_ASSERT_EQUAL_INT16(77, out);
}

static void test_single_sample(void)
{
    TEST_ASSERT_EQUAL_INT16(-42, push(-42));
    TEST_ASSERT_EQUAL_UINT8(1, median_filter_count(&f));
}

static void test_two_samples_average(void)
{
    push(10);
    TEST_ASSERT_EQUAL_INT16(15, push(20));
}

static void test_even_average_truncates_toward_zero(void)
{
    push(-3);
    TEST_ASSERT_EQUAL_INT16(-1, push(0));      /* -1.5 向零取整为 -1 */
    median_filter_reset(&f);
    push(3);
    TEST_ASSERT_EQUAL_INT16(3, push(4));       /* 3.5 向零取整为 3 */
}

static void test_three_samples(void)
{
    push(9);
    push(1);
    TEST_ASSERT_EQUAL_INT16(5, push(5));
}

static void test_four_samples_average_of_middle(void)
{
    push(1);
    push(9);
    push(3);
    TEST_ASSERT_EQUAL_INT16(5, push(7));       /* 1 3 7 9 → (3+7)/2 */
}

static void test_five_samples(void)
{
    push(50);
    push(10);
    push(40);
    push(20);
    TEST_ASSERT_EQUAL_INT16(30, push(30));
    TEST_ASSERT_EQUAL_UINT8(5, median_filter_count(&f));
}

static void test_spec_example(void)
{
    TEST_ASSERT_EQUAL_INT16(10, push(10));
    TEST_ASSERT_EQUAL_INT16(15, push(20));
    TEST_ASSERT_EQUAL_INT16(20, push(1000));
    TEST_ASSERT_EQUAL_INT16(25, push(30));
    TEST_ASSERT_EQUAL_INT16(30, push(40));
    TEST_ASSERT_EQUAL_INT16(40, push(50));
}

static void test_window_drops_oldest(void)
{
    push(9);
    push(8);
    push(7);
    push(6);
    TEST_ASSERT_EQUAL_INT16(7, push(5));       /* 窗口 9 8 7 6 5 */
    TEST_ASSERT_EQUAL_INT16(6, push(1));       /* 丢掉最早的 9：8 7 6 5 1 */
    TEST_ASSERT_EQUAL_INT16(5, push(1));       /* 丢掉 8：7 6 5 1 1 */
    TEST_ASSERT_EQUAL_UINT8(5, median_filter_count(&f));
}

static void test_spike_rejected(void)
{
    push(100);
    push(101);
    TEST_ASSERT_EQUAL_INT16(101, push(30000));
    TEST_ASSERT_EQUAL_INT16(101, push(102));
    TEST_ASSERT_EQUAL_INT16(101, push(99));
}

static void test_duplicates(void)
{
    push(3);
    push(3);
    push(3);
    push(1);
    TEST_ASSERT_EQUAL_INT16(3, push(9));
}

static void test_extremes_no_overflow(void)
{
    push(32767);
    TEST_ASSERT_EQUAL_INT16(32767, push(32767));
    median_filter_reset(&f);
    push(-32768);
    TEST_ASSERT_EQUAL_INT16(-32768, push(-32768));
    median_filter_reset(&f);
    push(-32768);
    TEST_ASSERT_EQUAL_INT16(0, push(32767));   /* -0.5 向零取整为 0 */
}

static void test_get_matches_last_push_and_keeps_window(void)
{
    int16_t out = 0;
    push(5);
    push(1);
    int16_t last = push(4);
    TEST_ASSERT_TRUE(median_filter_get(&f, &out));
    TEST_ASSERT_EQUAL_INT16(last, out);
    TEST_ASSERT_TRUE(median_filter_get(&f, &out));
    TEST_ASSERT_EQUAL_INT16(last, out);
    TEST_ASSERT_EQUAL_UINT8(3, median_filter_count(&f));
    TEST_ASSERT_EQUAL_INT16(4, push(8));       /* 窗口仍是 5 1 4 再加 8 */
}

static void test_reset_clears(void)
{
    int16_t out = 0;
    push(1);
    push(2);
    median_filter_reset(&f);
    TEST_ASSERT_EQUAL_UINT8(0, median_filter_count(&f));
    TEST_ASSERT_FALSE(median_filter_get(&f, &out));
    TEST_ASSERT_EQUAL_INT16(500, push(500));
}

static void test_null_arguments(void)
{
    int16_t out = 9;
    median_filter_init(NULL);
    median_filter_reset(NULL);
    TEST_ASSERT_FALSE(median_filter_push(NULL, 1, &out));
    TEST_ASSERT_FALSE(median_filter_get(NULL, &out));
    TEST_ASSERT_EQUAL_UINT8(0, median_filter_count(NULL));
    TEST_ASSERT_EQUAL_INT16(9, out);
    push(4);
    TEST_ASSERT_FALSE(median_filter_push(&f, 100, NULL));
    TEST_ASSERT_EQUAL_UINT8(1, median_filter_count(&f));      /* 窗口不变 */
    TEST_ASSERT_FALSE(median_filter_get(&f, NULL));
    TEST_ASSERT_TRUE(median_filter_get(&f, &out));
    TEST_ASSERT_EQUAL_INT16(4, out);
}

static void test_long_sequence_matches_reference(void)
{
    int16_t hist[300];
    uint32_t x = 12345u;
    for (unsigned i = 0; i < 300; i++) {
        x = x * 1103515245u + 12345u;
        hist[i] = (int16_t)(x >> 16);
        unsigned n = i + 1 < MEDIAN_FILTER_WINDOW ? i + 1 : MEDIAN_FILTER_WINDOW;
        int16_t expect = ref_median(&hist[i + 1 - n], n);
        TEST_ASSERT_EQUAL_INT16(expect, push(hist[i]));
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_is_empty);
    RUN_TEST(test_single_sample);
    RUN_TEST(test_two_samples_average);
    RUN_TEST(test_even_average_truncates_toward_zero);
    RUN_TEST(test_three_samples);
    RUN_TEST(test_four_samples_average_of_middle);
    RUN_TEST(test_five_samples);
    RUN_TEST(test_spec_example);
    RUN_TEST(test_window_drops_oldest);
    RUN_TEST(test_spike_rejected);
    RUN_TEST(test_duplicates);
    RUN_TEST(test_extremes_no_overflow);
    RUN_TEST(test_get_matches_last_push_and_keeps_window);
    RUN_TEST(test_reset_clears);
    RUN_TEST(test_null_arguments);
    RUN_TEST(test_long_sequence_matches_reference);
    return UNITY_END();
}
