# Knowledge Schema

知识文件格式规范。

## Front Matter 定义

所有新增知识文件**必须**包含 Front Matter：

```yaml
---
name: uart-debugging          # 唯一标识 (kebab-case)
description: UART 串口调试     # 简短描述

category:                      # 知识类别
  - communication
  - debugging

platform:                      # 适用平台 (空=通用)
  - stm32
  - esp32

keywords:                      # 搜索关键词
  - uart
  - serial
  - baudrate

related_workflows:             # 关联 workflow
  - firmware
  - build

source:                        # 知识来源
  - datasheet
  - reference-manual
  - project-experience

confidence: verified           # 可信度: verified / draft / experimental
version: "1.0.0"               # 知识版本
---
```

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| name | ✅ | 唯一标识，kebab-case |
| description | ✅ | 一句话描述 |
| category | ✅ | 类别列表 |
| platform | ❌ | 适用平台，空=通用 |
| keywords | ✅ | 搜索关键词 |
| related_workflows | ❌ | 关联 workflow |
| source | ❌ | 知识来源 |
| confidence | ❌ | 可信度，默认 draft |
| version | ❌ | 版本号 |

## Category 定义

| Category | 说明 |
|----------|------|
| gpio | GPIO 相关 |
| communication | 通信接口 (UART/SPI/I2C/CAN) |
| timer | 定时器/PWM |
| adc | ADC/DAC |
| dma | DMA |
| interrupt | 中断 |
| memory | 内存管理 |
| power | 电源管理 |
| debugging | 调试方法 |
| architecture | 程序架构 |
| coding | 编码规范 |
| engineering | 工程管理 |
| motor-control | 电机控制 |
| power-electronics | 电源电子 |
| battery | BMS 电池管理 |
| rf | 射频通信 |
| display | 显示驱动 |
| audio | 音频处理 |
| camera | 摄像头 |

## Confidence 定义

| 等级 | 说明 |
|------|------|
| verified | 经过验证，可直接使用 |
| draft | 草稿，需要验证 |
| experimental | 实验性，可能有误 |

## 工程证据字段

生命周期 artifact 和生成的硬件数据还必须记录：

- `source`: 稳定 artifact ID/path，以及可用的 revision/SHA-256/page。
- `evidence_class`: `HARDWARE_FACT`, `DEVICE_DOCUMENTATION`, `OFFICIAL_BOARD_PROFILE`, `OFFICIAL_BOARD_VALIDATION`, `IMPLEMENTATION_REFERENCE`, `GENERATED_CONFIG`, `UNTRUSTED_OR_MODIFIED`。
- `confidence`: `confirmed`, `unverified`, `unknown`, `conflict`。

知识库 `confidence: verified` 不等于某个实际项目的硬件事实已确认。Requirements 和 ADR 表达意图/决策；不能建立物理能力或关闭 physical evidence debt。

## 旧文件兼容

无 Front Matter 的旧知识文件仍可加载，但：
- 从文件路径推断 platform
- category 默认为文件名关键词
- confidence 默认为 draft
