# 字节环形缓冲区 ringbuf

实现 `include/ringbuf.h` 中声明的全部函数。这是一个先进先出（FIFO）的字节队列，典型用途是 UART 接收缓冲区。

## 约束

- 不能使用动态内存（malloc/free 等）和 printf 系列函数。存储区由调用方提供。
- 容量 `capacity` 可以是任意正整数，**不要求是 2 的幂**。容量为 N 时最多能存 N 个字节。
- `ringbuf_t` 的字段由头文件给出，实现只能使用这些字段，头文件不能修改。

## 函数行为

| 函数 | 正常行为 | 异常/边界 |
|---|---|---|
| `ringbuf_init(rb, storage, capacity)` | 绑定存储区，缓冲区为空，返回 true | rb 为 NULL、storage 为 NULL 或 capacity 为 0：返回 false |
| `ringbuf_put(rb, byte)` | 写入一个字节，返回 true | rb 为 NULL 或缓冲区已满：返回 false，**不覆盖旧数据** |
| `ringbuf_get(rb, out)` | 取出最早写入的字节到 `*out`，返回 true | rb 为 NULL、out 为 NULL 或缓冲区为空：返回 false，缓冲区内容不变 |
| `ringbuf_count(rb)` | 已存字节数 | rb 为 NULL：返回 0 |
| `ringbuf_free(rb)` | 剩余空间（capacity − count） | rb 为 NULL：返回 0 |
| `ringbuf_is_empty(rb)` | count 为 0 时为 true | rb 为 NULL：返回 true |
| `ringbuf_is_full(rb)` | count 等于 capacity 时为 true | rb 为 NULL：返回 false |
| `ringbuf_write(rb, data, len)` | 按顺序写入 min(len, 剩余空间) 个字节，返回实际写入数 | rb 或 data 为 NULL：返回 0 |
| `ringbuf_read(rb, out, len)` | 按顺序读出 min(len, 已存字节数) 个字节，返回实际读出数 | rb 或 out 为 NULL：返回 0 |
| `ringbuf_clear(rb)` | 清空缓冲区（容量和存储区不变） | rb 为 NULL：什么都不做 |

读写指针到达存储区末尾时要回绕到开头，任何时候都不能访问 `storage[0..capacity-1]` 以外的内存。
