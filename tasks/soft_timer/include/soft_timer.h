#ifndef SOFT_TIMER_H
#define SOFT_TIMER_H

#include <stdbool.h>
#include <stdint.h>

#define SOFT_TIMER_COUNT 8u

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint32_t period;      /* 周期（tick 数） */
    uint32_t remaining;   /* 距离到期还剩几次 tick */
    bool running;
    bool periodic;        /* true = 循环，false = 单次 */
} soft_timer_slot_t;

typedef struct {
    soft_timer_slot_t slots[SOFT_TIMER_COUNT];
} soft_timer_t;

void     soft_timer_init(soft_timer_t *st);
bool     soft_timer_start(soft_timer_t *st, uint8_t id, uint32_t period_ticks, bool periodic);
bool     soft_timer_stop(soft_timer_t *st, uint8_t id);
uint8_t  soft_timer_tick(soft_timer_t *st);
bool     soft_timer_is_running(const soft_timer_t *st, uint8_t id);
uint32_t soft_timer_remaining(const soft_timer_t *st, uint8_t id);

#endif /* SOFT_TIMER_H */
