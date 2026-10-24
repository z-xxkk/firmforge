/* soft_timer 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "soft_timer.h"

static soft_timer_t st;

void setUp(void)
{
    memset(&st, 0xA5, sizeof st);
    soft_timer_init(&st);
}

void tearDown(void) {}

/* 推进 n 次 tick，返回期间所有到期位的"或" */
static uint8_t ticks(unsigned n)
{
    uint8_t acc = 0;
    for (unsigned i = 0; i < n; i++) {
        acc = (uint8_t)(acc | soft_timer_tick(&st));
    }
    return acc;
}

static void test_init_all_stopped(void)
{
    for (uint8_t i = 0; i < SOFT_TIMER_COUNT; i++) {
        TEST_ASSERT_FALSE(soft_timer_is_running(&st, i));
        TEST_ASSERT_EQUAL_UINT32(0, soft_timer_remaining(&st, i));
    }
    TEST_ASSERT_EQUAL_HEX8(0, ticks(100));
}

static void test_oneshot_fires_on_period_tick_then_stops(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 2, 3, false));
    TEST_ASSERT_EQUAL_HEX8(0x00, soft_timer_tick(&st));
    TEST_ASSERT_EQUAL_HEX8(0x00, soft_timer_tick(&st));
    TEST_ASSERT_EQUAL_HEX8(0x04, soft_timer_tick(&st));
    TEST_ASSERT_FALSE(soft_timer_is_running(&st, 2));
    TEST_ASSERT_EQUAL_UINT32(0, soft_timer_remaining(&st, 2));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(20));
}

static void test_periodic_fires_every_period_without_drift(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 0, 3, true));
    for (unsigned t = 1; t <= 60; t++) {
        uint8_t fired = soft_timer_tick(&st);
        TEST_ASSERT_EQUAL_HEX8((t % 3u == 0u) ? 0x01 : 0x00, fired);
    }
    TEST_ASSERT_TRUE(soft_timer_is_running(&st, 0));
}

static void test_period_one_fires_every_tick(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 5, 1, true));
    for (int i = 0; i < 10; i++) {
        TEST_ASSERT_EQUAL_HEX8(0x20, soft_timer_tick(&st));
    }
}

static void test_simultaneous_expiry_reported_together(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 0, 4, false));
    TEST_ASSERT_TRUE(soft_timer_start(&st, 7, 4, false));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(3));
    TEST_ASSERT_EQUAL_HEX8(0x81, soft_timer_tick(&st));
}

static void test_interleaved_periods(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 1, 2, true));
    TEST_ASSERT_TRUE(soft_timer_start(&st, 3, 3, true));
    for (unsigned t = 1; t <= 12; t++) {
        uint8_t expect = 0;
        if (t % 2u == 0u) {
            expect |= 0x02;
        }
        if (t % 3u == 0u) {
            expect |= 0x08;
        }
        TEST_ASSERT_EQUAL_HEX8(expect, soft_timer_tick(&st));
    }
}

static void test_remaining_counts_down(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 4, 5, false));
    TEST_ASSERT_EQUAL_UINT32(5, soft_timer_remaining(&st, 4));
    soft_timer_tick(&st);
    TEST_ASSERT_EQUAL_UINT32(4, soft_timer_remaining(&st, 4));
    ticks(3);
    TEST_ASSERT_EQUAL_UINT32(1, soft_timer_remaining(&st, 4));
    TEST_ASSERT_EQUAL_HEX8(0x10, soft_timer_tick(&st));
}

static void test_periodic_remaining_after_reload(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 6, 4, true));
    ticks(4);
    TEST_ASSERT_EQUAL_UINT32(4, soft_timer_remaining(&st, 6));
}

static void test_stop_prevents_expiry(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 3, 5, true));
    ticks(4);
    TEST_ASSERT_TRUE(soft_timer_stop(&st, 3));
    TEST_ASSERT_FALSE(soft_timer_is_running(&st, 3));
    TEST_ASSERT_EQUAL_UINT32(0, soft_timer_remaining(&st, 3));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(50));
    TEST_ASSERT_TRUE(soft_timer_stop(&st, 3));     /* 停止已停止的定时器也返回 true */
}

static void test_restart_running_timer_counts_from_now(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 0, 5, false));
    ticks(3);
    TEST_ASSERT_TRUE(soft_timer_start(&st, 0, 5, false));     /* 剩 2 个 tick 时重启 */
    TEST_ASSERT_EQUAL_UINT32(5, soft_timer_remaining(&st, 0));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(4));
    TEST_ASSERT_EQUAL_HEX8(0x01, soft_timer_tick(&st));
}

static void test_restart_with_new_period_and_type(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 2, 10, false));
    ticks(5);
    TEST_ASSERT_TRUE(soft_timer_start(&st, 2, 2, true));
    for (unsigned t = 1; t <= 8; t++) {
        TEST_ASSERT_EQUAL_HEX8((t % 2u == 0u) ? 0x04 : 0x00, soft_timer_tick(&st));
    }
}

static void test_restart_after_oneshot_fired(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 1, 2, false));
    TEST_ASSERT_EQUAL_HEX8(0x02, ticks(2));
    TEST_ASSERT_TRUE(soft_timer_start(&st, 1, 2, false));
    TEST_ASSERT_EQUAL_HEX8(0x00, soft_timer_tick(&st));
    TEST_ASSERT_EQUAL_HEX8(0x02, soft_timer_tick(&st));
}

static void test_zero_period_rejected_without_side_effect(void)
{
    TEST_ASSERT_FALSE(soft_timer_start(&st, 0, 0, true));
    TEST_ASSERT_FALSE(soft_timer_is_running(&st, 0));
    TEST_ASSERT_TRUE(soft_timer_start(&st, 0, 3, true));
    soft_timer_tick(&st);
    TEST_ASSERT_FALSE(soft_timer_start(&st, 0, 0, false));   /* 运行中的定时器不受影响 */
    TEST_ASSERT_TRUE(soft_timer_is_running(&st, 0));
    TEST_ASSERT_EQUAL_UINT32(2, soft_timer_remaining(&st, 0));
}

static void test_invalid_id_rejected(void)
{
    TEST_ASSERT_FALSE(soft_timer_start(&st, 8, 5, true));
    TEST_ASSERT_FALSE(soft_timer_start(&st, 255, 5, true));
    TEST_ASSERT_FALSE(soft_timer_stop(&st, 8));
    TEST_ASSERT_FALSE(soft_timer_is_running(&st, 8));
    TEST_ASSERT_EQUAL_UINT32(0, soft_timer_remaining(&st, 200));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(10));
}

static void test_large_period(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 7, 100000u, false));
    TEST_ASSERT_EQUAL_HEX8(0x00, ticks(99999u));
    TEST_ASSERT_EQUAL_HEX8(0x80, soft_timer_tick(&st));
}

static void test_max_period_accepted(void)
{
    TEST_ASSERT_TRUE(soft_timer_start(&st, 3, 0xFFFFFFFFu, true));
    TEST_ASSERT_EQUAL_UINT32(0xFFFFFFFFu, soft_timer_remaining(&st, 3));
    soft_timer_tick(&st);
    TEST_ASSERT_EQUAL_UINT32(0xFFFFFFFEu, soft_timer_remaining(&st, 3));
}

static void test_null_arguments(void)
{
    soft_timer_init(NULL);
    TEST_ASSERT_FALSE(soft_timer_start(NULL, 0, 5, true));
    TEST_ASSERT_FALSE(soft_timer_stop(NULL, 0));
    TEST_ASSERT_EQUAL_HEX8(0, soft_timer_tick(NULL));
    TEST_ASSERT_FALSE(soft_timer_is_running(NULL, 0));
    TEST_ASSERT_EQUAL_UINT32(0, soft_timer_remaining(NULL, 0));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_all_stopped);
    RUN_TEST(test_oneshot_fires_on_period_tick_then_stops);
    RUN_TEST(test_periodic_fires_every_period_without_drift);
    RUN_TEST(test_period_one_fires_every_tick);
    RUN_TEST(test_simultaneous_expiry_reported_together);
    RUN_TEST(test_interleaved_periods);
    RUN_TEST(test_remaining_counts_down);
    RUN_TEST(test_periodic_remaining_after_reload);
    RUN_TEST(test_stop_prevents_expiry);
    RUN_TEST(test_restart_running_timer_counts_from_now);
    RUN_TEST(test_restart_with_new_period_and_type);
    RUN_TEST(test_restart_after_oneshot_fired);
    RUN_TEST(test_zero_period_rejected_without_side_effect);
    RUN_TEST(test_invalid_id_rejected);
    RUN_TEST(test_large_period);
    RUN_TEST(test_max_period_accepted);
    RUN_TEST(test_null_arguments);
    return UNITY_END();
}
