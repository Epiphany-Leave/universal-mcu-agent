# ESP32 工程创建

## ESP-IDF 生成

**适用**: ESP32、ESP32-S2、ESP32-S3、ESP32-C3、ESP32-S31

**工程结构**:
```
xxx/
├── CMakeLists.txt          # 顶层 CMake
├── sdkconfig               # ESP-IDF 配置
├── main/
│   ├── CMakeLists.txt
│   └── main.c
├── components/             # 自定义组件
└── build/
```

**创建步骤**:
```bash
idf.py create-project xxx
idf.py menuconfig
```

**预览芯片** (ESP32-S31):
```bash
idf.py --preview set-target esp32s31
idf.py --preview build
```

**中文路径问题**:
```powershell
$env:PYTHONUTF8 = 1
```
