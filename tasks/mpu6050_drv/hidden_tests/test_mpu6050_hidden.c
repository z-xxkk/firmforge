/* mpu6050_drv 隐藏参考测试：Agent 永远看不到本文件 */
#include <string.h>

#include "unity.h"
#include "mpu6050.h"
#include "mock_hal_i2c.h"

static mpu6050_t dev;

void setUp(void)
{
    memset(&dev, 0xA5, sizeof dev);
    mock_reset(0x68);
}

void tearDown(void) {}

static void init_ok(mpu6050_accel_range_t ar, mpu6050_gyro_range_t gr)
{
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_init(&dev, false, ar, gr));
    mock_log_count = 0;   /* 只看之后的读操作 */
}

static void test_init_configures_registers(void)
{
    for (int ar = 0; ar < 4; ar++) {
        for (int gr = 0; gr < 4; gr++) {
            mock_reset(0x68);
            TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_init(&dev, false, (mpu6050_accel_range_t)ar,
                                                           (mpu6050_gyro_range_t)gr));
            TEST_ASSERT_EQUAL_HEX8(0x01, mock_regs[0x6B]);
            TEST_ASSERT_EQUAL_HEX8((uint8_t)(gr << 3), mock_regs[0x1B]);
            TEST_ASSERT_EQUAL_HEX8((uint8_t)(ar << 3), mock_regs[0x1C]);
        }
    }
}

static void test_init_reads_who_am_i_first(void)
{
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_init(&dev, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
    TEST_ASSERT_TRUE(mock_log_count >= 2);
    TEST_ASSERT_FALSE(mock_log[0].is_write);
    TEST_ASSERT_EQUAL_HEX8(0x68, mock_log[0].addr);
    TEST_ASSERT_EQUAL_HEX8(0x75, mock_log[0].reg);
    for (unsigned i = 0; i < mock_log_count; i++) {
        TEST_ASSERT_EQUAL_HEX8(0x68, mock_log[i].addr);
    }
}

static void test_ad0_high_uses_0x69(void)
{
    mock_reset(0x69);
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_init(&dev, true, MPU6050_ACCEL_4G, MPU6050_GYRO_500DPS));
    TEST_ASSERT_EQUAL_HEX8(0x69, mock_log[0].addr);
    mock_reset(0x69);
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_BUS, mpu6050_init(&dev, false, MPU6050_ACCEL_4G, MPU6050_GYRO_500DPS));
}

static void test_wrong_id_rejected_without_writes(void)
{
    mock_regs[0x75] = 0x98;   /* 兼容芯片 */
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_ID, mpu6050_init(&dev, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
    TEST_ASSERT_EQUAL_UINT(0, mock_write_count());
    TEST_ASSERT_EQUAL_HEX8(0x40, mock_regs[0x6B]);
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(&dev, &d));
}

static void test_bus_error_on_id_read(void)
{
    mock_fail_on(1);
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_BUS, mpu6050_init(&dev, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
    TEST_ASSERT_EQUAL_UINT(1, mock_log_count);
}

static void test_bus_error_mid_init_stops(void)
{
    mock_fail_on(2);          /* 第一次写失败 */
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_BUS, mpu6050_init(&dev, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
    TEST_ASSERT_EQUAL_UINT(2, mock_log_count);
    mock_log_count = 0;
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

static void test_invalid_range_no_bus_access(void)
{
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_init(&dev, false, (mpu6050_accel_range_t)4,
                                                          MPU6050_GYRO_250DPS));
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_init(&dev, false, MPU6050_ACCEL_2G,
                                                          (mpu6050_gyro_range_t)7));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_init(NULL, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
}

static void test_read_is_one_burst_of_14_bytes(void)
{
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_UINT(1, mock_log_count);
    TEST_ASSERT_FALSE(mock_log[0].is_write);
    TEST_ASSERT_EQUAL_HEX8(0x3B, mock_log[0].reg);
    TEST_ASSERT_EQUAL_UINT16(14, mock_log[0].len);
}

static void test_raw_values_big_endian(void)
{
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    mock_set_be16(0x3B, 0x1234);
    mock_set_be16(0x3D, -2);
    mock_set_be16(0x3F, -32768);
    mock_set_be16(0x41, 0x0102);
    mock_set_be16(0x43, 32767);
    mock_set_be16(0x45, -256);
    mock_set_be16(0x47, 1);
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_INT16(0x1234, d.accel_raw[0]);
    TEST_ASSERT_EQUAL_INT16(-2, d.accel_raw[1]);
    TEST_ASSERT_EQUAL_INT16(-32768, d.accel_raw[2]);
    TEST_ASSERT_EQUAL_INT16(0x0102, d.temp_raw);
    TEST_ASSERT_EQUAL_INT16(32767, d.gyro_raw[0]);
    TEST_ASSERT_EQUAL_INT16(-256, d.gyro_raw[1]);
    TEST_ASSERT_EQUAL_INT16(1, d.gyro_raw[2]);
}

static void test_accel_conversion_all_ranges(void)
{
    static const int16_t one_g[4] = {16384, 8192, 4096, 2048};
    for (int ar = 0; ar < 4; ar++) {
        mock_reset(0x68);
        init_ok((mpu6050_accel_range_t)ar, MPU6050_GYRO_250DPS);
        mock_set_be16(0x3B, one_g[ar]);
        mock_set_be16(0x3D, (int16_t)(-one_g[ar] / 2));
        mock_set_be16(0x3F, 0);
        mpu6050_data_t d;
        TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
        TEST_ASSERT_EQUAL_INT32(1000, d.accel_mg[0]);
        TEST_ASSERT_EQUAL_INT32(-500, d.accel_mg[1]);
        TEST_ASSERT_EQUAL_INT32(0, d.accel_mg[2]);
    }
}

static void test_gyro_conversion_all_ranges(void)
{
    static const int16_t ten_dps[4] = {1310, 655, 328, 164};
    static const int32_t minus_one[4] = {0, -1, -3, -6};      /* -1000 / 灵敏度×10，向零取整 */
    for (int gr = 0; gr < 4; gr++) {
        mock_reset(0x68);
        init_ok(MPU6050_ACCEL_2G, (mpu6050_gyro_range_t)gr);
        mock_set_be16(0x43, ten_dps[gr]);
        mock_set_be16(0x45, (int16_t)-ten_dps[gr]);
        mock_set_be16(0x47, -1);
        mpu6050_data_t d;
        TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
        TEST_ASSERT_EQUAL_INT32(1000, d.gyro_cdps[0]);     /* 10.00 °/s */
        TEST_ASSERT_EQUAL_INT32(-1000, d.gyro_cdps[1]);
        TEST_ASSERT_EQUAL_INT32(minus_one[gr], d.gyro_cdps[2]);
    }
}

static void test_conversion_truncates_toward_zero(void)
{
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    mock_set_be16(0x3B, 100);      /* 6.10 mg */
    mock_set_be16(0x3D, -100);     /* -6.10 mg */
    mock_set_be16(0x43, 200);      /* 152.67 */
    mock_set_be16(0x45, -200);
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_INT32(6, d.accel_mg[0]);
    TEST_ASSERT_EQUAL_INT32(-6, d.accel_mg[1]);
    TEST_ASSERT_EQUAL_INT32(152, d.gyro_cdps[0]);
    TEST_ASSERT_EQUAL_INT32(-152, d.gyro_cdps[1]);
}

static void test_extreme_raw_values(void)
{
    init_ok(MPU6050_ACCEL_16G, MPU6050_GYRO_2000DPS);
    mock_set_be16(0x3B, -32768);
    mock_set_be16(0x43, 32767);
    mpu6050_data_t d;
    TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_INT32(-16000, d.accel_mg[0]);
    TEST_ASSERT_EQUAL_INT32(199798, d.gyro_cdps[0]);
}

static void test_temperature_conversion(void)
{
    static const int16_t raw[5] = {0, 340, -521, -12419, 20000};
    static const int32_t expect[5] = {3653, 3753, 3500, 1, 9535};   /* -12419 → -3652.6 向零取整 → 1 */
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    for (int i = 0; i < 5; i++) {
        mock_set_be16(0x41, raw[i]);
        mpu6050_data_t d;
        TEST_ASSERT_EQUAL_INT(MPU6050_OK, mpu6050_read(&dev, &d));
        TEST_ASSERT_EQUAL_INT32(expect[i], d.temp_cdeg);
    }
}

static void test_read_bus_error_leaves_output_untouched(void)
{
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    mpu6050_data_t d, before;
    memset(&d, 0x5C, sizeof d);
    before = d;
    mock_fail_on(5);          /* init 用了 4 次，这是第 5 次 */
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_BUS, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_MEMORY(&before, &d, sizeof d);
}

static void test_read_before_init_and_null(void)
{
    mpu6050_t fresh;
    mpu6050_data_t d;
    memset(&fresh, 0, sizeof fresh);
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(&fresh, &d));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(NULL, &d));
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(&dev, NULL));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

static void test_failed_reinit_makes_not_ready(void)
{
    init_ok(MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS);
    mock_regs[0x75] = 0x00;
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_ID, mpu6050_init(&dev, false, MPU6050_ACCEL_2G, MPU6050_GYRO_250DPS));
    mpu6050_data_t d;
    mock_log_count = 0;
    TEST_ASSERT_EQUAL_INT(MPU6050_ERR_PARAM, mpu6050_read(&dev, &d));
    TEST_ASSERT_EQUAL_UINT(0, mock_log_count);
}

int main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_init_configures_registers);
    RUN_TEST(test_init_reads_who_am_i_first);
    RUN_TEST(test_ad0_high_uses_0x69);
    RUN_TEST(test_wrong_id_rejected_without_writes);
    RUN_TEST(test_bus_error_on_id_read);
    RUN_TEST(test_bus_error_mid_init_stops);
    RUN_TEST(test_invalid_range_no_bus_access);
    RUN_TEST(test_read_is_one_burst_of_14_bytes);
    RUN_TEST(test_raw_values_big_endian);
    RUN_TEST(test_accel_conversion_all_ranges);
    RUN_TEST(test_gyro_conversion_all_ranges);
    RUN_TEST(test_conversion_truncates_toward_zero);
    RUN_TEST(test_extreme_raw_values);
    RUN_TEST(test_temperature_conversion);
    RUN_TEST(test_read_bus_error_leaves_output_untouched);
    RUN_TEST(test_read_before_init_and_null);
    RUN_TEST(test_failed_reinit_makes_not_ready);
    return UNITY_END();
}
