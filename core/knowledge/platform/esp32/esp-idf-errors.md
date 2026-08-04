# ESP-IDF 常见错误

## 工程结构错误

### does not contain a CMakeLists.txt
- **原因**: 工程目录结构不正确
- **解决**: 使用 `idf.py create-project` 创建工程

### component xxx not found
- **原因**: 组件未注册
- **解决**: 在 CMakeLists.txt 中正确注册组件

## 目标芯片错误

### IDF_TARGET 不一致
```
Project sdkconfig was generated for target 'esp32s31', but environment variable IDF_TARGET is set to 'esp32'
```
- **解决**: `idf.py --preview set-target esp32s31`
- **注意**: ESP32-S31 是预览芯片，需要 `--preview` 参数

### esp_async_color_convert.h not found
- **原因**: esp_lvgl_adapter 版本过高
- **解决**: 降级到 0.5.1 或禁用 PPA 加速

## 编码错误

### UnicodeDecodeError: 'gbk' codec
- **原因**: 项目路径包含中文字符
- **解决**: 设置环境变量 `PYTHONUTF8=1`

## GPIO 冲突

### GPIO 0 is not usable
- **原因**: I2C 和 I2S 共用 GPIO 0
- **解决**: 延迟初始化音频硬件，避免与触控冲突

## 依赖管理

### idf_component.yml 版本锁定
```yaml
dependencies:
  espressif/esp_lvgl_adapter:
    version: "0.5.1"  # 锁定版本避免兼容问题
```
