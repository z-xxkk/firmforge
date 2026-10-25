#ifndef ONEWIRE_DS18B20_H
#define ONEWIRE_DS18B20_H

#include <stddef.h>
#include <stdint.h>

#define OW_ROM_SIZE                 8u
#define DS18B20_FAMILY              0x28u
#define DS18B20_MAX_CONVERT_POLLS   100000u   /* 等待转换完成时最多读多少个位 */

typedef enum {
    OW_OK = 0,
    OW_ERR_PARAM,          /* 参数错误 */
    OW_ERR_NO_DEVICE,      /* 复位后没有设备应答 */
    OW_ERR_BUS,            /* 搜索中途没有设备参与（读到 1、1）或设备中途消失 */
    OW_ERR_CRC,            /* ROM 号或暂存器的 CRC 不对 */
    OW_ERR_TIMEOUT,        /* 等待转换完成超时 */
    OW_ERR_NOT_DS18B20,    /* 家族码不是 28h */
    OW_MORE                /* 搜索结果数组已满，总线上还有更多设备 */
} ow_status_t;

typedef struct {
    uint8_t rom[OW_ROM_SIZE];   /* rom[0] 是家族码，rom[1..6] 是序列号，rom[7] 是 CRC */
} ow_rom_t;

/* CRC-8/MAXIM（多项式 X^8 + X^5 + X^4 + 1，初值 0，低位先算） */
uint8_t     ow_crc8(const uint8_t *data, size_t len);

/* 找出总线上的全部设备，按找到的顺序写进 roms[0..max)，*found 为找到的个数 */
ow_status_t ow_search_all(ow_rom_t *roms, size_t max, size_t *found);

/* 让总线上所有 DS18B20 同时开始转换，并等到转换完成 */
ow_status_t ds18b20_convert_all(void);

/* 读一个 DS18B20 的温度，单位 0.01 °C */
ow_status_t ds18b20_read_temp(const ow_rom_t *rom, int32_t *centi_celsius);

#endif /* ONEWIRE_DS18B20_H */
