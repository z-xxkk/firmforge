#ifndef MAVLINK_PARSER_H
#define MAVLINK_PARSER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MAVLINK_STX_V1          0xFEu
#define MAVLINK_STX_V2          0xFDu
#define MAVLINK_MAX_PAYLOAD     255u
#define MAVLINK_SIGNATURE_LEN   13u
#define MAVLINK_IFLAG_SIGNED    0x01u
#define MAVLINK_MAX_FRAME       280u   /* v2 最长：10 字节帧头 + 255 负载 + 2 校验 + 13 签名 */
#define MAVLINK_MAX_SOURCES     8u     /* 丢包统计最多跟踪几个来源 */

/* 消息表的一项：由调用方提供（真实系统里由代码生成器产出） */
typedef struct {
    uint32_t msgid;
    uint8_t len;          /* 完整负载长度（含扩展字段） */
    uint8_t crc_extra;
} mavlink_msg_info_t;

typedef enum {
    MAVLINK_NONE = 0,     /* 还没收完一帧 */
    MAVLINK_OK,           /* 收到一条消息 */
    MAVLINK_BAD_CRC,      /* 校验错 */
    MAVLINK_UNKNOWN_MSG,  /* 消息号不在消息表里 */
    MAVLINK_BAD_LEN,      /* 长度与消息表不符 */
    MAVLINK_BAD_FLAGS     /* v2 不兼容标志里有不认识的位 */
} mavlink_result_t;

typedef struct {
    uint8_t version;      /* 1 或 2 */
    uint8_t seq;
    uint8_t sysid;
    uint8_t compid;
    uint32_t msgid;
    uint8_t len;          /* 补齐后的负载长度 = 消息表里的长度 */
    bool is_signed;
    uint8_t payload[MAVLINK_MAX_PAYLOAD];
} mavlink_message_t;

typedef struct {
    uint32_t received;    /* 收到的好消息 */
    uint32_t bad_crc;     /* 校验错的帧 */
    uint32_t dropped;     /* 因未知消息、长度不对、不认识的标志而丢弃的帧 */
    uint32_t lost;        /* 按序号推算丢失的消息数 */
} mavlink_stats_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t sysid;
    uint8_t compid;
    uint8_t last_seq;
} mavlink_source_t;

typedef struct {
    const mavlink_msg_info_t *table;
    size_t table_count;
    uint8_t buf[MAVLINK_MAX_FRAME];     /* 当前正在接收的帧（从起始字节开始） */
    uint16_t len;                       /* buf 中已收到的字节数，0 表示在等起始字节 */
    mavlink_message_t msg;              /* 最近一条好消息 */
    bool has_msg;
    mavlink_stats_t stats;
    mavlink_source_t sources[MAVLINK_MAX_SOURCES];
    uint8_t source_count;
} mavlink_parser_t;

bool                     mavlink_parser_init(mavlink_parser_t *p, const mavlink_msg_info_t *table, size_t count);
mavlink_result_t         mavlink_parser_feed(mavlink_parser_t *p, uint8_t byte);
const mavlink_message_t *mavlink_parser_message(const mavlink_parser_t *p);
void                     mavlink_parser_stats(const mavlink_parser_t *p, mavlink_stats_t *out);

#endif /* MAVLINK_PARSER_H */
