/* 隐藏测试：只有评测流程能读。每个用例单独开机（软重启），前一个用例写进 NVS 的数据后一个用例还读得到。 */
#include <string.h>
#include "unity.h"
#include "nvs.h"
#include "ff_test.h"
#include "kv.h"

/* 独立核对：测试自己直接用 NVS 读，不信被测函数 */
static esp_err_t raw_get_u32(const char *key, uint32_t *v)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open("ff_cfg", NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_get_u32(h, key, v);
    nvs_close(h);
    return err;
}

TEST_CASE("没有初始化时调用返回 ESP_ERR_INVALID_STATE", "[kv]")
{
    uint32_t v = 0;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, kv_set_u32("a", 1));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, kv_get_u32("a", &v));
    kv_deinit();                                       /* 没初始化时也不能出错 */
}

TEST_CASE("清空后写入数字和字符串能读回，NVS 里确实有", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());              /* 重复初始化直接返回 ESP_OK */
    TEST_ASSERT_EQUAL(ESP_OK, kv_erase_all());
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_u32("boot_cnt", 41));
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_u32("boot_cnt", 42));
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_str("name", "firmforge"));
    uint32_t v = 0, raw = 0;
    char s[16];
    TEST_ASSERT_EQUAL(ESP_OK, kv_get_u32("boot_cnt", &v));
    TEST_ASSERT_EQUAL_UINT32(42, v);
    TEST_ASSERT_EQUAL(ESP_OK, kv_get_str("name", s, sizeof s));
    TEST_ASSERT_EQUAL_STRING("firmforge", s);
    TEST_ASSERT_EQUAL(ESP_OK, raw_get_u32("boot_cnt", &raw));
    TEST_ASSERT_EQUAL_UINT32(42, raw);
    kv_deinit();
}

TEST_CASE("重启后上一个用例写的值还在", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    uint32_t v = 0;
    char s[16];
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, kv_get_u32("boot_cnt", &v), "重启后读不到 boot_cnt");
    TEST_ASSERT_EQUAL_UINT32(42, v);
    TEST_ASSERT_EQUAL(ESP_OK, kv_get_str("name", s, sizeof s));
    TEST_ASSERT_EQUAL_STRING("firmforge", s);
    kv_deinit();
}

TEST_CASE("读或删除不存在的键返回 ESP_ERR_NOT_FOUND", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    uint32_t v = 7;
    char s[8];
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, kv_get_u32("nope", &v));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, kv_get_str("nope", s, sizeof s));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, kv_erase("nope"));
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_u32("tmp", 5));
    TEST_ASSERT_EQUAL(ESP_OK, kv_erase("tmp"));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, kv_get_u32("tmp", &v));
    kv_deinit();
}

TEST_CASE("字符串缓冲区放不下返回 ESP_ERR_INVALID_SIZE", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_str("s", "abcdef"));
    char s[8];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE, kv_get_str("s", s, 6));     /* 要 7 个字节 */
    TEST_ASSERT_EQUAL(ESP_OK, kv_get_str("s", s, 7));
    TEST_ASSERT_EQUAL_STRING("abcdef", s);
    kv_deinit();
}

TEST_CASE("非法参数：键名为空或超过 15 个字符、空指针", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    uint32_t v;
    char s[8];
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_set_u32("", 1));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_set_u32(NULL, 1));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_set_u32("a_very_long_key_x", 1));   /* 17 个字符 */
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_u32("key_15_chars_ok", 1));                  /* 正好 15 个 */
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_get_u32("a", NULL));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_set_str("a", NULL));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_get_str("a", NULL, 8));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, kv_get_str("a", s, 0));
    kv_deinit();
}

TEST_CASE("反初始化后可以再初始化，数据还在", "[kv]")
{
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    TEST_ASSERT_EQUAL(ESP_OK, kv_set_u32("again", 9));
    kv_deinit();
    uint32_t v = 0;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, kv_get_u32("again", &v));
    TEST_ASSERT_EQUAL(ESP_OK, kv_init());
    TEST_ASSERT_EQUAL(ESP_OK, kv_get_u32("again", &v));
    TEST_ASSERT_EQUAL_UINT32(9, v);
    TEST_ASSERT_EQUAL(ESP_OK, kv_erase_all());
    kv_deinit();
}
