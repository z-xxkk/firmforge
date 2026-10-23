#include "debounce.h"

#include <stddef.h>

void debounce_init(debounce_t *d, uint16_t threshold_ticks, bool initial_pressed)
{
    if (d == NULL) {
        return;
    }
    d->threshold = (threshold_ticks == 0u) ? 1u : threshold_ticks;
    d->counter = 0u;
    d->stable_pressed = initial_pressed;
}

btn_event_t debounce_update(debounce_t *d, bool raw_pressed)
{
    if (d == NULL) {
        return BTN_EVT_NONE;
    }
    if (raw_pressed == d->stable_pressed) {
        d->counter = 0u;
        return BTN_EVT_NONE;
    }
    d->counter++;
    if (d->counter > d->threshold) {   /* 变异：差一 */
        d->stable_pressed = raw_pressed;
        d->counter = 0u;
        return raw_pressed ? BTN_EVT_PRESS : BTN_EVT_RELEASE;
    }
    return BTN_EVT_NONE;
}

bool debounce_is_pressed(const debounce_t *d)
{
    return d != NULL && d->stable_pressed;
}
