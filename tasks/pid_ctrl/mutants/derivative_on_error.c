#include "pid_ctrl.h"

#include <stddef.h>

static int64_t clamp64(int64_t v, int64_t lo, int64_t hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

static bool gains_ok(int32_t kp, int32_t ki, int32_t kd)
{
    return kp >= 0 && ki >= 0 && kd >= 0 && kp <= PID_GAIN_MAX && ki <= PID_GAIN_MAX && kd <= PID_GAIN_MAX;
}

static void clamp_state(pid_ctrl_t *pid)
{
    pid->iterm = clamp64(pid->iterm, (int64_t)pid->out_min * PID_GAIN_ONE, (int64_t)pid->out_max * PID_GAIN_ONE);
    pid->output = (int32_t)clamp64(pid->output, pid->out_min, pid->out_max);
}

bool pid_init(pid_ctrl_t *pid, int32_t kp, int32_t ki, int32_t kd, int32_t out_min, int32_t out_max)
{
    if (pid == NULL || !gains_ok(kp, ki, kd) || out_min >= out_max) {
        return false;
    }
    pid->kp = kp;
    pid->ki = ki;
    pid->kd = kd;
    pid->out_min = out_min;
    pid->out_max = out_max;
    pid->iterm = 0;
    pid->last_input = 0;
    pid->output = 0;
    pid->automatic = false;
    clamp_state(pid);
    return true;
}

bool pid_set_tunings(pid_ctrl_t *pid, int32_t kp, int32_t ki, int32_t kd)
{
    if (pid == NULL || !gains_ok(kp, ki, kd)) {
        return false;
    }
    pid->kp = kp;
    pid->ki = ki;
    pid->kd = kd;             /* 积分项里已经乘过各时刻的 Ki，改 Ki 不影响它 */
    return true;
}

bool pid_set_limits(pid_ctrl_t *pid, int32_t out_min, int32_t out_max)
{
    if (pid == NULL || out_min >= out_max) {
        return false;
    }
    pid->out_min = out_min;
    pid->out_max = out_max;
    clamp_state(pid);
    return true;
}

void pid_set_auto(pid_ctrl_t *pid, bool automatic, int32_t input)
{
    if (pid == NULL) {
        return;
    }
    if (automatic && !pid->automatic) {      /* 手动 → 自动：无扰切换 */
        pid->iterm = (int64_t)pid->output * PID_GAIN_ONE;
        pid->last_input = input;
        clamp_state(pid);
    }
    pid->automatic = automatic;
}

bool pid_set_output(pid_ctrl_t *pid, int32_t output)
{
    if (pid == NULL || pid->automatic) {
        return false;
    }
    pid->output = (int32_t)clamp64(output, pid->out_min, pid->out_max);
    return true;
}

int32_t pid_compute(pid_ctrl_t *pid, int32_t setpoint, int32_t input)
{
    if (pid == NULL) {
        return 0;
    }
    if (!pid->automatic) {
        return pid->output;
    }
    int64_t error = (int64_t)setpoint - input;
    pid->iterm += (int64_t)pid->ki * error;
    pid->iterm = clamp64(pid->iterm, (int64_t)pid->out_min * PID_GAIN_ONE, (int64_t)pid->out_max * PID_GAIN_ONE);
    int64_t d_input = -(error - pid->last_input);   /* 变异：对误差求微分（last_input 里存的是上次误差） */
    int64_t out = ((int64_t)pid->kp * error + pid->iterm - (int64_t)pid->kd * d_input) / PID_GAIN_ONE;
    pid->output = (int32_t)clamp64(out, pid->out_min, pid->out_max);
    pid->last_input = (int32_t)error;
    return pid->output;
}

int32_t pid_output(const pid_ctrl_t *pid)
{
    return pid == NULL ? 0 : pid->output;
}

bool pid_is_auto(const pid_ctrl_t *pid)
{
    return pid != NULL && pid->automatic;
}
