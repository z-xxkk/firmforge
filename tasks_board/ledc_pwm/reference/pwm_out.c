#include "driver/ledc.h"
#include "hw_config.h"
#include "pwm_out.h"

#define PWM_MODE    LEDC_LOW_SPEED_MODE     /* ESP32-S3 的 LEDC 只有低速模式 */
#define PWM_TIMER   LEDC_TIMER_0
#define PWM_CHANNEL LEDC_CHANNEL_0
#define SRC_CLK_HZ  80000000u               /* APB 时钟 */
#define MAX_BITS    14u                     /* SOC_LEDC_TIMER_BIT_WIDTH */

static uint32_t s_bits;
static bool s_on;

static uint32_t duty_value(uint32_t permille)
{
    return (uint32_t)(((uint64_t)permille << s_bits) / 1000u);
}

esp_err_t pwm_init(uint32_t freq_hz, uint32_t duty_permille)
{
    if (freq_hz < 100 || freq_hz > 100000 || duty_permille > 1000) {
        return ESP_ERR_INVALID_ARG;
    }
    uint32_t bits = ledc_find_suitable_duty_resolution(SRC_CLK_HZ, freq_hz);
    if (bits > MAX_BITS) {
        bits = MAX_BITS;
    }
    if (bits == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    s_bits = bits;
    ledc_timer_config_t t = {
        .speed_mode = PWM_MODE, .duty_resolution = (ledc_timer_bit_t)bits, .timer_num = PWM_TIMER,
        .freq_hz = freq_hz, .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t err = ledc_timer_config(&t);
    if (err != ESP_OK) {
        return err;
    }
    ledc_channel_config_t c = {
        .gpio_num = FF_PIN_PWM_OUT, .speed_mode = PWM_MODE, .channel = PWM_CHANNEL,
        .timer_sel = PWM_TIMER, .duty = duty_value(duty_permille), .hpoint = 0,
    };
    err = ledc_channel_config(&c);
    s_on = (err == ESP_OK);
    return err;
}

esp_err_t pwm_set_duty(uint32_t duty_permille)
{
    if (duty_permille > 1000) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_on) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = ledc_set_duty(PWM_MODE, PWM_CHANNEL, duty_value(duty_permille));
    if (err != ESP_OK) {
        return err;
    }
    return ledc_update_duty(PWM_MODE, PWM_CHANNEL);     /* 不调这一步，新占空比不生效 */
}

void pwm_deinit(void)
{
    if (s_on) {
        ledc_stop(PWM_MODE, PWM_CHANNEL, 0);
        s_on = false;
    }
}
