/* button_events 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "button_events.h"

static button_t b;

void setUp(void)
{
    memset(&b, 0xA5, sizeof b);
}

void tearDown(void) {}

/* 按 pattern 逐 tick 喂入（'1' 按下，'0' 松开），把每个 tick 的事件写进 ev，返回 tick 数 */
static size_t feed(const char *pattern, button_event_t *ev)
{
    size_t n = strlen(pattern);
    for (size_t i = 0; i < n; i++) {
        ev[i] = button_update(&b, pattern[i] == '1');
    }
    return n;
}

/* 断言：只有第 at 个 tick（从 1 数起）出现事件 e，其他 tick 都是 NONE；at 为 0 表示全是 NONE */
static void expect_only(const char *pattern, size_t at, button_event_t e)
{
    button_event_t ev[4096];
    size_t n = feed(pattern, ev);
    for (size_t i = 0; i < n; i++) {
        if (i + 1 == at) {
            TEST_ASSERT_EQUAL_INT_MESSAGE(e, ev[i], "事件 tick 不对");
        } else {
            TEST_ASSERT_EQUAL_INT_MESSAGE(BUTTON_EVT_NONE, ev[i], "不该有事件");
        }
    }
}

static void test_spec_example_single_click(void)
{
    button_init(&b, 3, 5);
    expect_only("1100000", 6, BUTTON_EVT_SINGLE_CLICK);
}

static void test_single_click_waits_full_window(void)
{
    button_init(&b, 10, 50);
    /* 按 3 个 tick，第 4 个 tick 松开，松开后第 10 个 tick（第 14 个 tick）报单击 */
    expect_only("1110000000000000000", 14, BUTTON_EVT_SINGLE_CLICK);
}

static void test_double_click_reported_on_second_release(void)
{
    button_init(&b, 5, 20);
    expect_only("11001100000000", 7, BUTTON_EVT_DOUBLE_CLICK);
}

static void test_double_click_at_last_tick_of_window(void)
{
    button_init(&b, 3, 20);
    /* 第 3 个 tick 松开，松开后第 3 个 tick（第 6 个 tick）再按下，仍算连击 */
    expect_only("11000110000", 8, BUTTON_EVT_DOUBLE_CLICK);
}

static void test_second_press_after_window_is_new_click(void)
{
    button_init(&b, 3, 20);
    button_event_t ev[32];
    size_t n = feed("11000010000", ev);
    /* 第 3 个 tick 松开，第 4–6 个 tick 没按 → 第 6 个 tick 报单击；第 7 个 tick 再按下是新一轮 */
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_SINGLE_CLICK, ev[5]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[6]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_SINGLE_CLICK, ev[n - 1]);   /* 第 8 个 tick 松开 → 第 11 个 tick */
}

static void test_window_counted_from_release_not_press(void)
{
    button_init(&b, 4, 50);
    /* 按住 3 个 tick，第 4 个 tick 松开，第 7 个 tick 再按下（松开后第 3 个 tick，在窗口内），
       第 9 个 tick 第二次松开 → 双击。若从按下计时，窗口在第二次按下前就过期了 */
    expect_only("11100011000000", 9, BUTTON_EVT_DOUBLE_CLICK);
}

static void test_long_press_start_when_reaching_threshold(void)
{
    button_init(&b, 3, 5);
    expect_only("11111111", 5, BUTTON_EVT_LONG_PRESS_START);
}

static void test_long_press_end_on_release_and_no_click(void)
{
    button_init(&b, 3, 4);
    button_event_t ev[32];
    size_t n = feed("111111000000000", ev);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_START, ev[3]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_END, ev[6]);
    for (size_t i = 0; i < n; i++) {
        if (i != 3 && i != 6) {
            TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[i]);
        }
    }
}

static void test_press_just_below_threshold_is_click(void)
{
    button_init(&b, 3, 5);
    expect_only("11110000", 8, BUTTON_EVT_SINGLE_CLICK);
}

static void test_second_press_becomes_long_drops_click(void)
{
    button_init(&b, 5, 4);
    button_event_t ev[32];
    size_t n = feed("1001111100000000", ev);
    /* 第 2 个 tick 松开；第 4 个 tick 再按下，按到第 7 个 tick 满 4 个 → 长按开始；第 9 个 tick 松开 */
    for (size_t i = 0; i < n; i++) {
        if (i == 6) {
            TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_START, ev[i]);
        } else if (i == 8) {
            TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_END, ev[i]);
        } else {
            TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[i]);        /* 没有单击 */
        }
    }
}

static void test_third_press_starts_new_round(void)
{
    button_init(&b, 3, 20);
    button_event_t ev[32];
    feed("1010100000", ev);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_DOUBLE_CLICK, ev[3]);     /* 第 4 个 tick 第二次松开 */
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_SINGLE_CLICK, ev[8]);     /* 第 6 个 tick 松开 → 第 9 个 tick */
}

static void test_idle_never_reports(void)
{
    button_init(&b, 3, 5);
    char idle[200];
    memset(idle, '0', sizeof idle - 1);
    idle[sizeof idle - 1] = '\0';
    expect_only(idle, 0, BUTTON_EVT_NONE);
}

static void test_defaults_when_zero(void)
{
    button_init(&b, 0, 0);
    char p[1300];
    /* 按住 799 个 tick 不到长按；第 800 个 tick 松开，松开后第 400 个 tick（第 1200 个 tick）报单击 */
    memset(p, '1', 799);
    memset(p + 799, '0', 401);
    p[1200] = '\0';
    expect_only(p, 1200, BUTTON_EVT_SINGLE_CLICK);
    button_init(&b, 0, 0);
    memset(p, '1', 800);
    p[800] = '\0';
    expect_only(p, 800, BUTTON_EVT_LONG_PRESS_START);
}

static void test_press_ticks_one(void)
{
    button_init(&b, 3, 1);
    button_event_t ev[8];
    feed("11100", ev);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_START, ev[0]);   /* 按下的第 1 个 tick 就达到阈值 */
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[1]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[2]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_LONG_PRESS_END, ev[3]);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, ev[4]);
}

static void test_repeated_double_clicks(void)
{
    button_init(&b, 3, 20);
    for (int r = 0; r < 5; r++) {
        expect_only("1010000", 4, BUTTON_EVT_DOUBLE_CLICK);
    }
}

static void test_reinit_resets_state(void)
{
    button_init(&b, 3, 20);
    button_event_t ev[8];
    feed("10", ev);                       /* 正在等双击 */
    button_init(&b, 3, 20);
    expect_only("000000", 0, BUTTON_EVT_NONE);
}

static void test_null_arguments(void)
{
    button_init(NULL, 3, 5);
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, button_update(NULL, true));
    TEST_ASSERT_EQUAL_INT(BUTTON_EVT_NONE, button_update(NULL, false));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_spec_example_single_click);
    RUN_TEST(test_single_click_waits_full_window);
    RUN_TEST(test_double_click_reported_on_second_release);
    RUN_TEST(test_double_click_at_last_tick_of_window);
    RUN_TEST(test_second_press_after_window_is_new_click);
    RUN_TEST(test_window_counted_from_release_not_press);
    RUN_TEST(test_long_press_start_when_reaching_threshold);
    RUN_TEST(test_long_press_end_on_release_and_no_click);
    RUN_TEST(test_press_just_below_threshold_is_click);
    RUN_TEST(test_second_press_becomes_long_drops_click);
    RUN_TEST(test_third_press_starts_new_round);
    RUN_TEST(test_idle_never_reports);
    RUN_TEST(test_defaults_when_zero);
    RUN_TEST(test_press_ticks_one);
    RUN_TEST(test_repeated_double_clicks);
    RUN_TEST(test_reinit_resets_state);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
