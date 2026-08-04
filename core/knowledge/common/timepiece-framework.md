# 时间片轮询框架（TimePiece）

## 何时使用

| 场景 | 推荐方案 |
|------|----------|
| 资源有限的 MCU（CH592、GD32、PY32） | 时间片轮询 |
| 简单任务调度 | 时间片轮询 |
| ESP32 双核架构 | FreeRTOS |
| 需要复杂同步 | FreeRTOS |
| 需要抢占式调度 | FreeRTOS |

## 核心文件

- `timepiece.c/h` - 核心实现
- `TaskList.c/h` - 任务列表管理

## 数据结构

```c
typedef struct {
    uint8_t taskID;
    uint8_t taskShieldFlag : 1;
    uint8_t taskHangFlag : 1;
    uint16_t taskTimerLoad;
    int taskTimerDelay;
    uint16_t taskTimePiece;
    void (*taskHandler)(void);
    volatile uint16_t taskTimer;
} task_t;
```

## API

```c
void Task_Init(task_t* regList, uint8_t listLength, uint16_t itPeriod);
void TaskDicision(void);                    // 主循环中调用
void Task_Shield(uint8_t taskID);           // 屏蔽任务
void Task_DeShield(uint8_t taskID);         // 取消屏蔽
void Task_Delay(uint8_t taskID, int ms);    // 延时执行
```

## 使用示例

```c
task_t taskList[] = {
    INIT_TASK(0, Task0_Handler, 10),   // 10ms
    INIT_TASK(1, Task1_Handler, 50),   // 50ms
    INIT_TASK(2, Task2_Handler, 100),  // 100ms
};

int main(void) {
    HAL_Init();
    SystemClock_Config();
    Task_Init(taskList, TaskNum(taskList), 1);
    while(1) {
        TaskDicision();
    }
}
```

## 宏定义

```c
#define TaskNum(__taskArray) (sizeof(__taskArray) / sizeof(__taskArray[0]))
#define INIT_TASK(_taskID, _taskHandler, _taskTimePiece) \
    { _taskID, 0, 0, 0, 0, _taskTimePiece, _taskHandler, 0 }
```

## 移植

1. 配置一个定时器，产生固定周期中断
2. 在中断中调用时间片中断处理函数
3. 包含 `timepiece.h` 和 `TaskList.h`
