#ifndef AT_PARSER_H
#define AT_PARSER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AT_MAX_LINE     128u   /* 一行最多多少个字符（不含行尾） */
#define AT_MAX_COMMAND  64u    /* 命令最多多少个字符 */
#define AT_MAX_PREFIX   16u    /* 信息文本前缀最多多少个字符 */

typedef enum {
    AT_EVT_NONE = 0,         /* 还没凑成一行，或这一行被忽略 */
    AT_EVT_INFO,             /* 一行信息文本：内容见 at_line */
    AT_EVT_URC,              /* 一行主动上报：内容见 at_line */
    AT_EVT_OK,               /* 命令成功结束 */
    AT_EVT_ERROR,            /* 命令失败（ERROR） */
    AT_EVT_CME_ERROR,        /* 命令失败（+CME ERROR），错误码见 at_cme_error */
    AT_EVT_NO_CARRIER,       /* 命令失败（NO CARRIER） */
    AT_EVT_BUSY,             /* 命令失败（BUSY） */
    AT_EVT_NO_ANSWER,        /* 命令失败（NO ANSWER） */
    AT_EVT_NO_DIALTONE,      /* 命令失败（NO DIALTONE） */
    AT_EVT_LINE_TOO_LONG,    /* 一行超过 AT_MAX_LINE，已整行丢弃 */
    AT_EVT_TIMEOUT           /* 调用方通知超时，命令结束 */
} at_event_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    char buf[AT_MAX_LINE + 1u];          /* 正在收的这一行 */
    size_t len;
    bool too_long;
    char line[AT_MAX_LINE + 1u];         /* 最近一行信息文本或主动上报 */
    char command[AT_MAX_COMMAND + 1u];   /* 正在执行的命令（用来认出回显） */
    char prefix[AT_MAX_PREFIX + 1u];     /* 这条命令的信息文本前缀 */
    bool busy;                           /* 有命令在等结果 */
    int cme_error;                       /* 最近一次 +CME ERROR 的错误码 */
} at_parser_t;

void        at_init(at_parser_t *p);
bool        at_begin(at_parser_t *p, const char *command, const char *info_prefix);
at_event_t  at_feed(at_parser_t *p, uint8_t byte);
at_event_t  at_timeout(at_parser_t *p);
bool        at_busy(const at_parser_t *p);
const char *at_line(const at_parser_t *p);
int         at_cme_error(const at_parser_t *p);

/* 解析 "+CSQ: <rssi>,<ber>" */
bool at_parse_csq(const char *line, int *rssi, int *ber);
/* rssi 换算成 dBm：0 → −113 … 31 → −51；99 或超出范围返回 false */
bool at_rssi_dbm(int rssi, int *dbm);

#endif /* AT_PARSER_H */
