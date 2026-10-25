#ifndef STEPPER_RAMP_H
#define STEPPER_RAMP_H

#include <stdbool.h>
#include <stdint.h>

/* 计时器 1 MHz、每圈 200 步。下面的常数按 AVR446 的公式算出后截掉小数：
 * α = 2π / 200；A_T_x100 = α × 频率 × 100；T1_FREQ_148 = 0.676 × 频率 ÷ 100；
 * A_SQ = 2α × 10^10；A_x20000 = α × 20000 */
#define STEPPER_T1_FREQ      1000000u
#define STEPPER_SPR          200u
#define STEPPER_A_T_X100     3141592u
#define STEPPER_T1_FREQ_148  6760u
#define STEPPER_A_SQ         628318530u
#define STEPPER_A_X20000     628u

#define STEPPER_MAX_STEPS    64000
#define STEPPER_MIN_ACCEL    71u      /* 加减速度单位 0.01 rad/s² */
#define STEPPER_MAX_ACCEL    32000u
#define STEPPER_MIN_SPEED    12u      /* 速度单位 0.01 rad/s */
#define STEPPER_MAX_SPEED    32000u

typedef enum {
    STEPPER_STOP = 0,
    STEPPER_ACCEL,
    STEPPER_RUN,
    STEPPER_DECEL
} stepper_state_t;

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    uint8_t state;            /* stepper_state_t */
    int8_t dir;               /* +1 或 −1 */
    uint32_t step_delay;      /* 下一步之前要等的计时器周期数 */
    uint32_t min_delay;
    uint32_t last_accel_delay;
    uint32_t decel_start;
    uint32_t step_count;
    int32_t decel_val;
    int32_t accel_count;
    int32_t rest;
    int32_t position;
} stepper_t;

uint32_t        stepper_isqrt(uint32_t x);
void            stepper_init(stepper_t *s);
bool            stepper_move(stepper_t *s, int32_t steps, uint32_t accel, uint32_t decel, uint32_t speed);
bool            stepper_next(stepper_t *s, uint32_t *delay);
stepper_state_t stepper_state(const stepper_t *s);
int32_t         stepper_position(const stepper_t *s);

#endif /* STEPPER_RAMP_H */
