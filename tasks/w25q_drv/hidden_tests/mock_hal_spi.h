/* 隐藏测试用的假 SPI Flash：按 W25Q64JV 手册的行为模拟，并记录每一次 SPI 调用 */
#ifndef MOCK_HAL_SPI_H
#define MOCK_HAL_SPI_H

#include <stdbool.h>
#include <stdint.h>

#include "hal_spi.h"

#define MOCK_CAPACITY (8u * 1024u * 1024u)
#define MOCK_LOG_MAX  512u

typedef struct {
    uint8_t cmd;
    uint32_t addr;        /* 03h / 02h / 20h 的地址（按高字节在前解析） */
    size_t data_len;      /* 02h 的数据长度 */
    size_t rx_len;
    bool ignored;         /* 芯片忙或未写使能而被忽略 */
} mock_spi_txn_t;

extern uint8_t mock_mem[MOCK_CAPACITY];
extern mock_spi_txn_t mock_log[MOCK_LOG_MAX];
extern unsigned mock_log_count;

/* 复位：存储全为 FFh，身份 EF 40 17，不忙、未写使能；编程 / 擦除后忙 busy_polls 次状态读取 */
void mock_reset(unsigned busy_polls);
void mock_set_id(uint8_t a, uint8_t b, uint8_t c);
void mock_fail_on(unsigned n);          /* 让第 n 次（从 1 数起）SPI 调用失败；0 表示不失败 */
unsigned mock_count_cmd(uint8_t cmd, bool include_ignored);

#endif /* MOCK_HAL_SPI_H */
