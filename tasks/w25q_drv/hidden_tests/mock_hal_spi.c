#include "mock_hal_spi.h"

#include <string.h>

uint8_t mock_mem[MOCK_CAPACITY];
mock_spi_txn_t mock_log[MOCK_LOG_MAX];
unsigned mock_log_count;

static uint8_t id[3];
static bool wel;
static unsigned busy_left;
static unsigned busy_cycles;
static unsigned fail_n;
static unsigned call_n;

void mock_reset(unsigned busy_polls)
{
    memset(mock_mem, 0xFF, sizeof mock_mem);
    id[0] = 0xEF;
    id[1] = 0x40;
    id[2] = 0x17;
    wel = false;
    busy_left = 0;
    busy_cycles = busy_polls;
    fail_n = 0;
    call_n = 0;
    mock_log_count = 0;
}

void mock_set_id(uint8_t a, uint8_t b, uint8_t c)
{
    id[0] = a;
    id[1] = b;
    id[2] = c;
}

void mock_fail_on(unsigned n)
{
    fail_n = n;
}

unsigned mock_count_cmd(uint8_t cmd, bool include_ignored)
{
    unsigned n = 0;
    for (unsigned i = 0; i < mock_log_count; i++) {
        if (mock_log[i].cmd == cmd && (include_ignored || !mock_log[i].ignored)) {
            n++;
        }
    }
    return n;
}

static uint32_t addr24(const uint8_t *p)
{
    return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | (uint32_t)p[2];
}

hal_spi_status_t hal_spi_transfer(const uint8_t *tx, size_t tx_len, uint8_t *rx, size_t rx_len)
{
    mock_spi_txn_t t = {0};
    call_n++;
    if (tx == NULL || tx_len == 0 || (rx_len > 0 && rx == NULL)) {
        return HAL_SPI_ERROR;
    }
    t.cmd = tx[0];
    t.rx_len = rx_len;
    if (tx_len >= 4) {
        t.addr = addr24(&tx[1]);
    }
    if (fail_n != 0 && call_n == fail_n) {
        if (mock_log_count < MOCK_LOG_MAX) {
            mock_log[mock_log_count++] = t;
        }
        return HAL_SPI_ERROR;
    }
    if (rx_len > 0) {
        memset(rx, 0xFF, rx_len);
    }
    if (busy_left > 0 && t.cmd != 0x05) {
        t.ignored = true;                      /* 忙时忽略除读状态以外的指令 */
    } else {
        switch (t.cmd) {
        case 0x9F:
            for (size_t i = 0; i < rx_len && i < 3; i++) {
                rx[i] = id[i];
            }
            break;
        case 0x05:
            if (rx_len > 0) {
                rx[0] = (uint8_t)((busy_left > 0 ? 0x01 : 0x00) | (wel ? 0x02 : 0x00));
            }
            if (busy_left > 0) {
                busy_left--;
            }
            break;
        case 0x06:
            wel = true;
            break;
        case 0x04:
            wel = false;
            break;
        case 0x03:
            if (tx_len < 4) {
                return HAL_SPI_ERROR;
            }
            for (size_t i = 0; i < rx_len; i++) {
                rx[i] = mock_mem[(t.addr + i) % MOCK_CAPACITY];
            }
            break;
        case 0x02:
            t.data_len = tx_len >= 4 ? tx_len - 4 : 0;
            if (!wel || tx_len < 5) {
                t.ignored = true;
                break;
            }
            {
                uint32_t base = t.addr & ~0xFFu;
                for (size_t i = 0; i < t.data_len; i++) {
                    uint32_t a = base + (uint32_t)((t.addr + i) & 0xFFu);   /* 跨页时回绕到本页开头 */
                    mock_mem[a % MOCK_CAPACITY] &= tx[4 + i];                /* 只能把 1 变成 0 */
                }
            }
            wel = false;
            busy_left = busy_cycles;
            break;
        case 0x20:
            if (!wel || tx_len < 4) {
                t.ignored = true;
                break;
            }
            memset(&mock_mem[(t.addr & ~0xFFFu) % MOCK_CAPACITY], 0xFF, 4096);
            wel = false;
            busy_left = busy_cycles;
            break;
        default:
            break;
        }
    }
    if (mock_log_count < MOCK_LOG_MAX) {
        mock_log[mock_log_count++] = t;
    }
    return HAL_SPI_OK;
}
