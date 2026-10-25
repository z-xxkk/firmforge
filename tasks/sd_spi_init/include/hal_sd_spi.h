#ifndef HAL_SD_SPI_H
#define HAL_SD_SPI_H

#include <stdbool.h>
#include <stdint.h>

/* SD 卡的 SPI 接口（由使用方实现）。 */

/* 片选：selected 为 true 时拉低片选（选中卡），false 时拉高。 */
void hal_sd_select(bool selected);

/* 全双工交换一个字节：发出 out 的同时收回一个字节。 */
uint8_t hal_sd_xfer(uint8_t out);

#endif /* HAL_SD_SPI_H */
