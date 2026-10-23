#include "ringbuf.h"

/* 一张 2 KB 的常量表，放进 Flash 后超出 512 字节预算 */
#define PAD16(x) x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x
#define PAD256(x) PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), \
                  PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x), PAD16(x)
const uint8_t ringbuf_pad_table[2048] = {
    PAD256(1), PAD256(2), PAD256(3), PAD256(4), PAD256(5), PAD256(6), PAD256(7), PAD256(8)
};

bool ringbuf_init(ringbuf_t *rb, uint8_t *storage, size_t capacity)
{
    if (rb == NULL || storage == NULL || capacity == 0u) {
        return false;
    }
    rb->buf = storage;
    rb->capacity = capacity;
    rb->head = 0u;
    rb->tail = 0u;
    rb->count = 0u;
    return true;
}

bool ringbuf_put(ringbuf_t *rb, uint8_t byte)
{
    if (rb == NULL || rb->count >= rb->capacity) {
        return false;
    }
    rb->buf[rb->head] = byte;
    rb->head = (rb->head + 1u) % rb->capacity;
    rb->count++;
    return true;
}

bool ringbuf_get(ringbuf_t *rb, uint8_t *out)
{
    if (rb == NULL || out == NULL || rb->count == 0u) {
        return false;
    }
    *out = rb->buf[rb->tail];
    rb->tail = (rb->tail + 1u) % rb->capacity;
    rb->count--;
    return true;
}

size_t ringbuf_count(const ringbuf_t *rb)
{
    return rb == NULL ? 0u : rb->count;
}

size_t ringbuf_free(const ringbuf_t *rb)
{
    return rb == NULL ? 0u : rb->capacity - rb->count;
}

bool ringbuf_is_empty(const ringbuf_t *rb)
{
    return rb == NULL || rb->count == 0u;
}

bool ringbuf_is_full(const ringbuf_t *rb)
{
    return rb != NULL && rb->count == rb->capacity;
}

size_t ringbuf_write(ringbuf_t *rb, const uint8_t *data, size_t len)
{
    size_t n = 0u;
    if (rb == NULL || data == NULL) {
        return 0u;
    }
    while (n < len && ringbuf_put(rb, data[n])) {
        n++;
    }
    return n;
}

size_t ringbuf_read(ringbuf_t *rb, uint8_t *out, size_t len)
{
    size_t n = 0u;
    if (rb == NULL || out == NULL) {
        return 0u;
    }
    while (n < len && ringbuf_get(rb, &out[n])) {
        n++;
    }
    return n;
}

void ringbuf_clear(ringbuf_t *rb)
{
    if (rb != NULL) {
        rb->head = 0u;
        rb->tail = ringbuf_pad_table[0] - 1u;
        rb->count = 0u;
    }
}
