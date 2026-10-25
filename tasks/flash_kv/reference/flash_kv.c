#include "flash_kv.h"

#include "hal_flash.h"

#define MAGIC0 0x4Bu   /* 'K' */
#define MAGIC1 0x56u   /* 'V' */
#define MAX_RECORD 72u /* 6 + 64，补齐到 4 的倍数 */

typedef struct {
    uint16_t id;
    uint16_t len;
    uint32_t off;      /* 条目在扇区内的偏移 */
    uint32_t size;     /* 补齐后的大小 */
} entry_t;

uint16_t kv_crc16(const uint8_t *data, size_t len, uint16_t crc)
{
    if (data == NULL) {
        return crc;
    }
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (unsigned b = 0; b < 8u; b++) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static uint32_t base_of(uint8_t sector)
{
    return (uint32_t)sector * KV_SECTOR_SIZE;
}

static uint32_t record_size(uint16_t len)
{
    return ((uint32_t)KV_ENTRY_HEADER + len + 3u) & ~3u;
}

/* 读出 off 处的条目。返回 1 = 有效条目，0 = 到结尾（全 FFh 的头或空间不够放头），−1 = 损坏，−2 = Flash 错误 */
static int read_entry(uint8_t sector, uint32_t off, entry_t *e, uint8_t data[KV_MAX_VALUE])
{
    uint8_t h[KV_ENTRY_HEADER];
    if (off + KV_ENTRY_HEADER > KV_SECTOR_SIZE) {
        return 0;
    }
    if (hal_flash_read(base_of(sector) + off, h, sizeof h) != HAL_FLASH_OK) {
        return -2;
    }
    bool blank = true;
    for (unsigned i = 0; i < sizeof h; i++) {
        blank = blank && h[i] == 0xFFu;
    }
    if (blank) {
        return 0;
    }
    e->id = (uint16_t)(h[0] | (h[1] << 8));
    e->len = (uint16_t)(h[2] | (h[3] << 8));
    e->off = off;
    uint16_t stored = (uint16_t)(h[4] | (h[5] << 8));
    if (e->id == KV_ID_INVALID || e->len > KV_MAX_VALUE) {
        return -1;
    }
    e->size = record_size(e->len);
    if (off + e->size > KV_SECTOR_SIZE) {
        return -1;
    }
    if (e->len > 0u && hal_flash_read(base_of(sector) + off + KV_ENTRY_HEADER, data, e->len) != HAL_FLASH_OK) {
        return -2;
    }
    uint16_t crc = kv_crc16(h, 4, 0xFFFFu);
    crc = kv_crc16(data, e->len, crc);
    return crc == stored ? 1 : -1;
}

/* 找 id 的最新条目：返回 1 找到、0 没有、−2 Flash 错误；data 为最新值 */
static int find_latest(const kv_t *kv, uint16_t id, entry_t *found, uint8_t data[KV_MAX_VALUE])
{
    entry_t e;
    uint8_t tmp[KV_MAX_VALUE];
    int hit = 0;
    for (uint32_t off = KV_HEADER_SIZE; off < kv->write_off;) {
        int r = read_entry(kv->active, off, &e, tmp);
        if (r == -2) {
            return -2;
        }
        if (r != 1) {
            break;
        }
        if (e.id == id) {
            *found = e;
            for (unsigned i = 0; i < e.len; i++) {
                data[i] = tmp[i];
            }
            hit = 1;
        }
        off += e.size;
    }
    return hit;
}

static uint32_t build_record(uint8_t rec[MAX_RECORD], uint16_t id, const uint8_t *data, uint16_t len)
{
    uint32_t size = record_size(len);
    rec[0] = (uint8_t)id;
    rec[1] = (uint8_t)(id >> 8);
    rec[2] = (uint8_t)len;
    rec[3] = (uint8_t)(len >> 8);
    uint16_t crc = kv_crc16(rec, 4, 0xFFFFu);
    crc = kv_crc16(data, len, crc);
    rec[4] = (uint8_t)crc;
    rec[5] = (uint8_t)(crc >> 8);
    for (uint32_t i = 0; i < size - KV_ENTRY_HEADER; i++) {
        rec[KV_ENTRY_HEADER + i] = (i < len) ? data[i] : 0xFFu;
    }
    return size;
}

/* 扫描扇区，返回下一条的写入位置；遇到损坏的条目返回扇区大小（之后的空间不再使用） */
static int scan(uint8_t sector, uint32_t *write_off)
{
    entry_t e;
    uint8_t tmp[KV_MAX_VALUE];
    uint32_t off = KV_HEADER_SIZE;
    for (;;) {
        int r = read_entry(sector, off, &e, tmp);
        if (r == -2) {
            return -2;
        }
        if (r == 0) {
            *write_off = off;
            return 0;
        }
        if (r == -1) {
            *write_off = KV_SECTOR_SIZE;
            return 0;
        }
        off += e.size;
    }
}

static int read_header(uint8_t sector, uint16_t *seq)
{
    uint8_t h[KV_HEADER_SIZE];
    if (hal_flash_read(base_of(sector), h, sizeof h) != HAL_FLASH_OK) {
        return -2;
    }
    *seq = (uint16_t)(h[2] | (h[3] << 8));
    return (h[0] == MAGIC0 && h[1] == MAGIC1) ? 1 : 0;
}

static bool write_header(uint8_t sector, uint16_t seq)
{
    const uint8_t h[KV_HEADER_SIZE] = {MAGIC0, MAGIC1, (uint8_t)seq, (uint8_t)(seq >> 8)};
    return hal_flash_write(base_of(sector), h, sizeof h) == HAL_FLASH_OK;
}

kv_status_t kv_mount(kv_t *kv)
{
    uint16_t s0, s1;
    if (kv == NULL) {
        return KV_ERR_PARAM;
    }
    kv->mounted = false;
    int v0 = read_header(0, &s0);
    int v1 = read_header(1, &s1);
    if (v0 < 0 || v1 < 0) {
        return KV_ERR_FLASH;
    }
    if (v0 == 0 && v1 == 0) {                         /* 空白 Flash：格式化 */
        if (hal_flash_erase(0) != HAL_FLASH_OK || hal_flash_erase(1) != HAL_FLASH_OK || !write_header(0, 0)) {
            return KV_ERR_FLASH;
        }
        kv->active = 0;
        kv->seq = 0;
    } else if (v0 == 1 && v1 == 1) {                  /* 整理到最后一步时断电：序号新的为准 */
        uint16_t d = (uint16_t)(s1 - s0);
        kv->active = (d != 0u && d < 0x8000u) ? 1u : 0u;
        kv->seq = kv->active ? s1 : s0;
        if (hal_flash_erase(kv->active ? 0u : 1u) != HAL_FLASH_OK) {
            return KV_ERR_FLASH;
        }
    } else {
        kv->active = v0 == 1 ? 0u : 1u;
        kv->seq = v0 == 1 ? s0 : s1;
    }
    if (scan(kv->active, &kv->write_off) != 0) {
        return KV_ERR_FLASH;
    }
    kv->mounted = true;
    return KV_OK;
}

/* 整理：把有效的最新值（跳过 skip_id）复制到另一个扇区，追加 rec（可为 NULL），最后写头、擦旧扇区 */
static kv_status_t collect(kv_t *kv, uint16_t skip_id, const uint8_t *rec, uint32_t rec_size)
{
    uint8_t other = (uint8_t)(1u - kv->active);
    uint32_t dst = KV_HEADER_SIZE;
    entry_t e, latest;
    uint8_t data[KV_MAX_VALUE], latest_data[KV_MAX_VALUE], buf[MAX_RECORD];
    if (hal_flash_erase(other) != HAL_FLASH_OK) {
        return KV_ERR_FLASH;
    }
    for (uint32_t off = KV_HEADER_SIZE; off < kv->write_off;) {
        int r = read_entry(kv->active, off, &e, data);
        if (r == -2) {
            return KV_ERR_FLASH;
        }
        if (r != 1) {
            break;
        }
        off += e.size;
        if (e.id == skip_id || e.len == 0u) {
            continue;                                 /* 正在写的 id、已删除的都不复制 */
        }
        if (find_latest(kv, e.id, &latest, latest_data) != 1 || latest.off != e.off) {
            continue;                                 /* 不是最新的一条 */
        }
        uint32_t size = build_record(buf, e.id, data, e.len);
        if (dst + size > KV_SECTOR_SIZE) {
            return KV_ERR_NO_SPACE;
        }
        if (hal_flash_write(base_of(other) + dst, buf, size) != HAL_FLASH_OK) {
            return KV_ERR_FLASH;
        }
        dst += size;
    }
    if (rec != NULL) {
        if (dst + rec_size > KV_SECTOR_SIZE) {
            return KV_ERR_NO_SPACE;                   /* 旧扇区还没动，原数据保持不变 */
        }
        if (hal_flash_write(base_of(other) + dst, rec, rec_size) != HAL_FLASH_OK) {
            return KV_ERR_FLASH;
        }
        dst += rec_size;
    }
    uint16_t seq = (uint16_t)(kv->seq + 1u);
    if (!write_header(other, seq)) {
        return KV_ERR_FLASH;
    }
    uint8_t old = kv->active;
    kv->active = other;
    kv->seq = seq;
    kv->write_off = dst;
    if (hal_flash_erase(old) != HAL_FLASH_OK) {
        return KV_ERR_FLASH;
    }
    return KV_OK;
}

static kv_status_t append(kv_t *kv, uint16_t id, const uint8_t *data, uint16_t len)
{
    uint8_t rec[MAX_RECORD];
    uint32_t size = build_record(rec, id, data, len);
    if (kv->write_off + size <= KV_SECTOR_SIZE) {
        if (hal_flash_write(base_of(kv->active) + kv->write_off, rec, size) != HAL_FLASH_OK) {
            kv->write_off = KV_SECTOR_SIZE;          /* 写了一半：这片空间不能再用 */
            return KV_ERR_FLASH;
        }
        kv->write_off += size;
        return KV_OK;
    }
    return collect(kv, id, len > 0u ? rec : NULL, size);
}

kv_status_t kv_write(kv_t *kv, uint16_t id, const uint8_t *data, size_t len)
{
    entry_t e;
    uint8_t cur[KV_MAX_VALUE];
    if (kv == NULL || id == KV_ID_INVALID || data == NULL || len == 0u || len > KV_MAX_VALUE) {
        return KV_ERR_PARAM;
    }
    if (!kv->mounted) {
        return KV_ERR_NOT_MOUNTED;
    }
    int f = find_latest(kv, id, &e, cur);
    if (f == -2) {
        return KV_ERR_FLASH;
    }
    if (f == 1 && e.len == len) {
        bool same = true;
        for (size_t i = 0; i < len; i++) {
            same = same && cur[i] == data[i];
        }
        if (same) {
            return KV_OK;                             /* 没变就不写 */
        }
    }
    return append(kv, id, data, (uint16_t)len);
}

kv_status_t kv_read(kv_t *kv, uint16_t id, uint8_t *buf, size_t cap, size_t *len)
{
    entry_t e;
    uint8_t cur[KV_MAX_VALUE];
    if (kv == NULL || len == NULL || (buf == NULL && cap > 0u) || id == KV_ID_INVALID) {
        return KV_ERR_PARAM;
    }
    if (!kv->mounted) {
        return KV_ERR_NOT_MOUNTED;
    }
    int f = find_latest(kv, id, &e, cur);
    if (f == -2) {
        return KV_ERR_FLASH;
    }
    if (f == 0 || e.len == 0u) {
        return KV_ERR_NOT_FOUND;
    }
    *len = e.len;
    if (cap < e.len) {
        return KV_ERR_BUF_TOO_SMALL;
    }
    for (unsigned i = 0; i < e.len; i++) {
        buf[i] = cur[i];
    }
    return KV_OK;
}

kv_status_t kv_delete(kv_t *kv, uint16_t id)
{
    entry_t e;
    uint8_t cur[KV_MAX_VALUE];
    if (kv == NULL || id == KV_ID_INVALID) {
        return KV_ERR_PARAM;
    }
    if (!kv->mounted) {
        return KV_ERR_NOT_MOUNTED;
    }
    int f = find_latest(kv, id, &e, cur);
    if (f == -2) {
        return KV_ERR_FLASH;
    }
    if (f == 0 || e.len == 0u) {
        return KV_OK;                                 /* 本来就没有 */
    }
    return append(kv, id, cur, 0);
}

size_t kv_free_space(const kv_t *kv)
{
    return (kv == NULL || !kv->mounted) ? 0u : KV_SECTOR_SIZE - kv->write_off;
}
