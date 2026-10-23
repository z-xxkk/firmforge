#include "crc16_modbus.h"

uint16_t crc16_modbus(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    if (data == NULL) {
        return crc;
    }
    for (size_t i = 0u; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if ((crc & 1u) != 0u) {
                crc = (uint16_t)((crc >> 1) ^ 0xA001u);
            } else {
                crc = (uint16_t)(crc >> 1);
            }
        }
    }
    return crc;
}

void crc16_modbus_append(uint8_t *frame, size_t len)
{
    uint16_t crc;
    if (frame == NULL) {
        return;
    }
    crc = crc16_modbus(frame, len);
    frame[len] = (uint8_t)(crc >> 8);          /* 变异：字节序反了 */
    frame[len + 1u] = (uint8_t)(crc & 0xFFu);
}

bool crc16_modbus_check(const uint8_t *frame, size_t total_len)
{
    uint16_t crc;
    if (frame == NULL || total_len < 3u) {
        return false;
    }
    crc = crc16_modbus(frame, total_len - 2u);
    return frame[total_len - 2u] == (uint8_t)(crc & 0xFFu) &&
           frame[total_len - 1u] == (uint8_t)(crc >> 8);
}
