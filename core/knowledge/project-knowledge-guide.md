# Project Knowledge 提升指南

## 知识层级

```
knowledge/
├── common/          # 通用知识（所有项目共享）
├── stm32/           # STM32 族知识
├── esp32/           # ESP32 族知识
├── ch592/           # CH592 族知识
├── protocols/       # 通信协议知识
└── project/         # 项目经验（仅当前项目）
```

## 规则

### 1. 项目经验默认只属于当前项目

`knowledge/project/` 中的内容：
- 由当前项目运行时产生
- 仅对当前项目有效
- **禁止自动进入 common 或 MCU 族目录**

### 2. 提升需要审核

项目经验要进入公共知识库，必须经过审核：

```
knowledge/project/xxx.md
    ↓ 发现通用价值
生成 knowledge-review.md
    ↓ 人工审核
knowledge/common/ 或 knowledge/<family>/
```

### 3. 审核标准

| 总分 | 目标目录 | 条件 |
|------|----------|------|
| >= 15 | common/ | 通用问题，所有项目可能遇到 |
| >= 10 | <family>/ | 特定 MCU 族问题 |
| < 10 | project/ | 保留项目特有 |

### 4. Workflow 触发

当以下 workflow 发现具有通用价值的经验时，会生成 `knowledge-review.md`：
- **build** — 编译调试中发现的通用错误
- **firmware** — 固件开发中发现的通用模式
- **code-qa** — 代码分析中发现的通用问题

**Workflow 不会自动修改 common/ 或 <family>/ 目录。**

### 5. 知识来源标识

每条项目经验必须包含：

```markdown
## 来源
- **项目**: <项目名称>
- **日期**: <YYYY-MM-DD>
- **MCU**: <芯片型号>
- **工具链**: <IDE/编译器>
```

## 审核流程

1. 在 `knowledge/project/` 中记录经验
2. Workflow 或用户发现通用价值
3. 生成 `knowledge-review.md`（使用审核模板）
4. 人工审核评分
5. 根据评分决定目标目录
6. 手动复制到目标目录
7. 删除 `knowledge-review.md`
