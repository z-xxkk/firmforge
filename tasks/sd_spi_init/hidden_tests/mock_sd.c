#include "mock_sd.h"

#include <string.h>

#include "hal_sd_spi.h"

#define BLOCKS 64

mock_cmd_t mock_log[MOCK_LOG_MAX];
unsigned mock_log_count, mock_clocks_before_first_select, mock_bytes_while_deselected, mock_xfers, mock_selects;
unsigned mock_cmd_while_busy;
bool mock_selected;

static card_kind_t kind;
static unsigned resp_delay, busy_acmd41, cmd0_silent, token_delay;
static uint8_t error_token;
static bool ever_selected, idle, ready, app_cmd;
static uint8_t cmd[6];
static unsigned cmd_len;
static uint8_t q[3000];
static unsigned q_len, q_pos;
static uint8_t mem[BLOCKS][512];

static uint8_t crc7(const uint8_t *d, int n)
{
    uint8_t c = 0;
    for (int i = 0; i < n; i++) {
        for (int b = 7; b >= 0; b--) {
            int top = (c >> 6) & 1, in = (d[i] >> b) & 1;
            c = (uint8_t)((c << 1) & 0x7F);
            if (top ^ in) {
                c ^= 0x09;
            }
        }
    }
    return c;
}

void mock_card(card_kind_t k)
{
    kind = k;
    resp_delay = 1;
    busy_acmd41 = 3;
    cmd0_silent = 0;
    token_delay = 5;
    error_token = 0;
    ever_selected = idle = ready = app_cmd = false;
    cmd_len = 0;
    q_len = q_pos = 0;
    mock_log_count = mock_clocks_before_first_select = mock_bytes_while_deselected = mock_xfers = mock_selects = 0;
    mock_cmd_while_busy = 0;
    mock_selected = false;
    for (int b = 0; b < BLOCKS; b++) {
        for (int i = 0; i < 512; i++) {
            mem[b][i] = (uint8_t)(b * 37 + i * 3 + 1);
        }
    }
}

void mock_set_resp_delay(unsigned bytes) { resp_delay = bytes; }
void mock_set_busy_acmd41(unsigned times) { busy_acmd41 = times; }
void mock_set_cmd0_silent(unsigned times) { cmd0_silent = times; }
void mock_set_token_delay(unsigned bytes) { token_delay = bytes; }
void mock_set_error_token(uint8_t token) { error_token = token; }
uint8_t *mock_block(uint32_t block) { return mem[block % BLOCKS]; }

static void push(uint8_t b)
{
    if (q_len < sizeof q) {
        q[q_len++] = b;
    }
}

static void reply(uint8_t r1)
{
    q_len = q_pos = 0;
    for (unsigned i = 0; i < resp_delay; i++) {
        push(0xFF);
    }
    push(r1);
}

static uint8_t r1_now(void) { return idle ? 0x01 : 0x00; }

static void execute(void)
{
    uint8_t index = cmd[0] & 0x3F;
    uint32_t arg = ((uint32_t)cmd[1] << 24) | ((uint32_t)cmd[2] << 16) | ((uint32_t)cmd[3] << 8) | cmd[4];
    bool crc_ok = cmd[5] == (uint8_t)((crc7(cmd, 5) << 1) | 1);
    bool was_app = app_cmd;
    app_cmd = false;
    if (mock_log_count < MOCK_LOG_MAX) {
        mock_log[mock_log_count++] = (mock_cmd_t){index, arg, crc_ok};
    }
    if (kind == CARD_NONE) {
        return;
    }
    if (index == 0) {
        if (cmd0_silent > 0) {
            cmd0_silent--;
            return;
        }
        if (!crc_ok) {
            reply(0x09);                          /* CMD0 必须带对的 CRC */
            return;
        }
        idle = true;
        ready = false;
        reply(0x01);
        return;
    }
    if (!idle && !ready) {
        return;                                   /* 还没复位：不理会 */
    }
    switch (index) {
    case 8:
        if (kind == CARD_V1 || kind == CARD_MMC) {
            reply(0x05);
            return;
        }
        if (!crc_ok) {
            reply(0x09);
            return;
        }
        reply(r1_now());
        push(0x00);
        push(0x00);
        push(kind == CARD_BAD_VOLTAGE ? 0x00 : (uint8_t)((arg >> 8) & 0x0F));
        push((uint8_t)arg);
        return;
    case 55:
        if (kind == CARD_MMC) {
            reply(0x05);
            return;
        }
        app_cmd = true;
        reply(r1_now());
        return;
    case 41:
        if (!was_app) {
            reply((uint8_t)(r1_now() | 0x04));
            return;
        }
        if (kind == CARD_V2_HC && !(arg & 0x40000000u)) {
            reply(0x01);                          /* 大容量卡：主机不带 HCS 就一直不就绪 */
            return;
        }
        if (busy_acmd41 > 0) {
            busy_acmd41--;
            reply(0x01);
            return;
        }
        idle = false;
        ready = true;
        reply(0x00);
        return;
    case 58:
        reply(r1_now());
        push(kind == CARD_V2_HC ? 0xC0 : 0x80);  /* 上电完成位 + CCS */
        push(0xFF);
        push(0x80);
        push(0x00);
        return;
    case 16:
        reply(ready && arg == 512 ? 0x00 : (uint8_t)(r1_now() | 0x40));
        return;
    case 17: {
        uint32_t block;
        if (!ready) {
            reply(0x01);
            return;
        }
        if (kind == CARD_V2_HC) {
            block = arg;
        } else {
            if (arg % 512u != 0u) {
                reply(0x20);                      /* 地址错 */
                return;
            }
            block = arg / 512u;
        }
        if (block >= BLOCKS) {
            reply(0x40);
            return;
        }
        reply(0x00);
        for (unsigned i = 0; i < token_delay; i++) {
            push(0xFF);
        }
        if (error_token) {
            push(error_token);
            return;
        }
        push(0xFE);
        for (int i = 0; i < 512; i++) {
            push(mem[block][i]);
        }
        push(0x12);
        push(0x34);
        return;
    }
    default:
        reply((uint8_t)(r1_now() | 0x04));
        return;
    }
}

void hal_sd_select(bool selected)
{
    if (selected && !mock_selected) {
        ever_selected = true;
        mock_selects++;
    }
    if (!selected) {
        q_len = q_pos = 0;                         /* 释放片选：没读完的回应丢掉 */
        cmd_len = 0;
    }
    mock_selected = selected;
}

uint8_t hal_sd_xfer(uint8_t out)
{
    mock_xfers++;
    if (!mock_selected) {
        if (!ever_selected) {
            mock_clocks_before_first_select++;
        }
        mock_bytes_while_deselected++;
        return 0xFF;
    }
    if (q_pos < q_len) {
        if (out != 0xFF) {
            mock_cmd_while_busy++;
        }
        return q[q_pos++];
    }
    if (cmd_len == 0 && (out & 0xC0) != 0x40) {
        return 0xFF;                               /* 命令之间的 FFh */
    }
    cmd[cmd_len++] = out;
    if (cmd_len == 6) {
        cmd_len = 0;
        execute();
    }
    return 0xFF;
}
