#include "sd_card.h"

#include "hal_sd_spi.h"

#define R1_IDLE     0x01u
#define R1_ILLEGAL  0x04u
#define NO_REPLY    0xFFu
#define TOKEN_START 0xFEu
#define HCS         0x40000000u

uint8_t sd_crc7(const uint8_t *data, size_t len)
{
    uint8_t crc = 0;
    if (data == NULL) {
        return 0;
    }
    for (size_t i = 0; i < len; i++) {
        for (int b = 7; b >= 0; b--) {
            uint8_t in = (uint8_t)((data[i] >> b) & 1u);
            uint8_t top = (uint8_t)((crc >> 6) & 1u);
            crc = (uint8_t)((crc << 1) & 0x7Fu);
            if ((top ^ in) != 0u) {
                crc ^= 0x09u;
            }
        }
    }
    return crc;
}

/* 发一条命令并取回 R1；extra 不为 NULL 时再读 4 字节（R7、R3）。keep 为 true 时不释放片选（读块用） */
static uint8_t command(uint8_t index, uint32_t arg, uint8_t extra[4], bool keep)
{
    uint8_t frame[6];
    uint8_t r1 = NO_REPLY;
    frame[0] = (uint8_t)(0x40u | index);
    frame[1] = (uint8_t)(arg >> 24);
    frame[2] = (uint8_t)(arg >> 16);
    frame[3] = (uint8_t)(arg >> 8);
    frame[4] = (uint8_t)arg;
    frame[5] = (uint8_t)((sd_crc7(frame, 5) << 1) | 1u);
    hal_sd_select(true);
    (void)hal_sd_xfer(0xFFu);
    for (unsigned i = 0; i < 6u; i++) {
        (void)hal_sd_xfer(frame[i]);
    }
    for (unsigned i = 0; i < 1u; i++) {   /* 变异：只等 1 个字节 */
        uint8_t r = hal_sd_xfer(0xFFu);
        if ((r & 0x80u) == 0u) {
            r1 = r;
            break;
        }
    }
    if (r1 != NO_REPLY && extra != NULL) {
        for (unsigned i = 0; i < 4u; i++) {
            extra[i] = hal_sd_xfer(0xFFu);
        }
    }
    if (!keep || r1 == NO_REPLY) {
        hal_sd_select(false);
        (void)hal_sd_xfer(0xFFu);
    }
    return r1;
}

static sd_status_t fail(sd_card_t *card, sd_status_t st)
{
    card->ready = false;
    card->type = (uint8_t)SD_TYPE_NONE;
    return st;
}

sd_status_t sd_init(sd_card_t *card)
{
    uint8_t r7[4];
    uint8_t r1 = NO_REPLY;
    bool v2;
    if (card == NULL) {
        return SD_ERR_PARAM;
    }
    card->ready = false;
    card->type = (uint8_t)SD_TYPE_NONE;
    hal_sd_select(false);
    for (unsigned i = 0; i < SD_INIT_CLOCK_BYTES; i++) {
        (void)hal_sd_xfer(0xFFu);
    }
    for (unsigned i = 0; i < SD_CMD0_TRIES && r1 != R1_IDLE; i++) {
        r1 = command(0, 0, NULL, false);
    }
    if (r1 != R1_IDLE) {
        return fail(card, SD_ERR_NO_CARD);
    }
    r1 = command(8, 0x000001AAu, r7, false);
    if (r1 == NO_REPLY) {
        return fail(card, SD_ERR_NO_RESPONSE);
    }
    if ((r1 & R1_ILLEGAL) != 0u) {
        v2 = false;                                   /* 1.x 版卡不认识 CMD8 */
    } else if (r1 == R1_IDLE) {
        if ((r7[2] & 0x0Fu) != 0x01u || r7[3] != 0xAAu) {
            return fail(card, SD_ERR_VOLTAGE);
        }
        v2 = true;
    } else {
        return fail(card, SD_ERR_CMD);
    }
    r1 = R1_IDLE;
    for (unsigned i = 0; i < SD_ACMD41_TRIES && r1 != 0u; i++) {
        r1 = command(55, 0, NULL, false);
        if (r1 == NO_REPLY) {
            return fail(card, SD_ERR_NO_RESPONSE);
        }
        if ((r1 & (uint8_t)~R1_IDLE) == 0u) {
            r1 = command(41, v2 ? HCS : 0u, NULL, false);
            if (r1 == NO_REPLY) {
                return fail(card, SD_ERR_NO_RESPONSE);
            }
        }
        if ((r1 & R1_ILLEGAL) != 0u && !v2) {
            return fail(card, SD_ERR_UNSUPPORTED);    /* 老式 MMC 卡 */
        }
        if ((r1 & (uint8_t)~R1_IDLE) != 0u) {
            return fail(card, SD_ERR_CMD);
        }
    }
    if (r1 != 0u) {
        return fail(card, SD_ERR_TIMEOUT);
    }
    sd_type_t type = SD_TYPE_V1;
    if (v2) {
        uint8_t ocr[4];
        r1 = command(58, 0, ocr, false);
        if (r1 == NO_REPLY) {
            return fail(card, SD_ERR_NO_RESPONSE);
        }
        if (r1 != 0u) {
            return fail(card, SD_ERR_CMD);
        }
        type = (ocr[0] & 0x40u) ? SD_TYPE_V2_HC : SD_TYPE_V2_SC;
    }
    if (type != SD_TYPE_V2_HC) {
        r1 = command(16, SD_BLOCK_SIZE, NULL, false);
        if (r1 == NO_REPLY) {
            return fail(card, SD_ERR_NO_RESPONSE);
        }
        if (r1 != 0u) {
            return fail(card, SD_ERR_CMD);
        }
    }
    card->type = (uint8_t)type;
    card->ready = true;
    return SD_OK;
}

sd_type_t sd_type(const sd_card_t *card)
{
    return (card == NULL || !card->ready) ? SD_TYPE_NONE : (sd_type_t)card->type;
}

sd_status_t sd_read_block(const sd_card_t *card, uint32_t block, uint8_t buf[SD_BLOCK_SIZE])
{
    sd_status_t st = SD_ERR_READ;
    if (card == NULL || buf == NULL || !card->ready) {
        return SD_ERR_PARAM;
    }
    uint32_t arg = (card->type == (uint8_t)SD_TYPE_V2_HC) ? block : block * SD_BLOCK_SIZE;
    uint8_t r1 = command(17, arg, NULL, true);
    if (r1 == NO_REPLY) {
        return SD_ERR_NO_RESPONSE;
    }
    if (r1 != 0u) {
        st = SD_ERR_CMD;
    } else {
        for (unsigned i = 0; i < SD_TOKEN_POLLS; i++) {
            uint8_t t = hal_sd_xfer(0xFFu);
            if (t == TOKEN_START) {
                for (unsigned k = 0; k < SD_BLOCK_SIZE; k++) {
                    buf[k] = hal_sd_xfer(0xFFu);
                }
                (void)hal_sd_xfer(0xFFu);           /* CRC16：SPI 模式下不校验，但要读掉 */
                (void)hal_sd_xfer(0xFFu);
                st = SD_OK;
                break;
            }
            if ((t & 0xF0u) == 0u) {
                break;                              /* 错误标志 0000xxxx */
            }
        }
    }
    hal_sd_select(false);
    (void)hal_sd_xfer(0xFFu);
    return st;
}
