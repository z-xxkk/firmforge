#include "w25q.h"

#include "hal_spi.h"

#define CMD_WRITE_ENABLE   0x06u
#define CMD_READ_STATUS1   0x05u
#define CMD_READ_DATA      0x03u
#define CMD_PAGE_PROGRAM   0x02u
#define CMD_SECTOR_ERASE   0x20u
#define CMD_JEDEC_ID       0x9Fu
#define STATUS_BUSY        0x01u

static void put_addr(uint8_t *p, uint32_t addr)
{
    p[0] = (uint8_t)(addr >> 16);
    p[1] = (uint8_t)(addr >> 8);
    p[2] = (uint8_t)addr;
}

static bool range_ok(uint32_t addr, size_t len)
{
    return addr < W25Q_CAPACITY && len <= (size_t)(W25Q_CAPACITY - addr);
}

static w25q_status_t write_enable(void)
{
    const uint8_t cmd = CMD_WRITE_ENABLE;
    return hal_spi_transfer(&cmd, 1u, NULL, 0u) == HAL_SPI_OK ? W25Q_OK : W25Q_ERR_BUS;
}

static w25q_status_t wait_ready(void)
{
    const uint8_t cmd = CMD_READ_STATUS1;
    for (uint32_t i = 0u; i < W25Q_MAX_BUSY_POLLS; i++) {
        uint8_t status = 0xFFu;
        if (hal_spi_transfer(&cmd, 1u, &status, 1u) != HAL_SPI_OK) {
            return W25Q_ERR_BUS;
        }
        if ((status & STATUS_BUSY) == 0u) {
            return W25Q_OK;
        }
    }
    return W25Q_ERR_TIMEOUT;
}

w25q_status_t w25q_init(w25q_t *dev)
{
    const uint8_t cmd = CMD_JEDEC_ID;
    uint8_t id[3] = {0u, 0u, 0u};

    if (dev == NULL) {
        return W25Q_ERR_PARAM;
    }
    dev->ready = false;
    if (hal_spi_transfer(&cmd, 1u, id, 3u) != HAL_SPI_OK) {
        return W25Q_ERR_BUS;
    }
    if (id[0] != 0xEFu || (id[1] != 0x40u && id[1] != 0x70u) || id[2] != 0x17u) {
        return W25Q_ERR_ID;
    }
    dev->ready = true;
    return W25Q_OK;
}

w25q_status_t w25q_read(const w25q_t *dev, uint32_t addr, uint8_t *buf, size_t len)
{
    uint8_t cmd[4];

    if (dev == NULL || buf == NULL || !dev->ready || !range_ok(addr, len)) {
        return W25Q_ERR_PARAM;
    }
    if (len == 0u) {
        return W25Q_OK;
    }
    cmd[0] = CMD_READ_DATA;
    put_addr(&cmd[1], addr);
    return hal_spi_transfer(cmd, sizeof cmd, buf, len) == HAL_SPI_OK ? W25Q_OK : W25Q_ERR_BUS;
}

w25q_status_t w25q_write(const w25q_t *dev, uint32_t addr, const uint8_t *data, size_t len)
{
    uint8_t frame[4u + W25Q_PAGE_SIZE];

    if (dev == NULL || data == NULL || !dev->ready || !range_ok(addr, len)) {
        return W25Q_ERR_PARAM;
    }
    bool first = true;
    while (len > 0u) {
        size_t room = W25Q_PAGE_SIZE - (addr % W25Q_PAGE_SIZE);
        size_t chunk = len < room ? len : room;
        w25q_status_t st;

        if (first) {   /* 变异：只在开头发一次写使能 */
            st = write_enable();
            if (st != W25Q_OK) {
                return st;
            }
            first = false;
        }
        frame[0] = CMD_PAGE_PROGRAM;
        put_addr(&frame[1], addr);
        for (size_t i = 0u; i < chunk; i++) {
            frame[4u + i] = data[i];
        }
        if (hal_spi_transfer(frame, 4u + chunk, NULL, 0u) != HAL_SPI_OK) {
            return W25Q_ERR_BUS;
        }
        st = wait_ready();
        if (st != W25Q_OK) {
            return st;
        }
        addr += (uint32_t)chunk;
        data += chunk;
        len -= chunk;
    }
    return W25Q_OK;
}

w25q_status_t w25q_erase_sector(const w25q_t *dev, uint32_t addr)
{
    uint8_t cmd[4];
    w25q_status_t st;

    if (dev == NULL || !dev->ready || addr >= W25Q_CAPACITY || (addr % W25Q_SECTOR_SIZE) != 0u) {
        return W25Q_ERR_PARAM;
    }
    st = write_enable();
    if (st != W25Q_OK) {
        return st;
    }
    cmd[0] = CMD_SECTOR_ERASE;
    put_addr(&cmd[1], addr);
    if (hal_spi_transfer(cmd, sizeof cmd, NULL, 0u) != HAL_SPI_OK) {
        return W25Q_ERR_BUS;
    }
    return wait_ready();
}
