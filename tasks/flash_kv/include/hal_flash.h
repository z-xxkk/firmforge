#ifndef HAL_FLASH_H
#define HAL_FLASH_H

#include <stddef.h>
#include <stdint.h>

/* 片内 NOR Flash 接口（由使用方实现）：共 2 个扇区，每个 1024 字节，地址 0–2047。 */
#define HAL_FLASH_SECTOR_SIZE   1024u
#define HAL_FLASH_SECTOR_COUNT  2u

typedef enum {
    HAL_FLASH_OK = 0,
    HAL_FLASH_ERROR = 1
} hal_flash_status_t;

hal_flash_status_t hal_flash_read(uint32_t addr, uint8_t *buf, size_t len);

/* 写入只能把位从 1 变成 0（擦除后为 FFh）；已经是 0 的位写 1 不会变回 1。 */
hal_flash_status_t hal_flash_write(uint32_t addr, const uint8_t *data, size_t len);

/* 擦除一个扇区（sector 为 0 或 1），整个扇区变回 FFh。 */
hal_flash_status_t hal_flash_erase(uint32_t sector);

#endif /* HAL_FLASH_H */
