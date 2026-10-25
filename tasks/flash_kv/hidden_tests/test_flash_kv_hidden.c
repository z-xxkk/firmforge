/* flash_kv 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "flash_kv.h"
#include "mock_flash.h"
#include "unity.h"

static kv_t kv;
static uint8_t snap[MOCK_FLASH_SIZE];

void setUp(void)
{
    mock_flash_blank();
    memset(&kv, 0xA5, sizeof kv);
}

void tearDown(void)
{
    TEST_ASSERT_EQUAL_UINT_MESSAGE(0, mock_violations, "把 0 写回了 1（Flash 不能原地改写）");
}

/* 测试自己的 CRC-16/CCITT-FALSE（逐位，另一种写法） */
static uint16_t crc_ref(const uint8_t *d, size_t n)
{
    uint32_t crc = 0xFFFF;
    for (size_t i = 0; i < n; i++) {
        for (int b = 7; b >= 0; b--) {
            uint32_t bit = ((crc >> 15) ^ (uint32_t)(d[i] >> b)) & 1u;
            crc = (crc << 1) & 0xFFFFu;
            if (bit) {
                crc ^= 0x1021u;
            }
        }
    }
    return (uint16_t)crc;
}

static void fill(uint8_t *v, size_t n, uint8_t seed)
{
    for (size_t i = 0; i < n; i++) {
        v[i] = (uint8_t)(seed * 17u + i * 5u + 3u);
    }
}

static void mount_ok(void)
{
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_mount(&kv));
}

static void write_ok(uint16_t id, uint8_t seed, size_t n)
{
    uint8_t v[KV_MAX_VALUE];
    fill(v, n, seed);
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_write(&kv, id, v, n));
}

static void expect_value(uint16_t id, uint8_t seed, size_t n)
{
    uint8_t got[KV_MAX_VALUE], want[KV_MAX_VALUE];
    size_t len = 999;
    fill(want, n, seed);
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, id, got, sizeof got, &len));
    TEST_ASSERT_EQUAL_UINT(n, len);
    TEST_ASSERT_EQUAL_UINT8_ARRAY(want, got, n);
}

static void expect_missing(uint16_t id)
{
    uint8_t got[KV_MAX_VALUE];
    size_t len;
    TEST_ASSERT_EQUAL_INT(KV_ERR_NOT_FOUND, kv_read(&kv, id, got, sizeof got, &len));
}

static void test_crc_reference_value(void)
{
    TEST_ASSERT_EQUAL_HEX16(0x29B1, kv_crc16((const uint8_t *)"123456789", 9, 0xFFFF));
    TEST_ASSERT_EQUAL_HEX16(0x29B1, crc_ref((const uint8_t *)"123456789", 9));
    uint16_t part = kv_crc16((const uint8_t *)"1234", 4, 0xFFFF);
    TEST_ASSERT_EQUAL_HEX16(0x29B1, kv_crc16((const uint8_t *)"56789", 5, part));   /* 可以分段算 */
}

static void test_blank_flash_is_formatted(void)
{
    memset(mock_flash, 0x00, 1024);                              /* 垃圾内容 */
    mount_ok();
    const uint8_t hdr[4] = {0x4B, 0x56, 0x00, 0x00};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(hdr, mock_flash, 4);
    TEST_ASSERT_EQUAL_UINT(1020, kv_free_space(&kv));
    for (unsigned i = 1024; i < 2048; i++) {
        TEST_ASSERT_EQUAL_HEX8(0xFF, mock_flash[i]);
    }
}

static void test_spec_example_record_bytes(void)
{
    mount_ok();
    const uint8_t v[2] = {0x11, 0x22};
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_write(&kv, 1, v, 2));
    const uint8_t body[6] = {0x01, 0x00, 0x02, 0x00, 0x11, 0x22};
    uint16_t crc = crc_ref(body, 6);
    const uint8_t want[8] = {0x01, 0x00, 0x02, 0x00, (uint8_t)crc, (uint8_t)(crc >> 8), 0x11, 0x22};
    TEST_ASSERT_EQUAL_HEX8_ARRAY(want, &mock_flash[4], 8);
    TEST_ASSERT_EQUAL_UINT(1012, kv_free_space(&kv));
    write_ok(2, 9, 3);                                           /* 6 + 3 → 补齐到 12 */
    TEST_ASSERT_EQUAL_UINT(1000, kv_free_space(&kv));
    TEST_ASSERT_EQUAL_HEX8(0xFF, mock_flash[4 + 8 + 11]);         /* 补的是 FFh */
}

static void test_latest_value_wins_and_survives_remount(void)
{
    mount_ok();
    write_ok(7, 1, 10);
    write_ok(8, 2, 64);
    write_ok(7, 3, 5);
    expect_value(7, 3, 5);
    expect_value(8, 2, 64);
    expect_missing(9);
    memset(&kv, 0, sizeof kv);
    mount_ok();                                                  /* 重新上电 */
    expect_value(7, 3, 5);
    expect_value(8, 2, 64);
}

static void test_unchanged_value_not_written(void)
{
    mount_ok();
    write_ok(3, 4, 20);
    unsigned writes = mock_write_calls;
    size_t free_before = kv_free_space(&kv);
    write_ok(3, 4, 20);
    TEST_ASSERT_EQUAL_UINT(writes, mock_write_calls);
    TEST_ASSERT_EQUAL_UINT(free_before, kv_free_space(&kv));
    write_ok(3, 4, 21);                                          /* 长度不同就要写 */
    TEST_ASSERT_EQUAL_UINT(writes + 1, mock_write_calls);
}

static void test_delete(void)
{
    mount_ok();
    write_ok(5, 1, 4);
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_delete(&kv, 5));
    expect_missing(5);
    unsigned writes = mock_write_calls;
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_delete(&kv, 5));             /* 已删除：不再写 */
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_delete(&kv, 6));             /* 从没有过 */
    TEST_ASSERT_EQUAL_UINT(writes, mock_write_calls);
    write_ok(5, 2, 4);
    expect_value(5, 2, 4);
    mount_ok();
    expect_value(5, 2, 4);
}

static void test_read_buffer_too_small(void)
{
    mount_ok();
    write_ok(1, 1, 30);
    uint8_t buf[10];
    size_t len = 0;
    TEST_ASSERT_EQUAL_INT(KV_ERR_BUF_TOO_SMALL, kv_read(&kv, 1, buf, sizeof buf, &len));
    TEST_ASSERT_EQUAL_UINT(30, len);
    TEST_ASSERT_EQUAL_INT(KV_ERR_BUF_TOO_SMALL, kv_read(&kv, 1, NULL, 0, &len));   /* 只问长度 */
    TEST_ASSERT_EQUAL_UINT(30, len);
}

static void test_garbage_collection_keeps_latest(void)
{
    mount_ok();
    write_ok(100, 50, 8);
    write_ok(101, 51, 8);
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_delete(&kv, 101));
    for (unsigned i = 0; i < 200; i++) {                          /* 反复改 3 个 id，必然触发整理 */
        write_ok((uint16_t)(i % 3), (uint8_t)i, 40);
    }
    TEST_ASSERT_TRUE(mock_erases >= 3);
    expect_value(0, 198, 40);
    expect_value(1, 199, 40);
    expect_value(2, 197, 40);
    expect_value(100, 50, 8);
    expect_missing(101);                                          /* 已删除的不会被复制回来 */
    mount_ok();
    expect_value(1, 199, 40);
    expect_value(100, 50, 8);
    expect_missing(101);
    /* 只有一个扇区有头，另一个已擦除 */
    unsigned heads = (mock_flash[0] == 0x4B && mock_flash[1] == 0x56) + (mock_flash[1024] == 0x4B && mock_flash[1025] == 0x56);
    TEST_ASSERT_EQUAL_UINT(1, heads);
}

static void test_sequence_number_increments(void)
{
    mount_ok();
    unsigned erases = mock_erases;
    for (unsigned i = 0; i < 40; i++) {
        write_ok(1, (uint8_t)i, 64);
    }
    TEST_ASSERT_TRUE(mock_erases > erases);
    uint32_t a = (mock_flash[0] == 0x4B) ? 0u : 1024u;
    uint16_t seq = (uint16_t)(mock_flash[a + 2] | (mock_flash[a + 3] << 8));
    TEST_ASSERT_TRUE(seq >= 2);
    expect_value(1, 39, 64);
}

static void test_no_space_keeps_old_data(void)
{
    mount_ok();
    kv_status_t st = KV_OK;
    unsigned id;
    for (id = 0; id < 20 && st == KV_OK; id++) {                  /* 每条 72 字节，最多放 14 条 */
        uint8_t v[64];
        fill(v, 64, (uint8_t)id);
        st = kv_write(&kv, (uint16_t)id, v, 64);
    }
    TEST_ASSERT_EQUAL_INT(KV_ERR_NO_SPACE, st);
    unsigned stored = id - 1;
    TEST_ASSERT_EQUAL_UINT(14, stored);
    for (unsigned k = 0; k < stored; k++) {
        expect_value((uint16_t)k, (uint8_t)k, 64);
    }
    expect_missing((uint16_t)stored);
    write_ok(3, 99, 64);                                          /* 改已有的 id 仍然可以 */
    expect_value(3, 99, 64);
    mount_ok();
    expect_value(3, 99, 64);
    expect_value(13, 13, 64);
}

static void test_both_headers_valid_picks_newer(void)
{
    static const uint16_t pairs[][2] = {{5, 6}, {6, 5}, {0xFFFF, 0x0000}, {0x0000, 0xFFFF}};
    static const unsigned newer[] = {1, 0, 1, 0};
    for (unsigned i = 0; i < 4; i++) {
        mock_flash_blank();
        TEST_ASSERT_EQUAL_INT(KV_OK, kv_mount(&kv));
        write_ok(1, 10, 4);                                       /* 扇区 0 的值 */
        memcpy(&mock_flash[1024], mock_flash, 1024);              /* 扇区 1 复制一份再改值 */
        uint8_t v[4];
        fill(v, 4, 20);
        uint8_t body[8] = {0x01, 0x00, 0x04, 0x00, v[0], v[1], v[2], v[3]};
        uint16_t c = crc_ref(body, 8);
        const uint8_t rec[10] = {0x01, 0x00, 0x04, 0x00, (uint8_t)c, (uint8_t)(c >> 8), v[0], v[1], v[2], v[3]};
        memcpy(&mock_flash[1024 + 16], rec, 10);
        mock_flash[2] = (uint8_t)pairs[i][0];
        mock_flash[3] = (uint8_t)(pairs[i][0] >> 8);
        mock_flash[1024 + 2] = (uint8_t)pairs[i][1];
        mock_flash[1024 + 3] = (uint8_t)(pairs[i][1] >> 8);
        mount_ok();
        expect_value(1, newer[i] ? 20 : 10, 4);
        uint32_t other = newer[i] ? 0u : 1024u;
        TEST_ASSERT_EQUAL_HEX8(0xFF, mock_flash[other]);          /* 旧的那个被擦除 */
    }
}

static void test_corrupt_record_stops_scan(void)
{
    mount_ok();
    write_ok(1, 1, 8);
    write_ok(2, 2, 8);
    mock_flash[4 + 16 + 7] ^= 0x40;                               /* 第 2 条的数据坏了一位 */
    mount_ok();
    expect_value(1, 1, 8);
    expect_missing(2);
    TEST_ASSERT_EQUAL_UINT(0, kv_free_space(&kv));                /* 坏条目之后的空间不再使用 */
    write_ok(3, 3, 8);                                            /* 触发整理后照常写入 */
    expect_value(1, 1, 8);
    expect_value(3, 3, 8);
    TEST_ASSERT_TRUE(kv_free_space(&kv) > 0);
}

/* ---------- 断电测试：在操作的每一个单位断电，重新上电后数据要么是旧值要么是新值 ---------- */

typedef void (*op_fn)(void);
static void op_write_new(void) { uint8_t v[20]; fill(v, 20, 200); (void)kv_write(&kv, 1, v, 20); }
static void op_delete(void) { (void)kv_delete(&kv, 1); }

static void prepare_nearly_full(void)
{
    mock_flash_blank();
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_mount(&kv));
    write_ok(1, 1, 20);
    write_ok(2, 2, 30);
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_delete(&kv, 2));
    write_ok(3, 3, 64);
    unsigned i = 0;
    while (kv_free_space(&kv) >= 28u + 72u) {                     /* 填到只差一点点 */
        write_ok(4, (uint8_t)(i++), 64);
    }
    while (kv_free_space(&kv) >= 28u) {
        write_ok(5, (uint8_t)(i++), 4);
    }
}

static void power_cut_everywhere(op_fn op, bool deleting, bool expect_gc)
{
    uint8_t v4[64], v5[64];
    size_t n4, n5;
    prepare_nearly_full();
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, 4, v4, sizeof v4, &n4));
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, 5, v5, sizeof v5, &n5));
    memcpy(snap, mock_flash, sizeof snap);
    kv_t saved = kv;
    unsigned before_units = mock_units, before_erases = mock_erases;
    op();
    unsigned total = mock_units - before_units;
    TEST_ASSERT_TRUE(total > 0);
    if (expect_gc) {
        TEST_ASSERT_TRUE_MESSAGE(mock_erases > before_erases, "这个场景应当触发整理");
    }
    for (unsigned k = 0; k <= total; k++) {
        memcpy(mock_flash, snap, sizeof snap);
        kv = saved;
        mock_cut_after(k);
        op();
        mock_power_on();
        memset(&kv, 0, sizeof kv);
        TEST_ASSERT_EQUAL_INT(KV_OK, kv_mount(&kv));
        uint8_t got[64];
        size_t len = 0;
        kv_status_t st = kv_read(&kv, 1, got, sizeof got, &len);
        uint8_t want_old[20], want_new[20];
        fill(want_old, 20, 1);
        fill(want_new, 20, 200);
        bool is_old = st == KV_OK && len == 20 && memcmp(got, want_old, 20) == 0;
        bool is_new = deleting ? st == KV_ERR_NOT_FOUND : (st == KV_OK && len == 20 && memcmp(got, want_new, 20) == 0);
        TEST_ASSERT_TRUE_MESSAGE(is_old || is_new, "断电后 id 1 既不是旧值也不是新值");
        if (k == total) {
            TEST_ASSERT_TRUE_MESSAGE(is_new, "没有断电时应当是新值");
        }
        expect_value(3, 3, 64);                                    /* 其他 id 不受影响 */
        TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, 4, got, sizeof got, &len));
        TEST_ASSERT_EQUAL_UINT(n4, len);
        TEST_ASSERT_EQUAL_UINT8_ARRAY(v4, got, n4);
        TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, 5, got, sizeof got, &len));
        TEST_ASSERT_EQUAL_UINT8_ARRAY(v5, got, n5);
        expect_missing(2);
        write_ok(9, 9, 16);                                        /* 恢复后还能继续正常使用 */
        expect_value(9, 9, 16);
    }
}

static void test_power_cut_during_append(void)
{
    mock_flash_blank();
    mount_ok();
    write_ok(1, 1, 20);
    write_ok(3, 3, 64);
    memcpy(snap, mock_flash, sizeof snap);
    kv_t saved = kv;
    for (unsigned k = 0; k <= 28; k++) {
        memcpy(mock_flash, snap, sizeof snap);
        kv = saved;
        mock_cut_after(k);
        op_write_new();
        mock_power_on();
        mount_ok();
        uint8_t got[64];
        size_t len = 0;
        TEST_ASSERT_EQUAL_INT(KV_OK, kv_read(&kv, 1, got, sizeof got, &len));
        uint8_t want_old[20], want_new[20];
        fill(want_old, 20, 1);
        fill(want_new, 20, 200);
        bool is_new = memcmp(got, want_new, 20) == 0;
        TEST_ASSERT_TRUE_MESSAGE(is_new || memcmp(got, want_old, 20) == 0, "断电后既不是旧值也不是新值");
        if (k < 26) {
            TEST_ASSERT_FALSE_MESSAGE(is_new, "条目没写完却被当成了有效");   /* 26 字节之后只剩补齐的 FFh */
        } else {
            TEST_ASSERT_TRUE(is_new);
        }
        expect_value(3, 3, 64);
        write_ok(9, 9, 16);
        expect_value(9, 9, 16);
    }
}

static void test_power_cut_during_gc_write(void) { power_cut_everywhere(op_write_new, false, true); }
static void test_power_cut_during_gc_delete(void) { power_cut_everywhere(op_delete, true, false); }

static void test_parameters_and_unmounted(void)
{
    uint8_t v[4] = {1, 2, 3, 4};
    size_t len;
    kv.mounted = false;
    TEST_ASSERT_EQUAL_INT(KV_ERR_NOT_MOUNTED, kv_write(&kv, 1, v, 4));
    TEST_ASSERT_EQUAL_INT(KV_ERR_NOT_MOUNTED, kv_read(&kv, 1, v, 4, &len));
    TEST_ASSERT_EQUAL_INT(KV_ERR_NOT_MOUNTED, kv_delete(&kv, 1));
    TEST_ASSERT_EQUAL_UINT(0, kv_free_space(&kv));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_mount(NULL));
    mount_ok();
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_write(NULL, 1, v, 4));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_write(&kv, 0xFFFF, v, 4));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_write(&kv, 1, NULL, 4));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_write(&kv, 1, v, 0));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_write(&kv, 1, v, 65));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_read(&kv, 1, v, 4, NULL));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_read(&kv, 1, NULL, 4, &len));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_read(&kv, 0xFFFF, v, 4, &len));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_delete(&kv, 0xFFFF));
    TEST_ASSERT_EQUAL_INT(KV_ERR_PARAM, kv_delete(NULL, 1));
    TEST_ASSERT_EQUAL_UINT(0, kv_free_space(NULL));
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_write(&kv, 0xFFFE, v, 4));   /* 最大合法 id */
    TEST_ASSERT_EQUAL_INT(KV_OK, kv_write(&kv, 0, v, 1));
}

static void test_flash_read_error(void)
{
    mount_ok();
    write_ok(1, 1, 4);
    mock_read_fail = true;
    uint8_t v[4];
    size_t len;
    TEST_ASSERT_EQUAL_INT(KV_ERR_FLASH, kv_read(&kv, 1, v, sizeof v, &len));
    TEST_ASSERT_EQUAL_INT(KV_ERR_FLASH, kv_mount(&kv));
    mock_read_fail = false;
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_crc_reference_value);
    RUN_TEST(test_blank_flash_is_formatted);
    RUN_TEST(test_spec_example_record_bytes);
    RUN_TEST(test_latest_value_wins_and_survives_remount);
    RUN_TEST(test_unchanged_value_not_written);
    RUN_TEST(test_delete);
    RUN_TEST(test_read_buffer_too_small);
    RUN_TEST(test_garbage_collection_keeps_latest);
    RUN_TEST(test_sequence_number_increments);
    RUN_TEST(test_no_space_keeps_old_data);
    RUN_TEST(test_both_headers_valid_picks_newer);
    RUN_TEST(test_corrupt_record_stops_scan);
    RUN_TEST(test_power_cut_during_append);
    RUN_TEST(test_power_cut_during_gc_write);
    RUN_TEST(test_power_cut_during_gc_delete);
    RUN_TEST(test_parameters_and_unmounted);
    RUN_TEST(test_flash_read_error);
    return UNITY_END();
}
