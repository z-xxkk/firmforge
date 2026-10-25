/* stepper_ramp 隐藏参考测试：Agent 永远看不到本文件
 * 期望序列由测试作者另写的 Python 程序按同样的公式生成（按 C 语言的整数除法规则处理负数），
 * 不是用参考实现跑出来的。 */
#include <string.h>

#include "stepper_ramp.h"
#include "unity.h"

static const uint32_t SEQ_AVR446_DEFAULT[400] = {
    26769u, 16062u, 12493u, 10571u, 9327u, 8439u, 7764u, 7229u, 6791u, 6424u,
    6110u, 5839u, 5601u, 5389u, 5200u, 5030u, 4875u, 4734u, 4604u, 4485u,
    4374u, 4271u, 4175u, 4085u, 4001u, 3922u, 3847u, 3777u, 3710u, 3646u,
    3586u, 3529u, 3474u, 3422u, 3372u, 3324u, 3278u, 3234u, 3192u, 3151u,
    3112u, 3074u, 3038u, 3003u, 2969u, 2936u, 2905u, 2874u, 2844u, 2815u,
    2787u, 2760u, 2734u, 2708u, 2683u, 2659u, 2635u, 2612u, 2590u, 2568u,
    2547u, 2526u, 2505u, 2486u, 2466u, 2447u, 2429u, 2411u, 2393u, 2376u,
    2359u, 2343u, 2326u, 2310u, 2295u, 2280u, 2265u, 2250u, 2236u, 2222u,
    2208u, 2194u, 2181u, 2168u, 2155u, 2142u, 2130u, 2118u, 2106u, 2094u,
    2082u, 2071u, 2060u, 2049u, 2038u, 2027u, 2017u, 2006u, 1996u, 1986u,
    1976u, 1966u, 1957u, 1947u, 1938u, 1929u, 1920u, 1911u, 1902u, 1893u,
    1885u, 1876u, 1868u, 1860u, 1851u, 1843u, 1835u, 1828u, 1820u, 1812u,
    1805u, 1797u, 1790u, 1783u, 1776u, 1768u, 1761u, 1755u, 1748u, 1741u,
    1734u, 1728u, 1721u, 1715u, 1708u, 1702u, 1696u, 1690u, 1683u, 1677u,
    1671u, 1665u, 1660u, 1654u, 1648u, 1642u, 1637u, 1631u, 1626u, 1620u,
    1615u, 1610u, 1604u, 1599u, 1594u, 1589u, 1584u, 1579u, 1574u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u, 1570u,
    1570u, 1569u, 1573u, 1578u, 1584u, 1589u, 1594u, 1599u, 1604u, 1610u,
    1615u, 1620u, 1626u, 1632u, 1637u, 1643u, 1648u, 1654u, 1660u, 1666u,
    1672u, 1678u, 1684u, 1690u, 1696u, 1703u, 1709u, 1716u, 1722u, 1729u,
    1735u, 1742u, 1749u, 1756u, 1763u, 1770u, 1777u, 1784u, 1792u, 1799u,
    1807u, 1814u, 1822u, 1830u, 1838u, 1846u, 1854u, 1862u, 1870u, 1879u,
    1887u, 1896u, 1905u, 1914u, 1923u, 1932u, 1941u, 1951u, 1960u, 1970u,
    1980u, 1990u, 2000u, 2010u, 2021u, 2032u, 2042u, 2053u, 2065u, 2076u,
    2088u, 2099u, 2111u, 2124u, 2136u, 2149u, 2161u, 2174u, 2188u, 2201u,
    2215u, 2229u, 2244u, 2258u, 2273u, 2288u, 2304u, 2320u, 2336u, 2352u,
    2369u, 2386u, 2404u, 2422u, 2440u, 2459u, 2479u, 2498u, 2519u, 2539u,
    2561u, 2582u, 2605u, 2628u, 2651u, 2676u, 2700u, 2726u, 2752u, 2780u,
    2807u, 2836u, 2866u, 2897u, 2928u, 2961u, 2995u, 3030u, 3066u, 3104u,
    3143u, 3184u, 3226u, 3270u, 3315u, 3363u, 3413u, 3465u, 3520u, 3577u,
    3637u, 3700u, 3767u, 3837u, 3912u, 3991u, 4075u, 4165u, 4260u, 4363u,
    4473u, 4593u, 4722u, 4863u, 5018u, 5188u, 5376u, 5587u, 5825u, 6096u,
    6409u, 6775u, 7212u, 7747u, 8420u, 9307u, 10548u, 12465u, 16027u, 26713u,
};
static const uint32_t SEQ_TRIANGLE[100] = {
    53539u, 32124u, 24985u, 21142u, 18655u, 16878u, 15528u, 14457u, 13581u, 12847u,
    12220u, 11677u, 11201u, 10778u, 10400u, 10059u, 9749u, 9467u, 9208u, 8968u,
    8747u, 8541u, 8349u, 8170u, 8001u, 7843u, 7694u, 7552u, 7419u, 7292u,
    7171u, 7057u, 6947u, 6843u, 6743u, 6647u, 6556u, 6468u, 6383u, 6302u,
    6224u, 6148u, 6075u, 6005u, 5937u, 5872u, 5808u, 5747u, 5687u, 5630u,
    5574u, 5519u, 5466u, 5415u, 5365u, 5317u, 5269u, 5223u, 5179u, 5135u,
    5092u, 5051u, 5010u, 4971u, 4932u, 4894u, 4857u, 4821u, 4786u, 4751u,
    4717u, 4684u, 4652u, 4620u, 4589u, 4559u, 4658u, 4760u, 4869u, 4987u,
    5113u, 5249u, 5397u, 5559u, 5735u, 5929u, 6145u, 6386u, 6658u, 6968u,
    7325u, 7744u, 8243u, 8854u, 9624u, 10637u, 12056u, 14248u, 18319u, 30532u,
};
static const uint32_t SEQ_ONE_STEP[1] = {
    75712u,
};
static const uint32_t SEQ_STARTS_IN_RUN[10] = {
    261799u, 261799u, 261799u, 261799u, 261799u, 261799u, 261799u, 261799u, 261799u, 261799u,
};
static const uint32_t SEQ_REVERSE[50] = {
    75712u, 45428u, 35333u, 29897u, 26380u, 23868u, 21958u, 20444u, 19205u, 18167u,
    17281u, 16513u, 15839u, 15241u, 14707u, 14224u, 13787u, 13387u, 13020u, 12682u,
    12369u, 12078u, 11807u, 11553u, 11315u, 11091u, 11325u, 11574u, 11840u, 12125u,
    12432u, 12764u, 13123u, 13515u, 13944u, 14417u, 14941u, 15527u, 16188u, 16941u,
    17810u, 18828u, 20042u, 21527u, 23399u, 25862u, 29311u, 34640u, 44538u, 74230u,
};
static const uint32_t SEQ_TWO_STEPS[2] = {
    26769u, 16062u,
};
static const uint32_t SEQ_ASYMMETRIC[300] = {
    119787u, 71873u, 55901u, 47301u, 41736u, 37762u, 34741u, 32345u, 30385u, 28742u,
    27340u, 26125u, 25059u, 24113u, 23267u, 22504u, 21812u, 21180u, 20600u, 20065u,
    19569u, 19109u, 18679u, 18278u, 17901u, 17546u, 17212u, 16896u, 16597u, 16314u,
    16044u, 15787u, 15543u, 15309u, 15085u, 14871u, 14666u, 14469u, 14280u, 14098u,
    13923u, 13755u, 13592u, 13435u, 13283u, 13136u, 12994u, 12857u, 12723u, 12594u,
    12469u, 12347u, 12229u, 12114u, 12003u, 11894u, 11788u, 11685u, 11585u, 11487u,
    11392u, 11299u, 11208u, 11120u, 11033u, 10949u, 10866u, 10785u, 10706u, 10629u,
    10553u, 10479u, 10407u, 10336u, 10266u, 10198u, 10131u, 10065u, 10001u, 9938u,
    9876u, 9815u, 9756u, 9697u, 9640u, 9583u, 9527u, 9473u, 9419u, 9366u,
    9315u, 9264u, 9213u, 9164u, 9115u, 9067u, 9020u, 8974u, 8928u, 8883u,
    8839u, 8795u, 8752u, 8710u, 8668u, 8627u, 8586u, 8546u, 8507u, 8468u,
    8430u, 8392u, 8354u, 8318u, 8281u, 8245u, 8210u, 8175u, 8140u, 8106u,
    8072u, 8039u, 8006u, 7974u, 7942u, 7910u, 7879u, 7848u, 7817u, 7787u,
    7757u, 7727u, 7698u, 7669u, 7641u, 7613u, 7585u, 7557u, 7530u, 7503u,
    7476u, 7449u, 7423u, 7397u, 7372u, 7346u, 7321u, 7296u, 7272u, 7247u,
    7223u, 7199u, 7176u, 7152u, 7129u, 7106u, 7083u, 7061u, 7039u, 7017u,
    6995u, 6973u, 6951u, 6930u, 6909u, 6888u, 6867u, 6847u, 6827u, 6806u,
    6786u, 6767u, 6747u, 6728u, 6708u, 6689u, 6670u, 6651u, 6633u, 6614u,
    6596u, 6578u, 6560u, 6542u, 6524u, 6506u, 6489u, 6472u, 6454u, 6437u,
    6420u, 6404u, 6387u, 6370u, 6354u, 6338u, 6322u, 6306u, 6290u, 6274u,
    6258u, 6243u, 6227u, 6212u, 6197u, 6182u, 6167u, 6152u, 6137u, 6122u,
    6108u, 6093u, 6079u, 6065u, 6051u, 6037u, 6023u, 6009u, 5995u, 5981u,
    5968u, 5954u, 5941u, 5928u, 5914u, 5901u, 5888u, 5875u, 5862u, 5850u,
    5837u, 5824u, 5812u, 5799u, 5787u, 5775u, 5762u, 5750u, 5738u, 5726u,
    5714u, 5702u, 5691u, 5679u, 5667u, 5656u, 5644u, 5633u, 5622u, 5610u,
    5599u, 5588u, 5577u, 5566u, 5555u, 5544u, 5533u, 5522u, 5512u, 5501u,
    5491u, 5480u, 5470u, 5459u, 5449u, 5439u, 5428u, 5418u, 5408u, 5398u,
    5388u, 5378u, 5368u, 5359u, 5349u, 5339u, 5329u, 5320u, 5310u, 5301u,
    5291u, 5282u, 5272u, 5263u, 5254u, 5245u, 5236u, 5226u, 5217u, 5208u,
    5199u, 5190u, 5526u, 5936u, 6452u, 7131u, 8082u, 9551u, 12281u, 20469u,
};
static const uint32_t SEQ_MIN_ACCEL[60] = {
    201042u, 120626u, 93820u, 79386u, 70047u, 63376u, 58306u, 54285u, 50995u, 48239u,
    45886u, 43846u, 42057u, 40470u, 39050u, 37769u, 36607u, 35546u, 34572u, 33674u,
    32843u, 32070u, 31350u, 30675u, 30043u, 29448u, 28887u, 28357u, 27855u, 27379u,
    26927u, 27396u, 27889u, 28411u, 28962u, 29548u, 30170u, 30833u, 31542u, 32302u,
    33119u, 34003u, 34960u, 36004u, 37147u, 38406u, 39803u, 41364u, 43124u, 45130u,
    47444u, 50155u, 53391u, 57346u, 62333u, 68895u, 78081u, 92277u, 118643u, 197738u,
};
/* long_trap: steps=64000 */ #define LONG_TRAP_SUM 24657971ull
#define LONG_TRAP_HASH 3434356875u
#define LONG_TRAP_LAST 38518u
#define LONG_TRAP_MIN 193u
/* long_rev: steps=-30000 */ #define LONG_REV_SUM 51494723ull
#define LONG_REV_HASH 188936471u
#define LONG_REV_LAST 9512u
#define LONG_REV_MIN 860u

static stepper_t s;
static uint32_t got[64000];

void setUp(void)
{
    memset(&s, 0xA5, sizeof s);
    stepper_init(&s);
}

void tearDown(void) {}

/* 跑完一次移动，返回步数 */
static unsigned run_move(int32_t steps, uint32_t a, uint32_t d, uint32_t v)
{
    unsigned n = 0;
    uint32_t delay;
    TEST_ASSERT_TRUE(stepper_move(&s, steps, a, d, v));
    while (stepper_next(&s, &delay)) {
        TEST_ASSERT_TRUE_MESSAGE(n < 64000u, "停不下来");
        got[n++] = delay;
    }
    TEST_ASSERT_EQUAL_INT(STEPPER_STOP, stepper_state(&s));
    return n;
}

static void check_seq(const uint32_t *want, unsigned len, int32_t steps, uint32_t a, uint32_t d, uint32_t v)
{
    unsigned n = run_move(steps, a, d, v);
    TEST_ASSERT_EQUAL_UINT_MESSAGE(len, n, "总步数不对");
    TEST_ASSERT_EQUAL_UINT32_ARRAY(want, got, len);
}

static void test_isqrt(void)
{
    static const uint32_t x[] = {0, 1, 2, 3, 4, 15, 16, 17, 6283185, 4294967295u, 4294836225u, 4294836224u};
    static const uint32_t r[] = {0, 1, 1, 1, 2, 3, 4, 4, 2506, 65535, 65535, 65534};
    for (unsigned i = 0; i < sizeof x / sizeof x[0]; i++) {
        TEST_ASSERT_EQUAL_UINT32(r[i], stepper_isqrt(x[i]));
    }
}

static void test_avr446_default_trapezoid(void)
{
    check_seq(SEQ_AVR446_DEFAULT, 400, 400, 4000, 4000, 2000);
    TEST_ASSERT_EQUAL_INT32(400, stepper_position(&s));
    TEST_ASSERT_EQUAL_UINT32(STEPPER_A_T_X100 / 2000u, got[200]);     /* 中段是最高速度 */
}

static void test_triangle_profile(void)
{
    check_seq(SEQ_TRIANGLE, 100, 100, 1000, 3000, 30000);
}

static void test_single_step(void)
{
    check_seq(SEQ_ONE_STEP, 1, 1, 500, 500, 800);
    TEST_ASSERT_EQUAL_INT32(1, stepper_position(&s));
}

static void test_two_steps(void)
{
    check_seq(SEQ_TWO_STEPS, 2, 2, 4000, 4000, 2000);
}

static void test_starts_directly_in_run(void)
{
    TEST_ASSERT_TRUE(stepper_move(&s, 10, 32000, 32000, 12));
    TEST_ASSERT_EQUAL_INT(STEPPER_RUN, stepper_state(&s));
    stepper_init(&s);
    check_seq(SEQ_STARTS_IN_RUN, 10, 10, 32000, 32000, 12);
}

static void test_reverse_direction(void)
{
    check_seq(SEQ_REVERSE, 50, -50, 500, 500, 800);
    TEST_ASSERT_EQUAL_INT32(-50, stepper_position(&s));
    run_move(20, 500, 500, 800);
    TEST_ASSERT_EQUAL_INT32(-30, stepper_position(&s));               /* 位置累计 */
}

static void test_asymmetric_and_min_accel(void)
{
    check_seq(SEQ_ASYMMETRIC, 300, 300, 200, 7000, 5000);
    check_seq(SEQ_MIN_ACCEL, 60, 60, 71, 71, 32000);
}

static uint32_t hash_of(unsigned n, unsigned long long *sum, uint32_t *mn)
{
    uint32_t h = 0;
    *sum = 0;
    *mn = 0xFFFFFFFFu;
    for (unsigned i = 0; i < n; i++) {
        h = h * 31u + got[i];
        *sum += got[i];
        if (got[i] < *mn) *mn = got[i];
    }
    return h;
}

static void test_long_moves(void)
{
    unsigned long long sum;
    uint32_t mn;
    unsigned n = run_move(64000, 1000, 2000, 32000);
    TEST_ASSERT_EQUAL_UINT(64000, n);
    TEST_ASSERT_EQUAL_UINT32(LONG_TRAP_HASH, hash_of(n, &sum, &mn));
    TEST_ASSERT_TRUE(sum == LONG_TRAP_SUM);
    TEST_ASSERT_EQUAL_UINT32(LONG_TRAP_LAST, got[n - 1]);
    TEST_ASSERT_EQUAL_UINT32(LONG_TRAP_MIN, mn);
    n = run_move(-30000, 71, 32000, 9000);
    TEST_ASSERT_EQUAL_UINT(30000, n);
    TEST_ASSERT_EQUAL_UINT32(LONG_REV_HASH, hash_of(n, &sum, &mn));
    TEST_ASSERT_TRUE(sum == LONG_REV_SUM);
    TEST_ASSERT_EQUAL_INT32(64000 - 30000, stepper_position(&s));
}

static void test_profile_shape(void)
{
    unsigned n = run_move(2000, 3000, 1500, 6000);
    uint32_t minimum = STEPPER_A_T_X100 / 6000u;
    unsigned i = 0;
    while (i + 1 < n && got[i + 1] < got[i]) {
        i++;                                                        /* 加速段：间隔越来越短 */
    }
    TEST_ASSERT_TRUE(i > 10);
    for (unsigned k = 0; k < n - 1; k++) {
        TEST_ASSERT_TRUE_MESSAGE(got[k] >= minimum, "比最高速度还快");
    }
}

static void test_rejects_bad_parameters(void)
{
    TEST_ASSERT_FALSE(stepper_move(&s, 0, 1000, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 64001, 1000, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, -64001, 1000, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 70, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 32001, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 1000, 70, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 1000, 32001, 1000));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 1000, 1000, 11));
    TEST_ASSERT_FALSE(stepper_move(&s, 10, 1000, 1000, 32001));
    TEST_ASSERT_FALSE(stepper_move(NULL, 10, 1000, 1000, 1000));
    TEST_ASSERT_EQUAL_INT(STEPPER_STOP, stepper_state(&s));
    TEST_ASSERT_TRUE(stepper_move(&s, -64000, 71, 32000, 32000));
}

static void test_busy_rejects_new_move(void)
{
    uint32_t d;
    TEST_ASSERT_TRUE(stepper_move(&s, 30, 1000, 1000, 1000));
    TEST_ASSERT_TRUE(stepper_next(&s, &d));
    TEST_ASSERT_FALSE(stepper_move(&s, 5, 1000, 1000, 1000));
    while (stepper_next(&s, &d)) {
    }
    TEST_ASSERT_EQUAL_INT32(30, stepper_position(&s));
    TEST_ASSERT_TRUE(stepper_move(&s, 5, 1000, 1000, 1000));          /* 停下后可以 */
}

static void test_next_when_stopped_and_null(void)
{
    uint32_t d = 77;
    TEST_ASSERT_FALSE(stepper_next(&s, &d));
    TEST_ASSERT_EQUAL_UINT32(77, d);
    TEST_ASSERT_TRUE(stepper_move(&s, 3, 1000, 1000, 1000));
    TEST_ASSERT_FALSE(stepper_next(&s, NULL));
    TEST_ASSERT_FALSE(stepper_next(NULL, &d));
    TEST_ASSERT_EQUAL_INT32(0, stepper_position(&s));
    TEST_ASSERT_EQUAL_INT(STEPPER_STOP, stepper_state(NULL));
    TEST_ASSERT_EQUAL_INT32(0, stepper_position(NULL));
    stepper_init(NULL);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_isqrt);
    RUN_TEST(test_avr446_default_trapezoid);
    RUN_TEST(test_triangle_profile);
    RUN_TEST(test_single_step);
    RUN_TEST(test_two_steps);
    RUN_TEST(test_starts_directly_in_run);
    RUN_TEST(test_reverse_direction);
    RUN_TEST(test_asymmetric_and_min_accel);
    RUN_TEST(test_long_moves);
    RUN_TEST(test_profile_shape);
    RUN_TEST(test_rejects_bad_parameters);
    RUN_TEST(test_busy_rejects_new_move);
    RUN_TEST(test_next_when_stopped_and_null);
    return UNITY_END();
}
