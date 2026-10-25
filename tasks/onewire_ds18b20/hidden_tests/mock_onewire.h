/* 假 1-Wire 总线：可以挂多个假设备，按真实总线做"线与"。只给隐藏测试用。 */
#ifndef MOCK_ONEWIRE_H
#define MOCK_ONEWIRE_H

#include <stdbool.h>
#include <stdint.h>

#define MOCK_MAX_DEVICES 16

void     mock_reset_bus(void);
int      mock_add_device(const uint8_t rom[8]);           /* 返回设备编号 */
void     mock_set_temp(int dev, uint16_t raw);              /* 转换完成后温度寄存器的值 */
void     mock_set_config(int dev, uint8_t config);          /* 暂存器第 5 字节（分辨率） */
void     mock_set_scratch_crc_error(int dev, bool on);
void     mock_set_convert_polls(unsigned polls);            /* 转换要读多少个 0 才完成；0xFFFFFFFF = 永不完成 */
void     mock_remove_all_at_search_bit(unsigned bit);       /* 搜索进行到第 bit 位时所有设备消失（0 = 不启用） */
void     mock_make_rom(uint8_t family, uint32_t serial_lo, uint16_t serial_hi, uint8_t rom[8]);

extern unsigned mock_resets;            /* 复位次数 */
extern unsigned mock_bus_ops;           /* 读写位的总次数 */
extern unsigned mock_protocol_errors;   /* 主机做了不符合协议的操作（如该写时读） */
extern uint8_t  mock_last_function;     /* 最近一条功能命令 */
extern uint8_t  mock_last_rom_cmd;      /* 最近一条 ROM 命令 */
extern unsigned mock_searches;          /* 完成的搜索轮数 */

#endif /* MOCK_ONEWIRE_H */
