# 编程习惯和代码风格

## 头文件规范

### 头文件保护
```c
#ifndef __XXX_H__
#define __XXX_H__
// 头文件内容
#endif /* __XXX_H__ */
```

### 头文件包含顺序
1. 标准库头文件（`stdint.h`、`stdio.h`）
2. 平台头文件（`main.h`）
3. 依赖模块头文件

### 注释风格
```c
/**
 * @brief  函数简要说明
 * @param  参数说明
 * @retval 返回值说明
 */
```

## 函数规范

- 驱动层：`模块_动作()`，如 `adc_init()`
- 固件层：`模块_bsp_动作()`，如 `adc_bsp_init()`
- 应用层：`功能_动作()`，如 `KeyEventCallBack()`
- 函数不超过 50 行，复杂逻辑拆分

## 变量规范

- 全局变量：`g_` 前缀或直接命名
- 局部变量：小写字母，下划线分隔
- 宏定义：大写字母，下划线分隔
- 使用 `stdint.h` 标准类型

## 代码结构

```c
// 1. 文件头注释
// 2. 头文件包含
// 3. 宏定义
// 4. 类型定义
// 5. 全局变量
// 6. 静态函数声明
// 7. 公共函数实现
// 8. 静态函数实现
```

## 错误处理

```c
int xxx_init(void) {
    if (step1() != 0) return -1;
    if (step2() != 0) return -2;
    return 0;
}
```

## 调试宏

```c
#ifdef DEBUG
    #define DBG_PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define DBG_PRINTF(fmt, ...)
#endif
```
