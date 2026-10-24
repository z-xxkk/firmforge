#include "button_events.h"

#include <stddef.h>

enum {
    ST_IDLE = 0,   /* 等待按下 */
    ST_DOWN,       /* 按下中，还没到长按 */
    ST_WAIT,       /* 短按松开后，在双击窗口里等待 */
    ST_LONG        /* 长按中 */
};

void button_init(button_t *b, uint16_t click_ticks, uint16_t press_ticks)
{
    if (b == NULL) {
        return;
    }
    b->click_ticks = (click_ticks == 0u) ? (uint16_t)BUTTON_DEFAULT_CLICK_TICKS : click_ticks;
    b->press_ticks = (press_ticks == 0u) ? (uint16_t)BUTTON_DEFAULT_PRESS_TICKS : press_ticks;
    b->counter = 0u;
    b->state = ST_IDLE;
    b->clicks = 0u;
}

/* 进入按下状态（这个 tick 算按住的第 1 个），按住时长达到阈值就转长按 */
static button_event_t enter_down(button_t *b)
{
    b->state = ST_DOWN;
    b->counter = 1u;
    if (b->counter >= b->press_ticks) {
        b->state = ST_LONG;
        b->clicks = 0u;
        return BUTTON_EVT_LONG_PRESS_START;
    }
    return BUTTON_EVT_NONE;
}

button_event_t button_update(button_t *b, bool pressed)
{
    if (b == NULL) {
        return BUTTON_EVT_NONE;
    }
    switch (b->state) {
    case ST_IDLE:
        if (pressed) {
            b->clicks = 0u;
            return enter_down(b);
        }
        return BUTTON_EVT_NONE;

    case ST_DOWN:
        if (pressed) {
            if (b->counter < UINT16_MAX) {
                b->counter++;
            }
            if (b->counter >= b->press_ticks) {
                b->state = ST_LONG;
                b->clicks = 0u;
                return BUTTON_EVT_LONG_PRESS_START;
            }
            return BUTTON_EVT_NONE;
        }
        b->clicks++;
        if (b->clicks >= 2u) {
            b->state = ST_IDLE;
            b->clicks = 0u;
            return BUTTON_EVT_DOUBLE_CLICK;
        }
        b->state = ST_WAIT;
        b->counter = 0u;
        return BUTTON_EVT_NONE;

    case ST_WAIT:
        if (pressed) {
            return enter_down(b);
        }
        b->counter++;
        if (b->counter >= b->click_ticks) {
            b->state = ST_IDLE;
            b->clicks = 0u;
            return BUTTON_EVT_SINGLE_CLICK;
        }
        return BUTTON_EVT_NONE;

    case ST_LONG:
        if (!pressed) {
            b->state = ST_IDLE;
            return BUTTON_EVT_LONG_PRESS_END;
        }
        return BUTTON_EVT_NONE;

    default:
        b->state = ST_IDLE;
        return BUTTON_EVT_NONE;
    }
}
