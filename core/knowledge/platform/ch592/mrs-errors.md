# MounRiver Studio 常见错误

## 工程结构错误

### 导入工程只导入了 src 子目录
- **原因**: .cproject 和 .project 文件放在了 src 子目录中
- **正确结构**: .cproject 必须在工程根目录

```
xxx/
├── .cproject          # 必须在根目录
├── .project           # 必须在根目录
├── .settings/         # 必须在根目录
├── APP/
├── BSP/
└── driver/
```

## 头文件错误

### BLE_MEMHEAP_SIZE undeclared
- **原因**: 包含了 CH59x_common.h 而不是 CONFIG.h
- **解决**: main.c 必须包含 `CONFIG.h`

### VER_LIB undeclared
- **原因**: 头文件包含错误
- **解决**: 包含 CONFIG.h

## 路径配置

### fatal error: xxx.h: No such file or directory
- **解决**: 在 .cproject 的 include paths 中添加所有自定义目录
