#ifndef MEDIAN_FILTER_H
#define MEDIAN_FILTER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MEDIAN_FILTER_WINDOW 5u

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    int16_t samples[MEDIAN_FILTER_WINDOW];  /* 环形存放最近的读数 */
    uint8_t head;                           /* 下一个读数写入的位置 */
    uint8_t count;                          /* 当前读数个数（0..MEDIAN_FILTER_WINDOW） */
} median_filter_t;

void    median_filter_init(median_filter_t *f);
bool    median_filter_push(median_filter_t *f, int16_t sample, int16_t *out);
bool    median_filter_get(const median_filter_t *f, int16_t *out);
uint8_t median_filter_count(const median_filter_t *f);
void    median_filter_reset(median_filter_t *f);

#endif /* MEDIAN_FILTER_H */
