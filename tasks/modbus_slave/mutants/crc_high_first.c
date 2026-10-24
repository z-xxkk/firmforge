#include "modbus_frame.h"

#include "modbus_crc.h"

bool modbus_frame_crc_ok(const uint8_t *frame, size_t len)
{
    uint16_t crc;
    if (len < 2u) {
        return false;
    }
    crc = modbus_crc16(frame, len - 2u);
    return frame[len - 2u] == (uint8_t)(crc & 0xFFu) && frame[len - 1u] == (uint8_t)(crc >> 8);
}

size_t modbus_frame_append_crc(uint8_t *frame, size_t len)
{
    uint16_t crc = modbus_crc16(frame, len);
    frame[len] = (uint8_t)(crc >> 8);          /* 变异：高字节在前 */
    frame[len + 1u] = (uint8_t)(crc & 0xFFu);
    return len + 2u;
}

size_t modbus_frame_exception(uint8_t address, uint8_t function, uint8_t code, uint8_t *resp, size_t resp_size)
{
    if (resp_size < 5u) {
        return 0u;
    }
    resp[0] = address;
    resp[1] = (uint8_t)(function | 0x80u);
    resp[2] = code;
    return modbus_frame_append_crc(resp, 3u);
}
