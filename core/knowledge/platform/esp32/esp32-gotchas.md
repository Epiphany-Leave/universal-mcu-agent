# ESP32 常见错误

## ESP-IDF 相关

1. **中文路径** → 设置 `PYTHONUTF8=1`
2. **GPIO 冲突** → I2C 和 I2S 共用 GPIO 0 时延迟初始化
3. **预览芯片** → 使用 `idf.py --preview set-target`
4. **PPA 加速** → 缺少 esp_async_color_convert 时禁用
5. **组件版本锁定** → 在 idf_component.yml 中指定版本
6. **WiFi 子系统重置** → 连接失败后 stop/deinit/init 重启
