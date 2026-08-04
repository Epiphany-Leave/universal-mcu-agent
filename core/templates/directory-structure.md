# v0.6.0 标准产品目录结构

```text
<product-root>/
├── .mcu-agent/
│   ├── config.yaml
│   ├── artifact-manifest.yaml
│   └── runtime/
├── hardware/
│   ├── schematic/
│   ├── pcb/
│   ├── datasheets/
│   └── bom/
├── software/
│   └── <project-name>/
│       ├── CMakeLists.txt
│       ├── main/
│       ├── components/
│       │   └── <component-name>/
│       ├── docs/
│       └── debug/
├── project/
│   └── requirements/
├── artifacts/
├── references/
├── releases/
└── archive/
```

## 不变量

- Firmware application root 必须是 `software/<project-name>`。
- 禁止创建 `<product-root>/firmware` 和 `<product-root>/drivers`。
- ESP-IDF component 默认必须位于 `software/<project-name>/components/<component-name>`；其它位置必须由平台 contract 显式注册。
- 产品根与 firmware project root 是两个不同概念。
- 旧 `02_Software/Projects` 仅作兼容读取；升级不得静默移动现有工程。
