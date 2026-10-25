#ifndef PID_CTRL_H
#define PID_CTRL_H

#include <stdbool.h>
#include <stdint.h>

#define PID_GAIN_ONE  1024        /* 增益的单位是 1/1024：增益 1.0 写成 1024 */
#define PID_GAIN_MAX  1048576     /* 增益上限 = 1024.0，保证 64 位中间结果不溢出 */

/* 字段属于实现私有，调用方不要直接访问 */
typedef struct {
    int32_t kp, ki, kd;      /* 每次采样的增益，单位 1/1024 */
    int32_t out_min, out_max;
    int64_t iterm;           /* 积分项，放大 1024 倍保存 */
    int32_t last_input;
    int32_t output;
    bool automatic;
} pid_ctrl_t;

bool    pid_init(pid_ctrl_t *pid, int32_t kp, int32_t ki, int32_t kd, int32_t out_min, int32_t out_max);
bool    pid_set_tunings(pid_ctrl_t *pid, int32_t kp, int32_t ki, int32_t kd);
bool    pid_set_limits(pid_ctrl_t *pid, int32_t out_min, int32_t out_max);
void    pid_set_auto(pid_ctrl_t *pid, bool automatic, int32_t input);
bool    pid_set_output(pid_ctrl_t *pid, int32_t output);
int32_t pid_compute(pid_ctrl_t *pid, int32_t setpoint, int32_t input);
int32_t pid_output(const pid_ctrl_t *pid);
bool    pid_is_auto(const pid_ctrl_t *pid);

#endif /* PID_CTRL_H */
