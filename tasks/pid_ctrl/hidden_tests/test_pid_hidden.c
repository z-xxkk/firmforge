/* pid_ctrl 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "pid_ctrl.h"
#include "unity.h"

static pid_ctrl_t pid;

void setUp(void) { memset(&pid, 0xA5, sizeof pid); }
void tearDown(void) {}

/* ---------- 测试自己的模型（另一种写法：分开保存正负、显式实现向零取整） ---------- */
typedef struct {
    long long kp, ki, kd, lo, hi, isum, last, out;
    int autom;
} model_t;

static long long div1024_toward_zero(long long v)
{
    long long m = v < 0 ? -v : v;
    long long q = m / 1024;
    return v < 0 ? -q : q;
}

static long long clampll(long long v, long long lo, long long hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static void m_init(model_t *m, long long kp, long long ki, long long kd, long long lo, long long hi)
{
    memset(m, 0, sizeof *m);
    m->kp = kp; m->ki = ki; m->kd = kd; m->lo = lo; m->hi = hi;
    m->out = clampll(0, lo, hi);
}

static void m_auto(model_t *m, long long input)
{
    if (!m->autom) {
        m->isum = clampll(m->out * 1024, m->lo * 1024, m->hi * 1024);
        m->last = input;
    }
    m->autom = 1;
}

static long long m_step(model_t *m, long long sp, long long in)
{
    if (!m->autom) return m->out;
    long long e = sp - in;
    m->isum = clampll(m->isum + m->ki * e, m->lo * 1024, m->hi * 1024);
    long long raw = m->kp * e + m->isum - m->kd * (in - m->last);
    m->out = clampll(div1024_toward_zero(raw), m->lo, m->hi);
    m->last = in;
    return m->out;
}

static void both_init(model_t *m, int32_t kp, int32_t ki, int32_t kd, int32_t lo, int32_t hi)
{
    TEST_ASSERT_TRUE(pid_init(&pid, kp, ki, kd, lo, hi));
    m_init(m, kp, ki, kd, lo, hi);
}

static void both_auto(model_t *m, int32_t input)
{
    pid_set_auto(&pid, true, input);
    m_auto(m, input);
}

static void both_step(model_t *m, int32_t sp, int32_t in)
{
    long long want = m_step(m, sp, in);
    TEST_ASSERT_EQUAL_INT32((int32_t)want, pid_compute(&pid, sp, in));
}

/* ---------- 测试 ---------- */

static void test_spec_example(void)
{
    TEST_ASSERT_TRUE(pid_init(&pid, 2048, 512, 0, -1000, 1000));
    pid_set_auto(&pid, true, 40);
    TEST_ASSERT_EQUAL_INT32(150, pid_compute(&pid, 100, 40));
    TEST_ASSERT_EQUAL_INT32(155, pid_compute(&pid, 100, 50));
    TEST_ASSERT_EQUAL_INT32(47, pid_compute(&pid, 100, 103));
}

static void test_starts_in_manual(void)
{
    TEST_ASSERT_TRUE(pid_init(&pid, 1024, 1024, 1024, -100, 100));
    TEST_ASSERT_FALSE(pid_is_auto(&pid));
    TEST_ASSERT_EQUAL_INT32(0, pid_output(&pid));
    TEST_ASSERT_EQUAL_INT32(0, pid_compute(&pid, 50, 0));     /* 手动：不计算 */
    TEST_ASSERT_TRUE(pid_init(&pid, 1024, 0, 0, 10, 100));
    TEST_ASSERT_EQUAL_INT32(10, pid_output(&pid));           /* 初值 0 钳到范围内 */
}

static void test_rounding_toward_zero(void)
{
    TEST_ASSERT_TRUE(pid_init(&pid, 1536, 0, 0, -1000, 1000));   /* kp = 1.5 */
    pid_set_auto(&pid, true, 0);
    TEST_ASSERT_EQUAL_INT32(-4, pid_compute(&pid, 0, 3));         /* −4.5 → −4，不是 −5 */
    TEST_ASSERT_EQUAL_INT32(4, pid_compute(&pid, 3, 0));
    TEST_ASSERT_EQUAL_INT32(-1, pid_compute(&pid, 0, 1));         /* −1.5 → −1 */
}

static void test_integral_accumulates(void)
{
    model_t m;
    both_init(&m, 0, 512, 0, -1000, 1000);
    both_auto(&m, 0);
    for (int i = 0; i < 10; i++) {
        both_step(&m, 10, 0);
    }
    TEST_ASSERT_EQUAL_INT32(50, pid_output(&pid));
}

static void test_anti_windup_recovers_immediately(void)
{
    model_t m;
    both_init(&m, 1024, 2048, 0, 0, 255);
    both_auto(&m, 0);
    for (int i = 0; i < 200; i++) {
        both_step(&m, 1000, 0);                                   /* 长时间饱和 */
    }
    TEST_ASSERT_EQUAL_INT32(255, pid_output(&pid));
    int32_t out = pid_compute(&pid, 0, 100);                      /* 设定值回落 */
    TEST_ASSERT_TRUE_MESSAGE(out < 255, "积分饱和：设定值回落后输出没有立即下降");
    TEST_ASSERT_EQUAL_INT32((int32_t)m_step(&m, 0, 100), out);
}

static void test_no_derivative_kick_on_setpoint_change(void)
{
    model_t m;
    both_init(&m, 0, 0, 10240, -10000, 10000);                    /* 只有 D */
    both_auto(&m, 20);
    both_step(&m, 20, 20);
    TEST_ASSERT_EQUAL_INT32(0, pid_compute(&pid, 900, 20));       /* 设定值突变，测量值没变：无尖峰 */
    m_step(&m, 900, 20);
    both_step(&m, 900, 25);                                       /* 测量值变 5：输出 −50 */
    TEST_ASSERT_EQUAL_INT32(-50, pid_output(&pid));
}

static void test_ki_change_does_not_bump(void)
{
    model_t m;
    both_init(&m, 1024, 1024, 0, -100000, 100000);
    both_auto(&m, 0);
    for (int i = 0; i < 20; i++) {
        both_step(&m, 50, 40);
    }
    int32_t before = pid_output(&pid);
    TEST_ASSERT_TRUE(pid_set_tunings(&pid, 1024, 4096, 0));
    m.ki = 4096;
    both_step(&m, 50, 50);                                        /* 误差 0：输出只剩积分，不应跳变 */
    TEST_ASSERT_EQUAL_INT32(before - 10, pid_output(&pid));
}

static void test_bumpless_manual_to_auto(void)
{
    TEST_ASSERT_TRUE(pid_init(&pid, 2048, 256, 1024, 0, 1000));
    TEST_ASSERT_TRUE(pid_set_output(&pid, 300));
    TEST_ASSERT_EQUAL_INT32(300, pid_compute(&pid, 75, 75));      /* 手动 */
    pid_set_auto(&pid, true, 75);
    TEST_ASSERT_TRUE(pid_is_auto(&pid));
    TEST_ASSERT_EQUAL_INT32(300, pid_compute(&pid, 75, 75));      /* 切换后输出不跳 */
    TEST_ASSERT_EQUAL_INT32(300, pid_compute(&pid, 75, 75));
}

static void test_repeated_auto_does_not_reinit(void)
{
    model_t m;
    both_init(&m, 0, 1024, 0, -1000, 1000);
    both_auto(&m, 0);
    for (int i = 0; i < 5; i++) {
        both_step(&m, 10, 0);
    }
    pid_set_auto(&pid, true, 999);                                /* 已经是自动 */
    m_auto(&m, 999);
    both_step(&m, 10, 0);
    TEST_ASSERT_EQUAL_INT32(60, pid_output(&pid));
}

static void test_auto_to_manual_and_back(void)
{
    model_t m;
    both_init(&m, 1024, 512, 512, -500, 500);
    both_auto(&m, 10);
    both_step(&m, 100, 10);
    pid_set_auto(&pid, false, 0);
    m.autom = 0;
    TEST_ASSERT_TRUE(pid_set_output(&pid, -200));
    m.out = -200;
    both_step(&m, 100, 10);                                       /* 手动：返回 −200 */
    both_auto(&m, 30);
    for (int i = 0; i < 10; i++) {
        both_step(&m, 100, 30 + i * 7);
    }
}

static void test_set_limits_clamps_immediately(void)
{
    model_t m;
    both_init(&m, 0, 1024, 0, -1000, 1000);
    both_auto(&m, 0);
    for (int i = 0; i < 8; i++) {
        both_step(&m, 100, 0);
    }
    TEST_ASSERT_EQUAL_INT32(800, pid_output(&pid));
    TEST_ASSERT_TRUE(pid_set_limits(&pid, -50, 300));
    TEST_ASSERT_EQUAL_INT32(300, pid_output(&pid));               /* 输出立即钳住 */
    TEST_ASSERT_EQUAL_INT32(300, pid_compute(&pid, 0, 0));        /* 积分也钳到 300：误差 0 时输出 300 */
    TEST_ASSERT_EQUAL_INT32(200, pid_compute(&pid, 0, 100));      /* 积分 300 − 100 */
    TEST_ASSERT_FALSE(pid_set_limits(&pid, 10, 10));
    TEST_ASSERT_FALSE(pid_set_limits(&pid, 20, 10));
    TEST_ASSERT_EQUAL_INT32(200, pid_output(&pid));
}

static void test_set_output_only_in_manual(void)
{
    TEST_ASSERT_TRUE(pid_init(&pid, 1024, 0, 0, -10, 10));
    TEST_ASSERT_TRUE(pid_set_output(&pid, 50));
    TEST_ASSERT_EQUAL_INT32(10, pid_output(&pid));                /* 钳到范围 */
    pid_set_auto(&pid, true, 0);
    TEST_ASSERT_FALSE(pid_set_output(&pid, 3));
    TEST_ASSERT_EQUAL_INT32(10, pid_output(&pid));
}

static void test_invalid_arguments(void)
{
    TEST_ASSERT_FALSE(pid_init(NULL, 1, 1, 1, 0, 1));
    TEST_ASSERT_FALSE(pid_init(&pid, -1, 0, 0, 0, 10));
    TEST_ASSERT_FALSE(pid_init(&pid, 0, -1, 0, 0, 10));
    TEST_ASSERT_FALSE(pid_init(&pid, 0, 0, -1, 0, 10));
    TEST_ASSERT_FALSE(pid_init(&pid, PID_GAIN_MAX + 1, 0, 0, 0, 10));
    TEST_ASSERT_FALSE(pid_init(&pid, 0, 0, 0, 10, 10));
    TEST_ASSERT_TRUE(pid_init(&pid, PID_GAIN_MAX, PID_GAIN_MAX, PID_GAIN_MAX, 0, 10));
    TEST_ASSERT_FALSE(pid_set_tunings(&pid, 0, PID_GAIN_MAX + 1, 0));
    TEST_ASSERT_FALSE(pid_set_tunings(NULL, 0, 0, 0));
    TEST_ASSERT_FALSE(pid_set_limits(NULL, 0, 1));
    TEST_ASSERT_FALSE(pid_set_output(NULL, 1));
    TEST_ASSERT_EQUAL_INT32(0, pid_compute(NULL, 1, 1));
    TEST_ASSERT_EQUAL_INT32(0, pid_output(NULL));
    TEST_ASSERT_FALSE(pid_is_auto(NULL));
    pid_set_auto(NULL, true, 0);
}

static void test_extreme_values_no_overflow(void)
{
    model_t m;
    both_init(&m, PID_GAIN_MAX, PID_GAIN_MAX, PID_GAIN_MAX, INT32_MIN, INT32_MAX);
    both_auto(&m, 0);
    both_step(&m, INT32_MAX, INT32_MIN);
    both_step(&m, INT32_MIN, INT32_MAX);
    both_step(&m, INT32_MAX, INT32_MAX);
    both_step(&m, 0, INT32_MIN);
}

static void test_long_random_run_matches_model(void)
{
    model_t m;
    unsigned seed = 12345;
    both_init(&m, 3000, 700, 2500, -32768, 32767);
    both_auto(&m, 0);
    for (int i = 0; i < 2000; i++) {
        seed = seed * 1103515245u + 12345u;
        int32_t sp = (int32_t)((seed >> 8) % 20001u) - 10000;
        seed = seed * 1103515245u + 12345u;
        int32_t in = (int32_t)((seed >> 8) % 20001u) - 10000;
        both_step(&m, sp, in);
        if (i == 700) {
            TEST_ASSERT_TRUE(pid_set_tunings(&pid, 1000, 2000, 100));
            m.kp = 1000; m.ki = 2000; m.kd = 100;
        }
        if (i == 1400) {
            TEST_ASSERT_TRUE(pid_set_limits(&pid, -500, 9000));
            m.lo = -500; m.hi = 9000;
            m.out = clampll(m.out, m.lo, m.hi);
            m.isum = clampll(m.isum, m.lo * 1024, m.hi * 1024);
        }
    }
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_spec_example);
    RUN_TEST(test_starts_in_manual);
    RUN_TEST(test_rounding_toward_zero);
    RUN_TEST(test_integral_accumulates);
    RUN_TEST(test_anti_windup_recovers_immediately);
    RUN_TEST(test_no_derivative_kick_on_setpoint_change);
    RUN_TEST(test_ki_change_does_not_bump);
    RUN_TEST(test_bumpless_manual_to_auto);
    RUN_TEST(test_repeated_auto_does_not_reinit);
    RUN_TEST(test_auto_to_manual_and_back);
    RUN_TEST(test_set_limits_clamps_immediately);
    RUN_TEST(test_set_output_only_in_manual);
    RUN_TEST(test_invalid_arguments);
    RUN_TEST(test_extreme_values_no_overflow);
    RUN_TEST(test_long_random_run_matches_model);
    return UNITY_END();
}
