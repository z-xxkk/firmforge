#ifndef CRC16_MODBUS_H
#define CRC16_MODBUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

uint16_t crc16_modbus(const uint8_t *data, size_t len);
void     crc16_modbus_append(uint8_t *frame, size_t len);
bool     crc16_modbus_check(const uint8_t *frame, size_t total_len);

#endif /* CRC16_MODBUS_H */
