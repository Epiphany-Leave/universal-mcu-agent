# STM32 工程创建

## CubeMX 生成

**适用**: STM32F/G/H/L/W/U 系列

**工程结构**:
```
xxx/
├── *.ioc                  # CubeMX 配置
├── *.uvprojx              # Keil 工程
├── Core/
│   ├── Inc/               # 头文件
│   └── Src/               # 源文件
├── Drivers/
│   ├── CMSIS/
│   └── STM32Fxxx_HAL_Driver/
└── MDK-ARM/
    ├── driver/            # 驱动层
    ├── BSP/               # 固件层
    ├── APP/               # 应用层
    └── Middlewares/       # 中间层
```

**创建步骤**:
1. 使用 CubeMX 配置外设
2. 生成 Keil 工程
3. 在 Core/Inc 和 Core/Src 中添加自定义代码
4. 在 USER CODE 区域编写代码
