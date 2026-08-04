# CH592/CH591 常见错误

## MRS 工程结构

1. **.cproject 必须在根目录** → 否则 MRS 导入只导入 src 子目录
2. **包含 CONFIG.h** → 不是 CH59x_common.h
3. **BLE_MEMHEAP_SIZE undeclared** → 包含 CONFIG.h

## 头文件

4. **.cproject include paths 配置** → 添加所有自定义目录
5. **VER_LIB undeclared** → 包含 CONFIG.h
