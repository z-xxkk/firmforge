# 判定器样例工作目录

都针对 `tasks/ringbuf`。用法：`firmforge verify tests/fixtures/<样例> --task tasks/ringbuf`

| 样例 | 预期首个失败层 | 说明 |
|---|---|---|
| good | 无（本机缺工具时为 incomplete） | 正确实现 + 正确测试 |
| syntax_error | host_compile | `ringbuf_put` 里少一个分号 |
| syntax_error_moved | host_compile | 同一个错误，挪到了另一行；签名应与 syntax_error 相同 |
| uses_malloc | static_check | `ringbuf_init` 调用了 malloc |
| test_failure | unit_tests | `ringbuf_free` 少算一个字节，测试失败 |
| infinite_loop | unit_tests | `ringbuf_read` 数据不足时死循环，超时 |
| over_budget | resources | 多了一张 2 KB 的常量表，超出 flash 预算（需要 arm-none-eabi 工具链） |
| recursion | resources | `ringbuf_count` 用递归实现，违反 MISRA C:2012 Rule 17.2（需要 arm-none-eabi 工具链） |
