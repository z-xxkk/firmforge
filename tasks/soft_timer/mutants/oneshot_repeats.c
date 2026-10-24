#include "soft_timer.h"

#include <stddef.h>

void soft_timer_init(soft_timer_t *st)
{
    if (st == NULL) {
        return;
    }
    for (uint8_t i = 0u; i < SOFT_TIMER_COUNT; i++) {
        st->slots[i].period = 0u;
        st->slots[i].remaining = 0u;
        st->slots[i].running = false;
        st->slots[i].periodic = false;
    }
}

bool soft_timer_start(soft_timer_t *st, uint8_t id, uint32_t period_ticks, bool periodic)
{
    if (st == NULL || id >= SOFT_TIMER_COUNT || period_ticks == 0u) {
        return false;
    }
    st->slots[id].period = period_ticks;
    st->slots[id].remaining = period_ticks;
    st->slots[id].periodic = periodic;
    st->slots[id].running = true;
    return true;
}

bool soft_timer_stop(soft_timer_t *st, uint8_t id)
{
    if (st == NULL || id >= SOFT_TIMER_COUNT) {
        return false;
    }
    st->slots[id].running = false;
    st->slots[id].remaining = 0u;
    return true;
}

uint8_t soft_timer_tick(soft_timer_t *st)
{
    uint8_t fired = 0u;
    if (st == NULL) {
        return 0u;
    }
    for (uint8_t i = 0u; i < SOFT_TIMER_COUNT; i++) {
        soft_timer_slot_t *s = &st->slots[i];
        if (!s->running) {
            continue;
        }
        s->remaining--;
        if (s->remaining == 0u) {
            fired = (uint8_t)(fired | (uint8_t)(1u << i));
            s->remaining = s->period;   /* 变异：单次定时器也重装，没有停止 */
        }
    }
    return fired;
}

bool soft_timer_is_running(const soft_timer_t *st, uint8_t id)
{
    return st != NULL && id < SOFT_TIMER_COUNT && st->slots[id].running;
}

uint32_t soft_timer_remaining(const soft_timer_t *st, uint8_t id)
{
    if (!soft_timer_is_running(st, id)) {
        return 0u;
    }
    return st->slots[id].remaining;
}
