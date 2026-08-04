# 通用调试方法

## 常见编译错误

### 头文件路径配置错误
```
fatal error: xxx.h: No such file or directory
```
解决：在 IDE 或构建系统中添加头文件搜索路径。

### 外部变量未声明
```
'xxx' undeclared (first use in this function)
```
解决：添加 `extern type_t variable_name;`

### 函数未声明
```
warning: implicit declaration of function 'xxx'
```
解决：在 .h 文件中添加函数声明。

### 重复定义
```
multiple definition of 'xxx'
```
解决：使用 `extern` 声明，只在一个文件中定义。

### 未定义的引用
```
undefined reference to 'xxx'
```
解决：确保 .c 文件已添加到工程。

## 调试方法

### 串口调试
- 使用 `printf()` 或自定义调试宏
- 关键步骤添加调试输出

### 断点调试
- 在初始化完成后设置断点
- 检查变量值是否符合预期

### LED 指示
- 用 LED 闪烁频率表示不同状态
- 适用于无串口的场景

## 代码组织规则

### 头文件包含顺序
```c
// 1. 平台/系统头文件
// 2. HAL 层
// 3. 驱动层
// 4. 固件层
// 5. 中间件
// 6. 应用层
```

### 错误处理模式
```c
int xxx_init(void) {
    if (step1() != 0) return -1;
    if (step2() != 0) return -2;
    return 0;
}
```
