/* 变异体：衰减用 0 dB */
#include <stddef.h>
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "hw_config.h"
#include "volt.h"

#define SAMPLES 16

static adc_oneshot_unit_handle_t s_unit;
static adc_cali_handle_t s_cali;
static adc_channel_t s_chan;

esp_err_t volt_init(void)
{
    if (s_unit != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    adc_unit_t unit;
    esp_err_t err = adc_oneshot_io_to_channel(FF_PIN_ADC_IN, &unit, &s_chan);
    if (err != ESP_OK) {
        return err;
    }
    adc_oneshot_unit_init_cfg_t ucfg = {.unit_id = unit};
    if ((err = adc_oneshot_new_unit(&ucfg, &s_unit)) != ESP_OK) {
        s_unit = NULL;
        return err;
    }
    adc_oneshot_chan_cfg_t ccfg = {.atten = ADC_ATTEN_DB_0, .bitwidth = ADC_BITWIDTH_DEFAULT};
    adc_cali_curve_fitting_config_t kcfg = {.unit_id = unit, .chan = s_chan, .atten = ADC_ATTEN_DB_0,
                                            .bitwidth = ADC_BITWIDTH_DEFAULT};
    if ((err = adc_oneshot_config_channel(s_unit, s_chan, &ccfg)) != ESP_OK ||
        (err = adc_cali_create_scheme_curve_fitting(&kcfg, &s_cali)) != ESP_OK) {
        adc_oneshot_del_unit(s_unit);
        s_unit = NULL;
        s_cali = NULL;
        return err;
    }
    return ESP_OK;
}

esp_err_t volt_read_mv(int *mv)
{
    if (mv == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_unit == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    int sum = 0;
    for (int i = 0; i < SAMPLES; i++) {
        int v = 0;
        esp_err_t err = adc_oneshot_get_calibrated_result(s_unit, s_cali, s_chan, &v);
        if (err != ESP_OK) {
            return err;
        }
        sum += v;
    }
    *mv = sum / SAMPLES;
    return ESP_OK;
}

void volt_deinit(void)
{
    if (s_cali != NULL) {
        adc_cali_delete_scheme_curve_fitting(s_cali);
        s_cali = NULL;
    }
    if (s_unit != NULL) {
        adc_oneshot_del_unit(s_unit);
        s_unit = NULL;
    }
}
