# CH592/CH591 工程创建

## 参考例程复制

**适用**: CH592、CH591、CH32V、CH32F 系列

**工程结构**:
```
xxx/
├── .cproject               # MRS 工程配置（必须在根目录）
├── .project
├── .settings/
├── APP/                    # 应用层
├── BSP/                    # 固件层
├── driver/                 # 驱动层
├── Middlewares/            # 中间层
├── TimePiece/              # 时间片轮询
├── HAL/                    # HAL 库
├── LIB/                    # BLE 库
├── Ld/                     # 链接脚本
├── Startup/                # 启动文件
└── StdPeriphDriver/        # 标准外设驱动
```

**重要**: .cproject 必须在根目录，否则 MRS 导入会失败。

**创建步骤**:
1. 创建工程根目录
2. 从参考例程复制 .cproject/.project/.settings
3. 复制 APP/BSP/driver 目录
4. 复制 HAL/LIB/StdPeriphDriver
5. 包含 CONFIG.h（不是 CH59x_common.h）
