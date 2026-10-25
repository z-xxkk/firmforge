#include "mock_onewire.h"

#include <string.h>

#include "hal_onewire.h"

typedef struct {
    uint8_t rom[8];
    uint8_t sp[9];
    uint16_t temp;
    bool active;          /* 还在参与本次事务 */
    bool crc_error;
} dev_t_;

enum { PH_IDLE, PH_ROM_CMD, PH_SEARCH, PH_MATCH, PH_FUNC, PH_READ_SP, PH_CONVERT };

static dev_t_ devs[MOCK_MAX_DEVICES];
static int ndev;
static int phase;
static unsigned bits;          /* 当前字节 / ROM 已收到的位数 */
static uint8_t acc;
static uint8_t match_rom[8];
static unsigned sbit;          /* 搜索：第几位（0..63） */
static int sstep;              /* 搜索：0 读原码，1 读反码，2 写方向 */
static unsigned convert_polls, convert_left;
static unsigned remove_at;
static unsigned sp_bit;

unsigned mock_resets, mock_bus_ops, mock_protocol_errors, mock_searches;
uint8_t mock_last_function, mock_last_rom_cmd;

static uint8_t crc8(const uint8_t *d, int n)
{
    uint8_t crc = 0;
    for (int i = 0; i < n; i++) {
        for (int b = 0; b < 8; b++) {
            uint8_t mix = (uint8_t)((crc ^ (d[i] >> b)) & 1u);
            crc = (uint8_t)(crc >> 1);
            if (mix) {
                crc ^= 0x8C;
            }
        }
    }
    return crc;
}

void mock_make_rom(uint8_t family, uint32_t serial_lo, uint16_t serial_hi, uint8_t rom[8])
{
    rom[0] = family;
    for (int i = 0; i < 4; i++) {
        rom[1 + i] = (uint8_t)(serial_lo >> (8 * i));
    }
    rom[5] = (uint8_t)serial_hi;
    rom[6] = (uint8_t)(serial_hi >> 8);
    rom[7] = crc8(rom, 7);
}

static void refresh_sp(dev_t_ *d)
{
    d->sp[8] = (uint8_t)(crc8(d->sp, 8) ^ (d->crc_error ? 0x01u : 0x00u));
}

void mock_reset_bus(void)
{
    memset(devs, 0, sizeof devs);
    ndev = 0;
    phase = PH_IDLE;
    convert_polls = 3;
    remove_at = 0;
    mock_resets = mock_bus_ops = mock_protocol_errors = mock_searches = 0;
    mock_last_function = mock_last_rom_cmd = 0;
}

int mock_add_device(const uint8_t rom[8])
{
    dev_t_ *d = &devs[ndev];
    memcpy(d->rom, rom, 8);
    d->sp[0] = 0x50;              /* 上电值 +85 °C（0550h） */
    d->sp[1] = 0x05;
    d->sp[2] = 0x4B;
    d->sp[3] = 0x46;
    d->sp[4] = 0x7F;              /* 12 位 */
    d->sp[5] = 0xFF;
    d->sp[6] = 0x0C;
    d->sp[7] = 0x10;
    d->temp = 0x0550;
    refresh_sp(d);
    return ndev++;
}

void mock_set_temp(int dev, uint16_t raw) { devs[dev].temp = raw; }

void mock_set_config(int dev, uint8_t config)
{
    devs[dev].sp[4] = config;
    refresh_sp(&devs[dev]);
}

void mock_set_scratch_crc_error(int dev, bool on)
{
    devs[dev].crc_error = on;
    refresh_sp(&devs[dev]);
}

void mock_set_convert_polls(unsigned polls) { convert_polls = polls; }
void mock_remove_all_at_search_bit(unsigned bit) { remove_at = bit; }

static int rom_bit(const dev_t_ *d, unsigned i) { return (d->rom[i / 8] >> (i % 8)) & 1; }

bool hal_ow_reset(void)
{
    mock_resets++;
    for (int i = 0; i < ndev; i++) {
        devs[i].active = true;
    }
    phase = PH_ROM_CMD;
    bits = 0;
    acc = 0;
    return ndev > 0;
}

static void on_byte(uint8_t b)
{
    if (phase == PH_ROM_CMD) {
        mock_last_rom_cmd = b;
        if (b == 0xF0) {
            phase = PH_SEARCH;
            sbit = 0;
            sstep = 0;
        } else if (b == 0x55) {
            phase = PH_MATCH;
        } else if (b == 0xCC) {
            phase = PH_FUNC;
        } else {
            mock_protocol_errors++;
            phase = PH_IDLE;
        }
    } else if (phase == PH_FUNC) {
        mock_last_function = b;
        if (b == 0x44) {
            phase = PH_CONVERT;
            convert_left = convert_polls;
        } else if (b == 0xBE) {
            phase = PH_READ_SP;
            sp_bit = 0;
        } else {
            mock_protocol_errors++;
            phase = PH_IDLE;
        }
    }
    bits = 0;
    acc = 0;
}

void hal_ow_write_bit(uint8_t bit)
{
    mock_bus_ops++;
    if (bit > 1u) {
        mock_protocol_errors++;
    }
    switch (phase) {
    case PH_ROM_CMD:
    case PH_FUNC:
        acc = (uint8_t)(acc | ((bit & 1u) << bits));
        if (++bits == 8) {
            on_byte(acc);
        }
        break;
    case PH_MATCH:
        match_rom[bits / 8] = (uint8_t)((match_rom[bits / 8] & ~(1u << (bits % 8))) | ((bit & 1u) << (bits % 8)));
        if (++bits == 64) {
            for (int i = 0; i < ndev; i++) {
                devs[i].active = memcmp(devs[i].rom, match_rom, 8) == 0;
            }
            phase = PH_FUNC;
            bits = 0;
            acc = 0;
        }
        break;
    case PH_SEARCH:
        if (sstep != 2) {
            mock_protocol_errors++;
            break;
        }
        for (int i = 0; i < ndev; i++) {
            if (devs[i].active && rom_bit(&devs[i], sbit) != (int)(bit & 1u)) {
                devs[i].active = false;      /* 不匹配的设备退出，等下次复位 */
            }
        }
        sstep = 0;
        if (++sbit == 64) {
            mock_searches++;
            phase = PH_IDLE;
        }
        break;
    default:
        mock_protocol_errors++;
        break;
    }
}

uint8_t hal_ow_read_bit(void)
{
    mock_bus_ops++;
    switch (phase) {
    case PH_SEARCH: {
        if (sstep == 2) {
            mock_protocol_errors++;
            return 1;
        }
        if (remove_at != 0 && sbit + 1u >= remove_at) {
            for (int i = 0; i < ndev; i++) {
                devs[i].active = false;
            }
        }
        uint8_t v = 1;
        for (int i = 0; i < ndev; i++) {
            if (devs[i].active) {
                int b = rom_bit(&devs[i], sbit);
                v &= (uint8_t)(sstep == 0 ? b : !b);
            }
        }
        sstep++;
        return v;
    }
    case PH_CONVERT:
        if (convert_left == 0xFFFFFFFFu) {
            return 0;
        }
        if (convert_left > 0) {
            convert_left--;
            return 0;
        }
        for (int i = 0; i < ndev; i++) {
            if (devs[i].active) {
                devs[i].sp[0] = (uint8_t)devs[i].temp;
                devs[i].sp[1] = (uint8_t)(devs[i].temp >> 8);
                refresh_sp(&devs[i]);
            }
        }
        return 1;
    case PH_READ_SP: {
        uint8_t v = 1;
        if (sp_bit >= 72) {
            return 1;
        }
        for (int i = 0; i < ndev; i++) {
            if (devs[i].active) {
                v &= (uint8_t)((devs[i].sp[sp_bit / 8] >> (sp_bit % 8)) & 1u);
            }
        }
        sp_bit++;
        return v;
    }
    case PH_ROM_CMD:
    case PH_FUNC:
    case PH_MATCH:
        mock_protocol_errors++;
        return 1;
    default:
        return 1;                    /* 空闲时总线被上拉 */
    }
}
