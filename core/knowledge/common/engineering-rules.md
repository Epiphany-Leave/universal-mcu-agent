# 通用工程创建规则

## 工程创建方式

| MCU | 创建方式 |
|-----|----------|
| STM32 | CubeMX 生成 |
| ESP32 | ESP-IDF 生成 |
| TMS320F | CCS 工程 |
| CH592/CH591/CH32 | 参考例程复制 |
| GD32/PY32/STM8/AVR | 手动创建 |

## 通用原则

1. 工程目录内部结构不拆分
2. 不修改自动生成的代码
3. 自定义代码放在指定区域
4. 头文件包含顺序：平台 → HAL → 驱动 → 固件 → 中间件 → 应用

## 代码区域标记

### CubeMX 项目
```c
/* USER CODE BEGIN Includes */
#include "app_xxx.h"
/* USER CODE END Includes */
```

### ESP-IDF 项目
```c
// 自定义代码直接添加到 main.c 或单独的 .c 文件
```

## 不要修改的文件

| IDE | 不要修改的文件 |
|-----|----------------|
| CubeMX | `MX_xxx_Init()`、`stm32fxxx_it.c` |
| ESP-IDF | `CMakeLists.txt` 自动生成的部分 |
| CCS | `.cmd` 文件中的链接配置 |
