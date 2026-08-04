# Knowledge 知识库

## 目录结构

```
knowledge/
├── common/                    # 通用知识（所有 MCU 共用）
│   ├── architecture.md        # 程序架构规范
│   ├── coding-style.md        # 编码规范
│   ├── code-rules.md          # 代码生成规则
│   ├── debugging-methods.md   # 通用调试方法
│   ├── engineering-rules.md   # 通用工程规则
│   ├── common-gotchas.md      # 通用常见错误
│   └── timepiece-framework.md # 时间片轮询框架
│
├── platform/                  # 芯片平台知识
│   ├── stm32/                 # STM32 专用
│   ├── esp32/                 # ESP32 专用
│   └── ch592/                 # CH592/CH591 专用
│
├── application/               # 应用领域知识（待扩展）
│   └── (motor-control, power-electronics, battery...)
│
├── project/                   # 项目经验（当前项目）
│
├── schema.md                  # 知识格式规范
├── project-knowledge-guide.md # 知识提升指南
├── CHANGELOG.md               # 版本变更记录
└── VERSION                    # 版本号
```

## 加载优先级

```
project > application > platform > common
```

## 知识格式

所有新增知识文件必须包含 Front Matter（参考 `schema.md`）。

## 注册

所有知识文件需注册到 `core/registry/knowledge.yaml`。
