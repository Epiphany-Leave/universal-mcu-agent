# Keil MDK 常见错误

## 编译错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `cannot open source input file "stm32g4xx.h"` | 芯片支持包未安装 | 安装对应的 DFP 包 |
| `No compatible library exists` | 启动文件不匹配 | 选择正确的启动文件 |
| `cannot open file: xxx.lib` | 库文件路径错误 | 配置库文件搜索路径 |

## 链接错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `undefined reference to 'xxx'` | 函数未添加到工程 | 添加包含该函数的 .c 文件 |
| `multiple definition of 'xxx'` | 变量重复定义 | 使用 extern 声明 |

## 配置检查

- Project -> Options for Target -> C/C++ -> Include Paths
- Project -> Options for Target -> Linker -> Misc Controls
- 确认芯片型号与启动文件匹配
