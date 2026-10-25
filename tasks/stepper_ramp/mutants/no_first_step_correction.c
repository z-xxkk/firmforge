#include "stepper_ramp.h"

#include <stddef.h>

uint32_t stepper_isqrt(uint32_t x)
{
    uint32_t root = 0;
    uint32_t bit = 1u << 30;
    while (bit > x) {
        bit >>= 2;
    }
    while (bit != 0u) {
        if (x >= root + bit) {
            x -= root + bit;
            root = (root >> 1) + bit;
        } else {
            root >>= 1;
        }
        bit >>= 2;
    }
    return root;
}

void stepper_init(stepper_t *s)
{
    if (s == NULL) {
        return;
    }
    s->state = (uint8_t)STEPPER_STOP;
    s->dir = 1;
    s->step_delay = 0;
    s->min_delay = 0;
    s->last_accel_delay = 0;
    s->decel_start = 0;
    s->step_count = 0;
    s->decel_val = 0;
    s->accel_count = 0;
    s->rest = 0;
    s->position = 0;
}

bool stepper_move(stepper_t *s, int32_t steps, uint32_t accel, uint32_t decel, uint32_t speed)
{
    if (s == NULL || s->state != (uint8_t)STEPPER_STOP || steps == 0 || steps > STEPPER_MAX_STEPS
        || steps < -STEPPER_MAX_STEPS || accel < STEPPER_MIN_ACCEL || accel > STEPPER_MAX_ACCEL
        || decel < STEPPER_MIN_ACCEL || decel > STEPPER_MAX_ACCEL || speed < STEPPER_MIN_SPEED
        || speed > STEPPER_MAX_SPEED) {
        return false;
    }
    s->dir = steps < 0 ? -1 : 1;
    uint32_t n = (uint32_t)(steps < 0 ? -steps : steps);
    uint32_t c0 = (uint32_t)((uint64_t)(STEPPER_T1_FREQ / 100u) * stepper_isqrt(STEPPER_A_SQ / accel) / 100u);
    s->step_count = 0;
    s->rest = 0;
    s->accel_count = 0;
    s->min_delay = STEPPER_A_T_X100 / speed;
    if (n == 1u) {
        s->accel_count = -1;
        s->step_delay = c0;
        s->state = (uint8_t)STEPPER_DECEL;
        return true;
    }
    uint64_t max_s_lim = (uint64_t)speed * speed / ((uint64_t)STEPPER_A_X20000 * accel / 100u);
    if (max_s_lim == 0u) {
        max_s_lim = 1;
    }
    uint64_t accel_lim = (uint64_t)n * decel / (accel + decel);
    if (accel_lim == 0u) {
        accel_lim = 1;
    }
    if (accel_lim <= max_s_lim) {
        s->decel_val = (int32_t)((int64_t)accel_lim - (int64_t)n);           /* 三角形：没到最高速度就减速 */
    } else {
        s->decel_val = -(int32_t)(max_s_lim * accel / decel);                 /* 梯形 */
    }
    if (s->decel_val == 0) {
        s->decel_val = -1;
    }
    s->decel_start = (uint32_t)((int64_t)n + s->decel_val);
    s->last_accel_delay = s->min_delay;
    if (c0 <= s->min_delay) {
        s->step_delay = s->min_delay;
        s->state = (uint8_t)STEPPER_RUN;
    } else {
        s->step_delay = c0;
        s->state = (uint8_t)STEPPER_ACCEL;
    }
    return true;
}

/* new = d − (2d + rest) ÷ (4·accel_count + 1)；余数留到下一步（C 语言整数除法：向零取整） */
static uint32_t ramp(stepper_t *s)
{
    int64_t num = 2 * (int64_t)s->step_delay + s->rest;
    int64_t den = 4 * (int64_t)s->accel_count + 1;
    s->rest = (int32_t)(num % den);
    return (uint32_t)((int64_t)s->step_delay - num / den);
}

bool stepper_next(stepper_t *s, uint32_t *delay)
{
    uint32_t next;
    if (s == NULL || delay == NULL || s->state == (uint8_t)STEPPER_STOP) {
        return false;
    }
    *delay = s->step_delay;
    s->position += s->dir;
    s->step_count++;
    switch (s->state) {
    case STEPPER_ACCEL:
        s->accel_count++;
        next = ramp(s);
        if (s->step_count >= s->decel_start) {
            s->accel_count = s->decel_val;
            s->state = (uint8_t)STEPPER_DECEL;
        } else if (next <= s->min_delay) {
            s->last_accel_delay = next;
            next = s->min_delay;
            s->rest = 0;
            s->state = (uint8_t)STEPPER_RUN;
        }
        break;
    case STEPPER_RUN:
        next = s->min_delay;
        if (s->step_count >= s->decel_start) {
            s->accel_count = s->decel_val;
            next = s->last_accel_delay;
            s->state = (uint8_t)STEPPER_DECEL;
        }
        break;
    default:                                       /* DECEL */
        s->accel_count++;
        next = ramp(s);
        if (s->accel_count >= 0) {
            s->state = (uint8_t)STEPPER_STOP;
        }
        break;
    }
    s->step_delay = next;
    return true;
}

stepper_state_t stepper_state(const stepper_t *s)
{
    return s == NULL ? STEPPER_STOP : (stepper_state_t)s->state;
}

int32_t stepper_position(const stepper_t *s)
{
    return s == NULL ? 0 : s->position;
}
