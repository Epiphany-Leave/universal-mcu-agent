"""
CubeMX .ioc 文件解析器.

从 .ioc 文件中提取引脚配置、外设信息、时钟配置。
"""

import re
from pathlib import Path
from typing import Any


def parse_ioc_file(ioc_path: str | Path) -> dict[str, Any]:
    """
    解析 CubeMX .ioc 文件，返回结构化的引脚和外设信息.

    Args:
        ioc_path: .ioc 文件路径

    Returns:
        {
            "mcu": "STM32G431CBU6",
            "package": "UFQFPN48",
            "pins": {
                "PA0": {"signal": "TIM2_CH1", "label": "DLP1", "mode": ""},
                "PA4": {"signal": "ADC2_IN17", "label": "TEMP_ADC", "mode": "IN17-Single-Ended"},
                ...
            },
            "peripherals": ["ADC2", "TIM2", "TIM3", "USART1", ...],
            "clock_config": {...},
        }
    """
    path = Path(ioc_path)
    if not path.exists():
        raise FileNotFoundError(f".ioc 文件不存在: {ioc_path}")

    content = path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()

    result = {
        "mcu": "",
        "family": "",
        "package": "",
        "pins": {},
        "peripherals": [],
        "gpio_labels": {},
        "signals": {},
    }

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # MCU 信息
        if key == "Mcu.CPN":
            result["mcu"] = value
        elif key == "Mcu.Family":
            result["family"] = value
        elif key == "Mcu.Package":
            result["package"] = value
        elif key == "Mcu.Name":
            if not result["mcu"]:
                result["mcu"] = value

        # 外设列表
        elif key.startswith("Mcu.IP") and key != "Mcu.IPNb":
            if value:
                result["peripherals"].append(value)

        # 引脚信号（如 PA0.Signal=TIM2_CH1）
        elif re.match(r"^P[A-F]\d+\.Signal$", key):
            pin = key.split(".")[0]
            if pin not in result["pins"]:
                result["pins"][pin] = {"signal": "", "label": "", "mode": "", "locked": False}
            result["pins"][pin]["signal"] = value

        # 引脚标签（如 PA0.GPIO_Label=DLP1）
        elif re.match(r"^P[A-F]\d+\.GPIO_Label$", key):
            pin = key.split(".")[0]
            if pin not in result["pins"]:
                result["pins"][pin] = {"signal": "", "label": "", "mode": "", "locked": False}
            result["pins"][pin]["label"] = value
            result["gpio_labels"][value] = pin

        # 引脚模式（如 PA10.Mode=Asynchronous）
        elif re.match(r"^P[A-F]\d+\.Mode$", key):
            pin = key.split(".")[0]
            if pin not in result["pins"]:
                result["pins"][pin] = {"signal": "", "label": "", "mode": "", "locked": False}
            result["pins"][pin]["mode"] = value

        # 引脚锁定状态
        elif re.match(r"^P[A-F]\d+\.Locked$", key):
            pin = key.split(".")[0]
            if pin not in result["pins"]:
                result["pins"][pin] = {"signal": "", "label": "", "mode": "", "locked": False}
            result["pins"][pin]["locked"] = value.lower() == "true"

    return result


def format_pin_table(ioc_data: dict) -> str:
    """格式化引脚表为 Markdown."""
    lines = [
        f"# CubeMX 引脚配置",
        f"",
        f"MCU: {ioc_data['mcu']}",
        f"封装: {ioc_data['package']}",
        f"外设: {', '.join(ioc_data['peripherals'])}",
        "",
        "## 引脚分配",
        "",
        "| 引脚 | 信号 | 标签 | 模式 |",
        "|------|------|------|------|",
    ]

    for pin in sorted(ioc_data["pins"].keys()):
        info = ioc_data["pins"][pin]
        signal = info["signal"] or "-"
        label = info["label"] or "-"
        mode = info["mode"] or "-"
        lines.append(f"| {pin} | {signal} | {label} | {mode} |")

    return "\n".join(lines)


def ioc_to_hardware_interfaces(ioc_data: dict) -> dict:
    """将 .ioc 数据转换为硬件接口表格式."""
    interfaces = []

    for pin, info in ioc_data["pins"].items():
        signal = info["signal"]
        label = info["label"]

        if not signal:
            continue

        # 跳过 SWD 调试口
        if "JTAG" in signal or "SWD" in signal or "SWCLK" in signal or "SWDIO" in signal:
            continue

        iface = {
            "name": label or signal,
            "pin": pin,
            "signal": signal,
            "needs_verification": False,
        }

        # 判断接口类型
        if "GPIO_Output" in signal:
            iface["type"] = "GPIO_OUTPUT"
            iface["active_level"] = "UNKNOWN"
            iface["default_state"] = "UNKNOWN"
            iface["needs_verification"] = True
        elif "GPIO_Input" in signal:
            iface["type"] = "GPIO_INPUT"
            iface["active_level"] = "UNKNOWN"
            iface["needs_verification"] = True
        elif "TIM" in signal and "CH" in signal:
            iface["type"] = "PWM"
            iface["frequency"] = "UNKNOWN"
            iface["needs_verification"] = True
        elif "ADC" in signal:
            iface["type"] = "ADC"
            iface["resolution"] = "UNKNOWN"
            iface["needs_verification"] = True
        elif "USART" in signal or "UART" in signal:
            iface["type"] = "UART"
            iface["baudrate"] = "UNKNOWN"
            iface["needs_verification"] = True
        elif "SPI" in signal:
            iface["type"] = "SPI"
        elif "I2C" in signal:
            iface["type"] = "I2C"
        else:
            iface["type"] = "OTHER"

        interfaces.append(iface)

    return {
        "mcu": ioc_data["mcu"],
        "package": ioc_data["package"],
        "interfaces": interfaces,
        "gpio_labels": ioc_data["gpio_labels"],
    }


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("用法: python -m src.parsers.ioc_parser <file.ioc>")
        sys.exit(1)

    data = parse_ioc_file(sys.argv[1])
    print(format_pin_table(data))
    print()
    print("=== 接口表 ===")
    interfaces = ioc_to_hardware_interfaces(data)
    print(json.dumps(interfaces, ensure_ascii=False, indent=2))
