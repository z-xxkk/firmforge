/* 弱符号的 setUp / tearDown：测试文件没有定义时使用这里的空实现 */
#include "unity.h"

__attribute__((weak)) void setUp(void) {}
__attribute__((weak)) void tearDown(void) {}
