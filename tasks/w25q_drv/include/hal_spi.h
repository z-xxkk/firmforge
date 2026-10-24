#ifndef HAL_SPI_H
#define HAL_SPI_H

#include <stddef.h>
#include <stdint.h>

typedef enum {
    HAL_SPI_OK = 0,
    HAL_SPI_ERROR = 1
} hal_spi_status_t;

/* 一次调用 = 一次完整的片选周期：
 * 拉低片选 → 发送 tx[0..tx_len) → 接收 rx_len 个字节到 rx → 拉高片选。
 * tx_len 至少为 1；rx_len 为 0 时 rx 可以为 NULL。 */
hal_spi_status_t hal_spi_transfer(const uint8_t *tx, size_t tx_len, uint8_t *rx, size_t rx_len);

#endif /* HAL_SPI_H */
