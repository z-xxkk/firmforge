# 固定块内存池 mem_pool

实现 `include/mem_pool.h` 中声明的全部函数：把调用方提供的一块缓冲区切成大小相同的块，提供常数时间的分配和释放，用来代替嵌入式系统里禁用的 malloc / free。

## 约束

- 不能使用动态内存、标准输入输出函数，不能用递归。
- 头文件由任务锁定，不能修改。
- 分配和释放都必须是常数时间（不能遍历全部块）。

## 资料（摘自 Zephyr 内存块 memory slab 的文档与内核源码、SEI CERT C MEM31-C）

- 空闲块组成一个链表，**链表指针就存在空闲块自己的开头**，不额外占内存；所以块大小至少能放下一个指针。
- 块大小必须是对齐单位的整数倍，缓冲区也要按对齐单位对齐，这样每一块都是对齐的。
- 初始化时从最后一块往前串成链表，所以第一次分配拿到地址最小的块；释放的块放回链表头，下一次最先被分配（后进先出）。
- 释放时检查指针：必须在缓冲区内，并且正好落在某一块的起点。
- 同一块内存只能释放一次（MEM31-C）；重复释放会让同一块在空闲链表里出现两次，之后被分配给两个使用者。

## 函数行为

**对齐单位**是 `MEM_POOL_ALIGN`，即 `sizeof(void *)`（STM32 上 4 字节，电脑上 8 字节；代码不能写死数字）。

| 函数 | 正常行为 | 异常 / 边界 |
|---|---|---|
| `mem_pool_init(pool, buf, buf_size, block_size)` | 块数 = buf_size ÷ block_size（余下的边角不用）；所有块都空闲；历史最大值为 0；返回 true | pool 或 buf 为 NULL；block_size 小于一个指针或不是对齐单位的整数倍；buf 地址没有按对齐单位对齐；块数为 0 或超过 `MEM_POOL_MAX_BLOCKS`：返回 false |
| `mem_pool_alloc(pool)` | 返回一个空闲块：第一次按地址从小到大，之后最近释放的块最先返回；块内容不清零 | 没有空闲块或 pool 为 NULL：返回 NULL |
| `mem_pool_free(pool, ptr)` | 把这一块放回空闲链表头，返回 `MEM_POOL_OK` | 见下表 |
| `mem_pool_reset(pool)` | 所有块变回空闲（顺序与刚初始化时相同），**历史最大值保留** | pool 为 NULL 什么都不做 |
| `mem_pool_used` / `mem_pool_available` / `mem_pool_capacity` | 已用块数 / 空闲块数 / 总块数 | pool 为 NULL 返回 0 |
| `mem_pool_peak(pool)` | 历史上同时被使用的最大块数 | pool 为 NULL 返回 0 |

**释放时的检查**（按顺序；任何一种错误都**什么都不改**）

| 情况 | 返回 |
|---|---|
| ptr 为 NULL（或 pool 为 NULL） | `MEM_POOL_OK`，什么都不做（与 free(NULL) 一致） |
| ptr 不在 [缓冲区起点, 起点 + 块数 × 块大小) 范围内 | `MEM_POOL_ERR_FOREIGN` |
| 在范围内，但到起点的距离不是块大小的整数倍 | `MEM_POOL_ERR_MISALIGNED` |
| 这一块当前是空闲的 | `MEM_POOL_ERR_DOUBLE_FREE` |

（提示：常数时间地知道"某一块是否已分配"，可以用头文件里的 `in_use` 位图。）

## 例子

缓冲区 64 字节、块大小 16（在 4 字节对齐的 STM32 上）：4 块，地址依次为 buf、buf+16、buf+32、buf+48。

```
a = alloc() → buf        b = alloc() → buf+16
free(a)     → OK         c = alloc() → buf（刚释放的最先返回）
free(a)     → OK（c 和 a 是同一块）    free(a) → DOUBLE_FREE
free(buf+8) → MISALIGNED            free(buf+64) → FOREIGN
```
