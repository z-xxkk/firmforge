# 判定器编码规则要点

自己整理的摘要：FirmForge 判定器逐层检查的规则，以及最常见的失败原因。

## 主机编译 -Werror 常见失败

- 用了 `NULL`、`size_t` 却没有 `#include <stddef.h>`；用了 `bool` 却没有 `<stdbool.h>`；用了 `uint8_t` 却没有 `<stdint.h>`。每个 .c 和 .h 都要自己包含用到的标准头文件，不要指望别的头文件顺带包含。
- 未使用的参数会报 unused-parameter：在函数开头写 `(void)param;`。
- 有符号与无符号比较（sign-compare）：循环变量和长度都用 `size_t`，或显式转换。
- `-pedantic` 下不允许空的翻译单元（.c 文件里至少要有一个声明）；函数无参数要写 `(void)`。
- 只在本文件使用的函数写成 `static`，否则多个模块里同名的辅助函数会在链接时重复定义。

## 禁用符号与动态内存

- 不能用 malloc / calloc / realloc / free（MISRA C:2012 Rule 21.3），存储区由调用方提供或用静态数组。
- 不能用 `<stdio.h>` 的任何输入输出函数（Rule 21.6），包括 printf、sprintf、snprintf、puts。
- 不能用 abort、exit、getenv、system（Rule 21.8），setjmp / longjmp（Rule 21.4），signal / raise（Rule 21.5）。
- 不能用变长数组（VLA）和 alloca：数组长度必须是编译期常量。
- 判定器通过检查目标文件里引用的外部符号来发现这些调用，写在测试文件里同样会被查到。

## 资源预算

- Flash = text + data，RAM = data + bss，按 arm-none-eabi-gcc -Os 统计。
- 查找表要写成 `static const`，才会放进 Flash；没有 const 的全局数组会占 RAM。
- 栈按调用链计算最坏值：不要在函数里放大的局部数组；禁止递归（MISRA Rule 17.2），判定器会用不优化的编译结果检查调用图里有没有环。

## 静态检查（cppcheck）

- 变量先初始化再使用；数组下标先检查范围再访问。
- 指针参数先判 NULL 再解引用；不要在判 NULL 之前就使用它。
- 移位次数不能大于等于类型位数；对有符号负数右移的结果依赖实现，用无符号类型做位运算。
