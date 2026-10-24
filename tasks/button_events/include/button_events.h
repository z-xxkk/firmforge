#ifndef BUTTON_EVENTS_H
#define BUTTON_EVENTS_H

#include <stdbool.h>
#include <stdint.h>

#define BUTTON_DEFAULT_CLICK_TICKS 400u
#define BUTTON_DEFAULT_PRESS_TICKS 800u

typedef enum {
    BUTTON_EVT_NONE = 0,
    BUTTON_EVT_SINGLE_CLICK,
    BUTTON_EVT_DOUBLE_CLICK,
    BUTTON_EVT_LONG_PRESS_START,
    BUTTON_EVT_LONG_PRESS_END
} button_event_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint16_t click_ticks;   /* 双击窗口 */
    uint16_t press_ticks;   /* 长按阈值 */
    uint16_t counter;       /* 当前状态已经持续的 tick 数 */
    uint8_t state;          /* 内部状态，取值由实现自行定义 */
    uint8_t clicks;         /* 本轮已完成的短按次数 */
} button_t;

void           button_init(button_t *b, uint16_t click_ticks, uint16_t press_ticks);
button_event_t button_update(button_t *b, bool pressed);

#endif /* BUTTON_EVENTS_H */
