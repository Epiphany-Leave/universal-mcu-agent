# 代码生成规则

## 核心原则

1. 遵循架构规范（driver/BSP/APP/TimePiece）
2. 遵循编码风格
3. 不修改自动生成的代码
4. 代码必须有据（hardware.json、datasheet.json）

## 代码路径规则

| MCU 类型 | 代码路径 |
|----------|----------|
| STM32 (CubeMX) | `Core/Inc/` 和 `Core/Src/` |
| ESP32 (ESP-IDF) | `main/` |
| TMS320F (CCS) | `include/` 和 `source/` |
| CH592/CH591 (MRS) | `APP/` 和 `APP/include/` |
| GD32/PY32 (Keil) | `User/` |

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

## 头文件包含顺序

```c
// 1. 平台/系统头文件
// 2. HAL 层
// 3. 驱动层
// 4. 固件层
// 5. 中间件
// 6. 应用层
```
