# MCU 项目程序架构规范

## 架构分层

适用于 STM32、CH592、ESP32 等不同 MCU。

### 1. 驱动层（driver）

**职责**：直接操作硬件寄存器，提供底层硬件访问接口。

**文件命名**：`xxx_driver.c` / `xxx_driver.h`

**示例**：
- `adc_driver.c/h` - ADC 驱动
- `lcd_driver.c/h` - LCD 驱动
- `key_driver.c/h` - 按键驱动
- `motor_driver.c/h` - 电机驱动
- `pwm_driver.c/h` - PWM 驱动

**特点**：
- 直接操作寄存器
- 提供初始化、读写、控制等基本函数
- 不包含业务逻辑

### 2. 固件层（BSP）

**职责**：基于驱动层，提供更高层次的硬件抽象。

**文件命名**：`xxx_bsp.c` / `xxx_bsp.h`

**示例**：
- `adc_bsp.c/h` - ADC 固件（滤波、校准）
- `lcd_bsp.c/h` - LCD 固件（显示函数）
- `key_bsp.c/h` - 按键固件（按键状态管理）

**特点**：
- 调用驱动层函数
- 提供更易用的接口
- 包含简单的数据处理

### 3. 应用层（APP）

**职责**：实现具体的应用功能。

**文件命名**：`app.c` / `app.h` 或 `xxx_app.c` / `xxx_app.h`

**特点**：
- 调用 BSP 层函数
- 实现业务逻辑
- 处理用户交互

### 4. 中间层（Middlewares）

**职责**：提供第三方库和通用组件。

**子目录**：LVGL/、MQTT/、cJSON/、WIFI/、HMI/

### 5. 时间片轮询（TimePiece）

**职责**：提供任务调度框架。

**文件命名**：`timepiece.c/h`、`TaskList.c/h`

---

## 目录结构模板

### STM32 项目（CubeMX 生成）

```
xxx/
├── *.ioc
├── Core/Inc/ + Core/Src/
├── Drivers/ (HAL 库)
├── MDK-ARM/
│   ├── driver/        # 驱动层
│   ├── BSP/           # 固件层
│   ├── APP/           # 应用层
│   ├── Middlewares/   # 中间层
│   └── TimePiece/     # 时间片轮询
└── obj/
```

### ESP32 项目（ESP-IDF）

```
xxx/
├── CMakeLists.txt
├── sdkconfig
├── main/
│   ├── CMakeLists.txt
│   ├── main.c
│   └── app.c/h
├── components/
│   ├── driver/        # 驱动层
│   ├── bsp/           # 固件层
│   └── middlewares/   # 中间层
└── build/
```

### CH592/CH591 项目（MRS）

```
xxx/
├── .cproject          # 必须在根目录
├── .project
├── APP/               # 应用层
├── BSP/               # 固件层
├── driver/            # 驱动层
├── Middlewares/       # 中间层
├── TimePiece/         # 时间片轮询
├── HAL/ + LIB/        # SDK 库
└── StdPeriphDriver/
```

---

## 命名规范

| 类型 | 文件命名 | 函数命名 |
|------|----------|----------|
| 驱动层 | `xxx_driver.c/h` | `xxx_init()`, `xxx_read()`, `xxx_write()` |
| 固件层 | `xxx_bsp.c/h` | `xxx_bsp_init()`, `xxx_bsp_get()` |
| 应用层 | `app.c/h` | `App_TaskName()`, `Event_CallBack()` |

### 头文件保护
```c
#ifndef __XXX_H__
#define __XXX_H__
// ...
#endif /* __XXX_H__ */
```
