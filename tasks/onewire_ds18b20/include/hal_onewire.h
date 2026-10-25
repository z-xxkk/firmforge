#ifndef HAL_ONEWIRE_H
#define HAL_ONEWIRE_H

#include <stdbool.h>
#include <stdint.h>

/* 1-Wire 总线最底层的三个操作（由使用方实现；时序在 HAL 里处理）。
 * 总线是"线与"：任何一个设备拉低，读到的就是 0。 */

/* 复位脉冲。有设备应答（presence）返回 true，总线上没有设备返回 false。 */
bool hal_ow_reset(void);

/* 写一个位：bit 为 0 或 1。 */
void hal_ow_write_bit(uint8_t bit);

/* 读一个位（读时隙），返回 0 或 1。 */
uint8_t hal_ow_read_bit(void);

#endif /* HAL_ONEWIRE_H */
