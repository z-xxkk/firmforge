#include "modbus_slave.h"

#include "modbus_frame.h"

#define FC_READ_HOLDING   0x03u
#define FC_WRITE_SINGLE   0x06u
#define REQ_LEN_FC03_FC06 8u
#define MAX_READ_QTY      125u

static uint16_t be16(const uint8_t *p)
{
    return (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

bool modbus_slave_init(modbus_slave_t *s, uint8_t address, uint16_t *regs, uint16_t reg_count)
{
    if (s == NULL) {
        return false;
    }
    s->address = 0u;
    s->regs = NULL;
    s->reg_count = 0u;
    if (address < 1u || address > 247u || regs == NULL || reg_count == 0u) {
        return false;
    }
    s->address = address;
    s->regs = regs;
    s->reg_count = reg_count;
    return true;
}

/* 处理 03：返回响应长度；*ex 非 0 表示要回异常 */
static size_t read_holding(const modbus_slave_t *s, const uint8_t *req, size_t req_len,
                           uint8_t *resp, size_t resp_size, uint8_t *ex)
{
    uint16_t start;
    uint16_t qty;
    size_t need;

    if (req_len != REQ_LEN_FC03_FC06) {
        *ex = MODBUS_EX_ILLEGAL_DATA_VALUE;
        return 0u;
    }
    start = be16(&req[2]);
    qty = be16(&req[4]);
    if (qty < 1u || qty > MAX_READ_QTY) {
        *ex = MODBUS_EX_ILLEGAL_DATA_VALUE;
        return 0u;
    }
    if ((uint32_t)start + qty > s->reg_count) {
        *ex = MODBUS_EX_ILLEGAL_DATA_ADDRESS;
        return 0u;
    }
    need = 5u + 2u * (size_t)qty;
    if (resp_size < need) {
        *ex = MODBUS_EX_SERVER_DEVICE_FAILURE;
        return 0u;
    }
    resp[0] = req[0];
    resp[1] = FC_READ_HOLDING;
    resp[2] = (uint8_t)(2u * qty);
    for (uint16_t i = 0u; i < qty; i++) {
        uint16_t v = s->regs[start + i];
        resp[3u + 2u * i] = (uint8_t)(v >> 8);
        resp[4u + 2u * i] = (uint8_t)(v & 0xFFu);
    }
    return modbus_frame_append_crc(resp, 3u + 2u * (size_t)qty);
}

/* 处理 06 */
static size_t write_single(modbus_slave_t *s, const uint8_t *req, size_t req_len,
                           uint8_t *resp, size_t resp_size, uint8_t *ex)
{
    uint16_t addr;

    if (req_len != REQ_LEN_FC03_FC06) {
        *ex = MODBUS_EX_ILLEGAL_DATA_VALUE;
        return 0u;
    }
    addr = be16(&req[2]);
    if (addr >= s->reg_count) {
        *ex = MODBUS_EX_ILLEGAL_DATA_ADDRESS;
        return 0u;
    }
    if (resp != NULL && resp_size < REQ_LEN_FC03_FC06) {
        *ex = MODBUS_EX_SERVER_DEVICE_FAILURE;
        return 0u;
    }
    s->regs[addr] = be16(&req[4]);
    if (resp == NULL) {
        return 0u;
    }
    for (size_t i = 0u; i < REQ_LEN_FC03_FC06; i++) {
        resp[i] = req[i];
    }
    return REQ_LEN_FC03_FC06;
}

size_t modbus_slave_handle(modbus_slave_t *s, const uint8_t *req, size_t req_len,
                           uint8_t *resp, size_t resp_size)
{
    uint8_t address;
    uint8_t function;
    uint8_t ex = 0u;
    size_t n;

    if (s == NULL || s->regs == NULL || req == NULL || resp == NULL) {
        return 0u;
    }
    if (req_len < 4u || req_len > MODBUS_MAX_FRAME || !modbus_frame_crc_ok(req, req_len)) {
        return 0u;
    }
    address = req[0];
    function = req[1];
    if (address != s->address && address != MODBUS_BROADCAST_ADDRESS) {
        return 0u;
    }
    /* 变异：广播也按普通请求处理并回复 */
    if (function == FC_READ_HOLDING) {
        n = read_holding(s, req, req_len, resp, resp_size, &ex);
    } else if (function == FC_WRITE_SINGLE) {
        n = write_single(s, req, req_len, resp, resp_size, &ex);
    } else {
        n = 0u;
        ex = MODBUS_EX_ILLEGAL_FUNCTION;
    }
    if (ex != 0u) {
        return modbus_frame_exception(address, function, ex, resp, resp_size);
    }
    return n;
}
