#ifndef DEBOUNCE_H
#define DEBOUNCE_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    BTN_EVT_NONE = 0,
    BTN_EVT_PRESS,
    BTN_EVT_RELEASE
} btn_event_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint16_t threshold;     /* 需要连续多少个 tick 才确认翻转 */
    uint16_t counter;       /* 当前已连续累计的 tick 数 */
    bool stable_pressed;    /* 当前稳定状态 */
} debounce_t;

void        debounce_init(debounce_t *d, uint16_t threshold_ticks, bool initial_pressed);
btn_event_t debounce_update(debounce_t *d, bool raw_pressed);
bool        debounce_is_pressed(const debounce_t *d);

#endif /* DEBOUNCE_H */
