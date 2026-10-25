#include "at_parser.h"

static bool str_eq(const char *a, const char *b)
{
    while (*a != '\0' && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

static bool starts_with(const char *s, const char *prefix)
{
    while (*prefix != '\0') {
        if (*s != *prefix) {
            return false;
        }
        s++;
        prefix++;
    }
    return true;
}

static size_t str_len(const char *s)
{
    size_t n = 0;
    while (s[n] != '\0') {
        n++;
    }
    return n;
}

static void str_copy(char *dst, const char *src)
{
    while ((*dst++ = *src++) != '\0') {
    }
}

void at_init(at_parser_t *p)
{
    if (p == NULL) {
        return;
    }
    p->len = 0;
    p->too_long = false;
    p->line[0] = '\0';
    p->command[0] = '\0';
    p->prefix[0] = '\0';
    p->busy = false;
    p->cme_error = 0;
}

bool at_begin(at_parser_t *p, const char *command, const char *info_prefix)
{
    if (p == NULL || command == NULL || p->busy) {
        return false;
    }
    if (info_prefix == NULL) {
        info_prefix = "";
    }
    if (str_len(command) == 0u || str_len(command) > AT_MAX_COMMAND || str_len(info_prefix) > AT_MAX_PREFIX) {
        return false;
    }
    str_copy(p->command, command);
    str_copy(p->prefix, info_prefix);
    p->busy = true;
    return true;
}

static int parse_cme(const char *s)
{
    int v = 0;
    bool any = false;
    while (*s == ' ') {
        s++;
    }
    while (*s >= '0' && *s <= '9') {
        v = v * 10 + (*s - '0');
        any = true;
        s++;
        if (v > 100000) {
            return -1;
        }
    }
    return (any && *s == '\0') ? v : -1;      /* 文字形式（+CMEE=2）记为 −1 */
}

static at_event_t final_code(const char *s)
{
    if (str_eq(s, "OK")) {
        return AT_EVT_OK;
    }
    if (str_eq(s, "ERROR")) {
        return AT_EVT_ERROR;
    }
    if (starts_with(s, "+CME ERROR:")) {
        return AT_EVT_CME_ERROR;
    }
    if (str_eq(s, "NO CARRIER")) {
        return AT_EVT_NO_CARRIER;
    }
    if (str_eq(s, "BUSY")) {
        return AT_EVT_BUSY;
    }
    if (str_eq(s, "NO ANSWER")) {
        return AT_EVT_NO_ANSWER;
    }
    if (str_eq(s, "NO DIALTONE")) {
        return AT_EVT_NO_DIALTONE;
    }
    return AT_EVT_NONE;
}

static bool looks_like_urc(const char *s)
{
    return s[0] == '+' || str_eq(s, "RING");
}

static bool matches_prefix(const char *s, const char *prefix)
{
    size_t n = str_len(prefix);
    return n > 0u && starts_with(s, prefix) && s[n] == ':';
}

static at_event_t keep(at_parser_t *p, at_event_t ev)
{
    str_copy(p->line, p->buf);
    return ev;
}

static at_event_t end_of_line(at_parser_t *p)
{
    at_event_t fin;
    p->buf[p->len] = '\0';
    if (p->too_long) {
        p->too_long = false;
        p->len = 0;
        return AT_EVT_LINE_TOO_LONG;
    }
    if (p->len == 0u) {
        return AT_EVT_NONE;                   /* 空行（<CR><LF> 的另一半） */
    }
    p->len = 0;
    fin = final_code(p->buf);
    if (!p->busy) {
        if (fin != AT_EVT_NONE) {
            return AT_EVT_NONE;               /* 没有命令时的残留结果码 */
        }
        return looks_like_urc(p->buf) ? keep(p, AT_EVT_URC) : AT_EVT_NONE;
    }
    if (fin != AT_EVT_NONE) {
        if (fin == AT_EVT_CME_ERROR) {
            p->cme_error = parse_cme(p->buf + 11);
        }
        p->busy = false;
        return fin;
    }
    if (str_eq(p->buf, p->command)) {
        return AT_EVT_NONE;                   /* 回显 */
    }
    if (looks_like_urc(p->buf)) {
        return keep(p, AT_EVT_URC);    /* 变异：先判主动上报，+CREG 查询结果被当成上报 */
    }
    if (matches_prefix(p->buf, p->prefix)) {
        return keep(p, AT_EVT_INFO);
    }
    return keep(p, AT_EVT_INFO);
}

at_event_t at_feed(at_parser_t *p, uint8_t byte)
{
    if (p == NULL) {
        return AT_EVT_NONE;
    }
    if (byte == '\r' || byte == '\n') {
        return end_of_line(p);
    }
    if (p->too_long) {
        return AT_EVT_NONE;
    }
    if (p->len >= AT_MAX_LINE) {
        p->too_long = true;
        p->len = 0;
        return AT_EVT_NONE;
    }
    p->buf[p->len++] = (char)byte;
    return AT_EVT_NONE;
}

at_event_t at_timeout(at_parser_t *p)
{
    if (p == NULL || !p->busy) {
        return AT_EVT_NONE;
    }
    p->busy = false;
    return AT_EVT_TIMEOUT;
}

bool at_busy(const at_parser_t *p)
{
    return p != NULL && p->busy;
}

const char *at_line(const at_parser_t *p)
{
    return p == NULL ? "" : p->line;
}

int at_cme_error(const at_parser_t *p)
{
    return p == NULL ? 0 : p->cme_error;
}

static bool parse_int(const char **s, int *out)
{
    int v = 0;
    bool any = false;
    while (**s >= '0' && **s <= '9') {
        v = v * 10 + (**s - '0');
        any = true;
        (*s)++;
        if (v > 1000) {
            return false;
        }
    }
    *out = v;
    return any;
}

bool at_parse_csq(const char *line, int *rssi, int *ber)
{
    int r, b;
    if (line == NULL || rssi == NULL || ber == NULL || !starts_with(line, "+CSQ:")) {
        return false;
    }
    line += 5;
    while (*line == ' ') {
        line++;
    }
    if (!parse_int(&line, &r) || *line != ',') {
        return false;
    }
    line++;
    if (!parse_int(&line, &b) || *line != '\0') {
        return false;
    }
    if (!((r >= 0 && r <= 31) || r == 99) || !((b >= 0 && b <= 7) || b == 99)) {
        return false;
    }
    *rssi = r;
    *ber = b;
    return true;
}

bool at_rssi_dbm(int rssi, int *dbm)
{
    if (dbm == NULL || rssi < 0 || rssi > 31) {
        return false;
    }
    *dbm = -113 + 2 * rssi;
    return true;
}
