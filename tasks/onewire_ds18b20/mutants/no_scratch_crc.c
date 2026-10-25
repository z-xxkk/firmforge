#include "onewire_ds18b20.h"

#include <stdbool.h>

#include "hal_onewire.h"

#define CMD_SEARCH_ROM     0xF0u
#define CMD_MATCH_ROM      0x55u
#define CMD_SKIP_ROM       0xCCu
#define CMD_CONVERT_T      0x44u
#define CMD_READ_SCRATCH   0xBEu

static void write_byte(uint8_t v)
{
    for (unsigned i = 0; i < 8u; i++) {           /* 低位先发 */
        hal_ow_write_bit((uint8_t)((v >> i) & 1u));
    }
}

static uint8_t read_byte(void)
{
    uint8_t v = 0;
    for (unsigned i = 0; i < 8u; i++) {
        if (hal_ow_read_bit() != 0u) {
            v = (uint8_t)(v | (1u << i));
        }
    }
    return v;
}

uint8_t ow_crc8(const uint8_t *data, size_t len)
{
    uint8_t crc = 0;
    if (data == NULL) {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        uint8_t in = data[i];
        for (unsigned b = 0; b < 8u; b++) {
            uint8_t mix = (uint8_t)((crc ^ in) & 1u);
            crc = (uint8_t)(crc >> 1);
            if (mix != 0u) {
                crc ^= 0x8Cu;
            }
            in = (uint8_t)(in >> 1);
        }
    }
    return crc;
}

/* AN187 的一轮搜索。last_disc 是上次最后一个"走 0 的分歧点"（0 表示第一轮），返回本轮的 */
static ow_status_t search_pass(uint8_t rom[OW_ROM_SIZE], unsigned *last_disc, bool first)
{
    unsigned last_zero = 0;
    if (!hal_ow_reset()) {
        return first ? OW_ERR_NO_DEVICE : OW_ERR_BUS;
    }
    write_byte(CMD_SEARCH_ROM);
    for (unsigned bit = 1; bit <= 64u; bit++) {
        unsigned byte_i = (bit - 1u) / 8u;
        uint8_t mask = (uint8_t)(1u << ((bit - 1u) % 8u));
        uint8_t id = hal_ow_read_bit();
        uint8_t cmp = hal_ow_read_bit();
        uint8_t dir;
        if (id != 0u && cmp != 0u) {
            return OW_ERR_BUS;                    /* 没有设备参与 */
        }
        if (id != cmp) {
            dir = id;                             /* 所有设备这一位都相同 */
        } else if (bit < *last_disc) {
            dir = (uint8_t)((rom[byte_i] & mask) != 0u);   /* 和上次找到的 ROM 一样 */
        } else {
            dir = (uint8_t)(bit == *last_disc);
        }
        if (id == cmp && dir == 0u) {
            last_zero = bit;
        }
        if (dir != 0u) {
            rom[byte_i] = (uint8_t)(rom[byte_i] | mask);
        } else {
            rom[byte_i] = (uint8_t)(rom[byte_i] & (uint8_t)~mask);
        }
        hal_ow_write_bit(dir);
    }
    if (ow_crc8(rom, OW_ROM_SIZE) != 0u) {
        return OW_ERR_CRC;
    }
    *last_disc = last_zero;
    return OW_OK;
}

ow_status_t ow_search_all(ow_rom_t *roms, size_t max, size_t *found)
{
    uint8_t rom[OW_ROM_SIZE] = {0};
    unsigned last_disc = 0;
    size_t n = 0;
    if (roms == NULL || found == NULL || max == 0u) {
        return OW_ERR_PARAM;
    }
    *found = 0;
    for (;;) {
        ow_status_t st = search_pass(rom, &last_disc, n == 0u);
        if (st != OW_OK) {
            return st;
        }
        if (n == max) {
            return OW_MORE;
        }
        for (unsigned i = 0; i < OW_ROM_SIZE; i++) {
            roms[n].rom[i] = rom[i];
        }
        n++;
        *found = n;
        if (last_disc == 0u) {
            return OW_OK;
        }
    }
}

ow_status_t ds18b20_convert_all(void)
{
    if (!hal_ow_reset()) {
        return OW_ERR_NO_DEVICE;
    }
    write_byte(CMD_SKIP_ROM);
    write_byte(CMD_CONVERT_T);
    for (uint32_t i = 0; i < DS18B20_MAX_CONVERT_POLLS; i++) {
        if (hal_ow_read_bit() != 0u) {
            return OW_OK;
        }
    }
    return OW_ERR_TIMEOUT;
}

ow_status_t ds18b20_read_temp(const ow_rom_t *rom, int32_t *centi_celsius)
{
    static const uint16_t masks[4] = {0xFFF8u, 0xFFFCu, 0xFFFEu, 0xFFFFu};   /* 9、10、11、12 位 */
    uint8_t sp[9];
    if (rom == NULL || centi_celsius == NULL) {
        return OW_ERR_PARAM;
    }
    if (rom->rom[0] != DS18B20_FAMILY) {
        return OW_ERR_NOT_DS18B20;
    }
    if (!hal_ow_reset()) {
        return OW_ERR_NO_DEVICE;
    }
    write_byte(CMD_MATCH_ROM);
    for (unsigned i = 0; i < OW_ROM_SIZE; i++) {
        write_byte(rom->rom[i]);
    }
    write_byte(CMD_READ_SCRATCH);
    for (unsigned i = 0; i < 9u; i++) {
        sp[i] = read_byte();
    }
    /* 变异：不校验暂存器 CRC */
    uint16_t bits = (uint16_t)(((uint16_t)sp[1] << 8) | sp[0]);
    bits = (uint16_t)(bits & masks[(sp[4] >> 5) & 3u]);
    int32_t raw = (bits & 0x8000u) ? (int32_t)bits - 65536 : (int32_t)bits;   /* 16 位二进制补码 */
    *centi_celsius = raw * 100 / 16;
    return OW_OK;
}
