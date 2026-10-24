#include "median_filter.h"

void median_filter_init(median_filter_t *f)
{
    if (f == NULL) {
        return;
    }
    for (uint8_t i = 0u; i < MEDIAN_FILTER_WINDOW; i++) {
        f->samples[i] = 0;
    }
    f->head = 0u;
    f->count = 0u;
}

void median_filter_reset(median_filter_t *f)
{
    median_filter_init(f);
}

uint8_t median_filter_count(const median_filter_t *f)
{
    return f == NULL ? 0u : f->count;
}

/* 复制当前窗口到临时数组排序，不改动窗口本身的先后顺序 */
static int16_t median_of(const median_filter_t *f)
{
    int16_t sorted[MEDIAN_FILTER_WINDOW] = {0};
    uint8_t n = f->count;

    for (uint8_t i = 0u; i < n; i++) {
        int16_t v = f->samples[i];
        uint8_t j = i;
        while (j > 0u && sorted[j - 1u] > v) {
            sorted[j] = sorted[j - 1u];
            j--;
        }
        sorted[j] = v;
    }
    if ((n % 2u) == 1u) {
        return sorted[n / 2u];
    }
    return (int16_t)(((int32_t)sorted[(n / 2u) - 1u] + (int32_t)sorted[n / 2u]) / 2);
}

bool median_filter_push(median_filter_t *f, int16_t sample, int16_t *out)
{
    if (f == NULL || out == NULL) {
        return false;
    }
    f->samples[f->head] = sample;
    f->head = (uint8_t)((f->head + 1u) % MEDIAN_FILTER_WINDOW);
    if (f->count < MEDIAN_FILTER_WINDOW) {
        f->count++;
    }
    *out = median_of(f);
    return true;
}

bool median_filter_get(const median_filter_t *f, int16_t *out)
{
    if (f == NULL || out == NULL || f->count == 0u) {
        return false;
    }
    *out = median_of(f);
    return true;
}
