#ifndef RINGBUF_H
#define RINGBUF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t *buf;      /* 调用方提供的存储区 */
    size_t capacity;   /* 存储区字节数 */
    size_t head;       /* 下一个写入位置 */
    size_t tail;       /* 下一个读出位置 */
    size_t count;      /* 已存字节数 */
} ringbuf_t;

bool   ringbuf_init(ringbuf_t *rb, uint8_t *storage, size_t capacity);
bool   ringbuf_put(ringbuf_t *rb, uint8_t byte);
bool   ringbuf_get(ringbuf_t *rb, uint8_t *out);
size_t ringbuf_count(const ringbuf_t *rb);
size_t ringbuf_free(const ringbuf_t *rb);
bool   ringbuf_is_empty(const ringbuf_t *rb);
bool   ringbuf_is_full(const ringbuf_t *rb);
size_t ringbuf_write(ringbuf_t *rb, const uint8_t *data, size_t len);
size_t ringbuf_read(ringbuf_t *rb, uint8_t *out, size_t len);
void   ringbuf_clear(ringbuf_t *rb);

#endif /* RINGBUF_H */
