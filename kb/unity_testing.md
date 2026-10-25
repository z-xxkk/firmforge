# 用 Unity 写单元测试

自己整理的摘要。

## 测试文件的结构

- 每个测试文件 `tests/test_*.c` 都会单独编译、单独链接成一个程序，必须自带 `main`：`UNITY_BEGIN();`、逐个 `RUN_TEST(test_xxx);`、`return UNITY_END();`。
- 测试函数写成 `static void test_xxx(void)`，一个函数只测一件事。
- `setUp` / `tearDown` 可以省略（框架提供了弱符号的默认实现）；需要每个测试前重置状态时再写。

## 常用断言

- 整数：`TEST_ASSERT_EQUAL_INT`、`TEST_ASSERT_EQUAL_UINT8`、`TEST_ASSERT_EQUAL_UINT16`、`TEST_ASSERT_EQUAL_HEX16`（报错时显示十六进制，适合 CRC 和寄存器值）。
- 布尔：`TEST_ASSERT_TRUE`、`TEST_ASSERT_FALSE`；指针：`TEST_ASSERT_NULL`、`TEST_ASSERT_NOT_NULL`。
- 数组：`TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, actual, len)`、`TEST_ASSERT_EQUAL_MEMORY`。
- 断言的第一个参数是期望值，第二个是实际值，写反了报错信息会误导排查。

## 期望值从哪里来

- 期望值必须能从需求文档推出来；需求给出的例子（如标准校验值）直接拿来用。
- 自己推算期望值时，优先用和实现**不同**的方法算（例如手算几个字节、用规范里的表），避免测试和实现一起错。
- 需求没有规定的行为不要测。

## 驱动类任务的 HAL 替身

- 替身函数（如 `hal_i2c_mem_read`）写在测试文件里，用静态变量记录调用次数、参数和预设的返回值。
- 每个测试开头重置替身的状态；替身要能模拟失败（返回错误码）和需求描述的硬件行为。

## 运行环境

- 测试用 ASan / UBSan 编译：越界访问、整数溢出、未对齐访问都会让测试失败，测试代码本身也要避免这些问题。
- 单个测试程序超过 10 秒判为超时，测试里不要写等待真实时间的循环。
- 测试里同样不能用 printf 等禁用符号。
