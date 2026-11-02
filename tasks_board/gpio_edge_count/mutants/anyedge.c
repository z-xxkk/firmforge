#include <stdatomic.h>
#include "driver/gpio.h"
#include "esp_attr.h"
#include "hw_config.h"
#include "edge_counter.h"

static atomic_uint_fast32_t s_count;
static bool s_isr_installed_by_us;

static void IRAM_ATTR on_edge(void *arg)
{
    (void)arg;
    atomic_fetch_add_explicit(&s_count, 1, memory_order_relaxed);
}

esp_err_t edge_counter_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << FF_PIN_PWM_IN,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    esp_err_t err = gpio_config(&io);
    if (err != ESP_OK) {
        return err;
    }
    err = gpio_install_isr_service(0);
    if (err == ESP_OK) {
        s_isr_installed_by_us = true;
    } else if (err != ESP_ERR_INVALID_STATE) {      /* 已经被别人装过：当作成功 */
        return err;
    }
    atomic_store(&s_count, 0);
    return gpio_isr_handler_add(FF_PIN_PWM_IN, on_edge, NULL);
}

uint32_t edge_counter_get(void)
{
    return (uint32_t)atomic_load(&s_count);
}

void edge_counter_reset(void)
{
    atomic_store(&s_count, 0);
}

void edge_counter_deinit(void)
{
    gpio_isr_handler_remove(FF_PIN_PWM_IN);
    gpio_set_intr_type(FF_PIN_PWM_IN, GPIO_INTR_DISABLE);
    if (s_isr_installed_by_us) {
        gpio_uninstall_isr_service();
        s_isr_installed_by_us = false;
    }
}
