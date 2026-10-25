/* at_parser 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "at_parser.h"
#include "unity.h"

static at_parser_t p;

#define MAX_EVENTS 32
static at_event_t evs[MAX_EVENTS];
static char lines[MAX_EVENTS][AT_MAX_LINE + 1];
static int nev;

void setUp(void)
{
    memset(&p, 0xA5, sizeof p);
    at_init(&p);
    nev = 0;
}

void tearDown(void) {}

/* 逐字节喂入，记下所有非 NONE 事件（INFO / URC 同时记下 at_line） */
static void feed(const char *s)
{
    for (; *s != '\0'; s++) {
        at_event_t e = at_feed(&p, (uint8_t)*s);
        if (e != AT_EVT_NONE && nev < MAX_EVENTS) {
            evs[nev] = e;
            lines[nev][0] = '\0';
            if (e == AT_EVT_INFO || e == AT_EVT_URC) {
                strncpy(lines[nev], at_line(&p), AT_MAX_LINE);
                lines[nev][AT_MAX_LINE] = '\0';
            }
            nev++;
        }
    }
}

static void expect(int i, at_event_t e, const char *line)
{
    TEST_ASSERT_TRUE_MESSAGE(i < nev, "事件比预期少");
    TEST_ASSERT_EQUAL_INT(e, evs[i]);
    if (line != NULL) {
        TEST_ASSERT_EQUAL_STRING(line, lines[i]);
    }
}

static void test_csq_with_echo(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    feed("AT+CSQ\r\r\n+CSQ: 20,99\r\n\r\nOK\r\n");
    TEST_ASSERT_EQUAL_INT(2, nev);
    expect(0, AT_EVT_INFO, "+CSQ: 20,99");
    expect(1, AT_EVT_OK, NULL);
    TEST_ASSERT_FALSE(at_busy(&p));
}

static void test_without_echo_and_lf_only(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    feed("\n+CSQ: 31,0\nOK\n");
    TEST_ASSERT_EQUAL_INT(2, nev);
    expect(0, AT_EVT_INFO, "+CSQ: 31,0");
    expect(1, AT_EVT_OK, NULL);
}

static void test_bytes_fed_one_by_one_across_calls(void)
{
    static const char *chunks[] = {"\r", "\n+C", "SQ", ": 1", "5,2", "\r", "\n", "\r\nO", "K", "\r\n"};
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    for (unsigned i = 0; i < sizeof chunks / sizeof chunks[0]; i++) {
        feed(chunks[i]);
    }
    TEST_ASSERT_EQUAL_INT(2, nev);
    expect(0, AT_EVT_INFO, "+CSQ: 15,2");
}

static void test_creg_is_info_during_query_and_urc_otherwise(void)
{
    feed("\r\n+CREG: 5\r\n");
    TEST_ASSERT_EQUAL_INT(1, nev);
    expect(0, AT_EVT_URC, "+CREG: 5");
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CREG?", "+CREG"));
    feed("\r\n+CREG: 0,1\r\n\r\nOK\r\n");
    expect(1, AT_EVT_INFO, "+CREG: 0,1");
    expect(2, AT_EVT_OK, NULL);
}

static void test_urc_interleaved_during_command(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    feed("AT+CSQ\r\r\nRING\r\n\r\n+CMTI: \"SM\",3\r\n\r\n+CSQ: 9,99\r\n\r\nOK\r\n");
    TEST_ASSERT_EQUAL_INT(4, nev);
    expect(0, AT_EVT_URC, "RING");
    expect(1, AT_EVT_URC, "+CMTI: \"SM\",3");
    expect(2, AT_EVT_INFO, "+CSQ: 9,99");
    expect(3, AT_EVT_OK, NULL);
}

static void test_prefix_must_be_followed_by_colon(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    feed("\r\n+CSQX: 1\r\n\r\nOK\r\n");
    expect(0, AT_EVT_URC, "+CSQX: 1");                 /* 不是 +CSQ: 开头：当作主动上报 */
}

static void test_info_without_prefix(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CGSN", NULL));
    feed("AT+CGSN\r\r\n866123045678901\r\n\r\nOK\r\n");
    TEST_ASSERT_EQUAL_INT(2, nev);
    expect(0, AT_EVT_INFO, "866123045678901");
    TEST_ASSERT_TRUE(at_begin(&p, "ATI", ""));
    feed("\r\nQuectel\r\nEC20F\r\n+CREG: 1\r\nOK\r\n");
    expect(2, AT_EVT_INFO, "Quectel");
    expect(3, AT_EVT_INFO, "EC20F");
    expect(4, AT_EVT_URC, "+CREG: 1");
    expect(5, AT_EVT_OK, NULL);
}

static void test_error_and_cme_error(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CPIN?", "+CPIN"));
    feed("\r\n+CME ERROR: 10\r\n");
    expect(0, AT_EVT_CME_ERROR, NULL);
    TEST_ASSERT_EQUAL_INT(10, at_cme_error(&p));
    TEST_ASSERT_FALSE(at_busy(&p));
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CPIN?", "+CPIN"));
    feed("\r\n+CME ERROR: SIM not inserted\r\n");          /* +CMEE=2 的文字形式 */
    expect(1, AT_EVT_CME_ERROR, NULL);
    TEST_ASSERT_EQUAL_INT(-1, at_cme_error(&p));
    TEST_ASSERT_TRUE(at_begin(&p, "AT+XYZ", ""));
    feed("\r\nERROR\r\n");
    expect(2, AT_EVT_ERROR, NULL);
    TEST_ASSERT_FALSE(at_busy(&p));
}

static void test_dial_result_codes_are_final(void)
{
    static const char *codes[] = {"NO CARRIER", "BUSY", "NO ANSWER", "NO DIALTONE"};
    static const at_event_t evt[] = {AT_EVT_NO_CARRIER, AT_EVT_BUSY, AT_EVT_NO_ANSWER, AT_EVT_NO_DIALTONE};
    for (int i = 0; i < 4; i++) {
        char buf[40] = "\r\n";
        strcat(buf, codes[i]);
        strcat(buf, "\r\n");
        TEST_ASSERT_TRUE(at_begin(&p, "ATD10086;", ""));
        feed(buf);
        expect(i, evt[i], NULL);
        TEST_ASSERT_FALSE(at_busy(&p));
    }
}

static void test_stale_result_codes_ignored_when_idle(void)
{
    feed("\r\nOK\r\n\r\nERROR\r\n\r\n+CME ERROR: 3\r\n\r\nNO CARRIER\r\nhello\r\n");
    TEST_ASSERT_EQUAL_INT(0, nev);
    TEST_ASSERT_EQUAL_INT(0, at_cme_error(&p));
}

static void test_timeout_then_late_ok(void)
{
    TEST_ASSERT_EQUAL_INT(AT_EVT_NONE, at_timeout(&p));
    TEST_ASSERT_TRUE(at_begin(&p, "AT+COPS?", "+COPS"));
    TEST_ASSERT_FALSE(at_begin(&p, "AT", ""));            /* 上一条还没结束 */
    TEST_ASSERT_EQUAL_INT(AT_EVT_TIMEOUT, at_timeout(&p));
    TEST_ASSERT_FALSE(at_busy(&p));
    feed("\r\n+COPS: 0,0,\"CMCC\"\r\n\r\nOK\r\n");          /* 超时后才到 */
    TEST_ASSERT_EQUAL_INT(1, nev);
    expect(0, AT_EVT_URC, "+COPS: 0,0,\"CMCC\"");
    TEST_ASSERT_TRUE(at_begin(&p, "AT", ""));
    feed("\r\nOK\r\n");
    expect(1, AT_EVT_OK, NULL);
}

static void test_line_too_long(void)
{
    char longline[300];
    memset(longline, 'A', 200);
    longline[0] = '+';
    longline[200] = '\0';
    TEST_ASSERT_TRUE(at_begin(&p, "AT+X", "+X"));
    feed("\r\n");
    feed(longline);
    feed("\r\n\r\nOK\r\n");
    TEST_ASSERT_EQUAL_INT(2, nev);
    expect(0, AT_EVT_LINE_TOO_LONG, NULL);
    expect(1, AT_EVT_OK, NULL);
    memset(longline, 'B', AT_MAX_LINE);                    /* 正好 128 个字符：不算太长 */
    longline[AT_MAX_LINE] = '\0';
    TEST_ASSERT_TRUE(at_begin(&p, "AT+Y", ""));
    feed(longline);
    feed("\r\nOK\r\n");
    expect(2, AT_EVT_INFO, NULL);
    TEST_ASSERT_EQUAL_UINT(AT_MAX_LINE, strlen(lines[2]));
}

static void test_echo_must_match_exactly(void)
{
    TEST_ASSERT_TRUE(at_begin(&p, "AT+CSQ", "+CSQ"));
    feed("at+csq\r\nOK\r\n");                               /* 大小写不同：不是回显 */
    expect(0, AT_EVT_INFO, "at+csq");
    expect(1, AT_EVT_OK, NULL);
}

static void test_begin_rejections(void)
{
    char big[100];
    memset(big, 'A', sizeof big - 1);
    big[sizeof big - 1] = '\0';
    TEST_ASSERT_FALSE(at_begin(NULL, "AT", ""));
    TEST_ASSERT_FALSE(at_begin(&p, NULL, ""));
    TEST_ASSERT_FALSE(at_begin(&p, "", ""));
    TEST_ASSERT_FALSE(at_begin(&p, big, ""));
    TEST_ASSERT_FALSE(at_begin(&p, "AT", "+ABCDEFGHIJKLMNOPQ"));   /* 前缀 18 个字符 */
    TEST_ASSERT_FALSE(at_busy(&p));
    TEST_ASSERT_TRUE(at_begin(&p, "AT", "+ABCDEFGHIJKLMNO"));       /* 正好 16 个 */
    TEST_ASSERT_EQUAL_INT(AT_EVT_NONE, at_feed(NULL, 'O'));
    TEST_ASSERT_FALSE(at_busy(NULL));
    TEST_ASSERT_EQUAL_STRING("", at_line(NULL));
    TEST_ASSERT_EQUAL_INT(0, at_cme_error(NULL));
    TEST_ASSERT_EQUAL_INT(AT_EVT_NONE, at_timeout(NULL));
    at_init(NULL);
}

static void test_parse_csq(void)
{
    int r = -5, b = -5;
    TEST_ASSERT_TRUE(at_parse_csq("+CSQ: 20,99", &r, &b));
    TEST_ASSERT_EQUAL_INT(20, r);
    TEST_ASSERT_EQUAL_INT(99, b);
    TEST_ASSERT_TRUE(at_parse_csq("+CSQ:31,7", &r, &b));
    TEST_ASSERT_TRUE(at_parse_csq("+CSQ: 99,99", &r, &b));
    TEST_ASSERT_EQUAL_INT(99, r);
    r = b = -5;
    static const char *bad[] = {"+CSQ: 32,0", "+CSQ: 5,8", "+CSQ: 20", "+CSQ: 20,99,1", "+CSQ: a,1",
                                "+CREG: 1,2", "+CSQ: ,1", "+CSQ: 20, 99x", "CSQ: 20,99"};
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        TEST_ASSERT_FALSE_MESSAGE(at_parse_csq(bad[i], &r, &b), bad[i]);
    }
    TEST_ASSERT_EQUAL_INT(-5, r);                           /* 失败时不改输出 */
    TEST_ASSERT_FALSE(at_parse_csq(NULL, &r, &b));
    TEST_ASSERT_FALSE(at_parse_csq("+CSQ: 1,1", NULL, &b));
}

static void test_rssi_to_dbm(void)
{
    int dbm = 0;
    TEST_ASSERT_TRUE(at_rssi_dbm(0, &dbm));
    TEST_ASSERT_EQUAL_INT(-113, dbm);
    TEST_ASSERT_TRUE(at_rssi_dbm(1, &dbm));
    TEST_ASSERT_EQUAL_INT(-111, dbm);
    TEST_ASSERT_TRUE(at_rssi_dbm(30, &dbm));
    TEST_ASSERT_EQUAL_INT(-53, dbm);
    TEST_ASSERT_TRUE(at_rssi_dbm(31, &dbm));
    TEST_ASSERT_EQUAL_INT(-51, dbm);
    dbm = 7;
    TEST_ASSERT_FALSE(at_rssi_dbm(99, &dbm));
    TEST_ASSERT_FALSE(at_rssi_dbm(-1, &dbm));
    TEST_ASSERT_FALSE(at_rssi_dbm(32, &dbm));
    TEST_ASSERT_EQUAL_INT(7, dbm);
    TEST_ASSERT_FALSE(at_rssi_dbm(5, NULL));
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_csq_with_echo);
    RUN_TEST(test_without_echo_and_lf_only);
    RUN_TEST(test_bytes_fed_one_by_one_across_calls);
    RUN_TEST(test_creg_is_info_during_query_and_urc_otherwise);
    RUN_TEST(test_urc_interleaved_during_command);
    RUN_TEST(test_prefix_must_be_followed_by_colon);
    RUN_TEST(test_info_without_prefix);
    RUN_TEST(test_error_and_cme_error);
    RUN_TEST(test_dial_result_codes_are_final);
    RUN_TEST(test_stale_result_codes_ignored_when_idle);
    RUN_TEST(test_timeout_then_late_ok);
    RUN_TEST(test_line_too_long);
    RUN_TEST(test_echo_must_match_exactly);
    RUN_TEST(test_begin_rejections);
    RUN_TEST(test_parse_csq);
    RUN_TEST(test_rssi_to_dbm);
    return UNITY_END();
}
