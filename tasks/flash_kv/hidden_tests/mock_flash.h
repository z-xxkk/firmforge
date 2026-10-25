/* 假 NOR Flash：只能把 1 变成 0，可以设定"做到第 N 个操作单位时断电"。只给隐藏测试用。 */
#ifndef MOCK_FLASH_H
#define MOCK_FLASH_H

#include <stdbool.h>
#include <stdint.h>

#define MOCK_FLASH_SIZE 2048u

extern uint8_t mock_flash[MOCK_FLASH_SIZE];
extern unsigned mock_erases;        /* 擦除次数 */
extern unsigned mock_write_calls;   /* 写调用次数 */
extern unsigned mock_violations;    /* 想把 0 写回 1 的次数（违反 Flash 特性） */
extern unsigned mock_units;         /* 已完成的操作单位：每写一个字节算 1，每擦除一次算 1 */
extern bool mock_read_fail;

void mock_flash_blank(void);
void mock_cut_after(unsigned units);  /* 再做这么多个单位后断电：之后的写和擦除都不生效并返回错误 */
void mock_power_on(void);             /* 恢复供电 */

#endif /* MOCK_FLASH_H */
