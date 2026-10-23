/* debounce 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "debounce.h"

static debounce_t d;

void setUp(void) { memset(&d, 0xA5, sizeof d); }
void tearDown(void) {}

/* 连续喂 n 个相同的原始输入，要求全部返回 NONE */
static void feed_none(bool raw, int n)
{
    for (int i = 0; i < n; i++) {
        TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, raw));
    }
}

static void test_init_released(void)
{
    debounce_init(&d, 3, false);
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
}

static void test_init_pressed(void)
{
    debounce_init(&d, 3, true);
    TEST_ASSERT_TRUE(debounce_is_pressed(&d));
}

static void test_null_arguments(void)
{
    debounce_init(NULL, 3, true);   /* 不能崩溃 */
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(NULL, true));
    TEST_ASSERT_FALSE(debounce_is_pressed(NULL));
}

static void test_press_on_exactly_threshold_tick(void)
{
    debounce_init(&d, 3, false);
    feed_none(true, 2);
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
    TEST_ASSERT_TRUE(debounce_is_pressed(&d));
}

static void test_release_on_exactly_threshold_tick(void)
{
    debounce_init(&d, 4, true);
    feed_none(false, 3);
    TEST_ASSERT_TRUE(debounce_is_pressed(&d));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_RELEASE, debounce_update(&d, false));
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
}

static void test_bounce_resets_counter(void)
{
    debounce_init(&d, 3, false);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, false));  /* 抖动 */
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
}

static void test_repeated_bounce_never_toggles(void)
{
    debounce_init(&d, 3, false);
    for (int i = 0; i < 50; i++) {
        TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
        TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
        TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, false));
    }
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
}

static void test_event_reported_once(void)
{
    debounce_init(&d, 2, false);
    feed_none(true, 1);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
    feed_none(true, 20);
    TEST_ASSERT_TRUE(debounce_is_pressed(&d));
}

static void test_counter_cleared_after_toggle(void)
{
    debounce_init(&d, 3, false);
    feed_none(true, 2);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
    feed_none(false, 2);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_RELEASE, debounce_update(&d, false));
}

static void test_threshold_zero_treated_as_one(void)
{
    debounce_init(&d, 0, false);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_RELEASE, debounce_update(&d, false));
}

static void test_threshold_one(void)
{
    debounce_init(&d, 1, true);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_NONE, debounce_update(&d, true));
    TEST_ASSERT_EQUAL_INT(BTN_EVT_RELEASE, debounce_update(&d, false));
    TEST_ASSERT_FALSE(debounce_is_pressed(&d));
}

static void test_stable_input_never_fires(void)
{
    debounce_init(&d, 5, false);
    feed_none(false, 100);
    debounce_init(&d, 5, true);
    feed_none(true, 100);
}

static void test_full_press_release_cycle(void)
{
    debounce_init(&d, 5, false);
    for (int cycle = 0; cycle < 3; cycle++) {
        feed_none(true, 4);
        TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
        feed_none(true, 10);
        feed_none(false, 4);
        TEST_ASSERT_EQUAL_INT(BTN_EVT_RELEASE, debounce_update(&d, false));
        feed_none(false, 10);
    }
}

static void test_large_threshold(void)
{
    debounce_init(&d, 1000, false);
    feed_none(true, 999);
    TEST_ASSERT_EQUAL_INT(BTN_EVT_PRESS, debounce_update(&d, true));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_released);
    RUN_TEST(test_init_pressed);
    RUN_TEST(test_null_arguments);
    RUN_TEST(test_press_on_exactly_threshold_tick);
    RUN_TEST(test_release_on_exactly_threshold_tick);
    RUN_TEST(test_bounce_resets_counter);
    RUN_TEST(test_repeated_bounce_never_toggles);
    RUN_TEST(test_event_reported_once);
    RUN_TEST(test_counter_cleared_after_toggle);
    RUN_TEST(test_threshold_zero_treated_as_one);
    RUN_TEST(test_threshold_one);
    RUN_TEST(test_stable_input_never_fires);
    RUN_TEST(test_full_press_release_cycle);
    RUN_TEST(test_large_threshold);
    return UNITY_END();
}
