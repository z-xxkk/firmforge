#ifndef MODBUS_FRAME_H
#define MODBUS_FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* 检查整帧的 CRC（最后两字节，低字节在前） */
bool   modbus_frame_crc_ok(const uint8_t *frame, size_t len);
/* 在 frame[0..len) 之后追加 CRC，返回追加后的总长度 */
size_t modbus_frame_append_crc(uint8_t *frame, size_t len);
/* 组装异常响应，返回长度；resp_size 不够时返回 0 */
size_t modbus_frame_exception(uint8_t address, uint8_t function, uint8_t code, uint8_t *resp, size_t resp_size);

#endif /* MODBUS_FRAME_H */
