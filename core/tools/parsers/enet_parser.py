"""
嘉立创EDA网表文件解析器 (.enet)

解析 .enet 格式的网表文件，提取元件和引脚连接信息。
"""

import json
from pathlib import Path
from typing import Any

try:
    from .netlist_parser import NetlistParser
except ImportError:
    from netlist_parser import NetlistParser


class EnetParser(NetlistParser):
    """嘉立创EDA网表解析器"""
    
    def parse(self, file_path: Path) -> dict[str, Any]:
        """
        解析 .enet 网表文件
        
        Args:
            file_path: .enet 文件路径
            
        Returns:
            标准化格式的网表数据
        """
        return parse_enet_file(file_path)
    
    def find_mcu(self, data: dict) -> dict | None:
        """
        查找 MCU 元件
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            MCU 元件信息
        """
        return find_mcu_in_enet(data)
    
    def extract_interfaces(self, data: dict) -> list[dict[str, Any]]:
        """
        提取接口信息
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            接口列表
        """
        return extract_mcu_interfaces(data)


def parse_enet_file(file_path: Path) -> dict[str, Any]:
    """
    解析 .enet 网表文件
    
    Args:
        file_path: .enet 文件路径
        
    Returns:
        解析后的数据，包含元件和引脚信息
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    result = {
        "version": data.get("version", ""),
        "file": str(file_path),
        "components": {},
        "mcu_pins": {},
        "nets": {},
    }
    
    # 解析元件
    for comp_id, comp in data.get("components", {}).items():
        props = comp.get("props", {})
        designator = props.get("Designator", "")
        device_name = props.get("DeviceName", "")
        value = props.get("Value", "")
        footprint = props.get("FootprintName", "")
        
        # 解析引脚
        pins = {}
        for pin_id, pin_info in comp.get("pinInfoMap", {}).items():
            pin_name = pin_info.get("name", "")
            net = pin_info.get("net", "")
            pins[pin_id] = {
                "name": pin_name,
                "net": net,
            }
            
            # 收集网络信息
            if net and not net.startswith("$"):
                if net not in result["nets"]:
                    result["nets"][net] = []
                result["nets"][net].append({
                    "component": designator,
                    "pin": pin_id,
                    "pin_name": pin_name,
                })
        
        result["components"][designator] = {
            "id": comp_id,
            "device": device_name,
            "value": value,
            "footprint": footprint,
            "pins": pins,
        }
    
    return result


def find_mcu_in_enet(enet_data: dict) -> dict[str, Any] | None:
    """
    从网表数据中查找 MCU 元件
    
    Args:
        enet_data: parse_enet_file 返回的数据
        
    Returns:
        MCU 元件信息，包含引脚连接
    """
    mcu_keywords = ['STM32', 'GD32', 'CH32', 'ESP32', 'ESP8266', 'MCU', 'CPU']
    
    for designator, comp in enet_data.get("components", {}).items():
        device = comp.get("device", "").upper()
        value = comp.get("value", "").upper()
        
        # 检查是否是 MCU
        if any(k in device or k in value for k in mcu_keywords):
            # 提取引脚连接
            pin_connections = {}
            for pin_id, pin_info in comp.get("pins", {}).items():
                net = pin_info.get("net", "")
                if net:
                    pin_connections[pin_id] = {
                        "pin_name": pin_info.get("name", ""),
                        "net": net,
                    }
            
            return {
                "designator": designator,
                "device": comp.get("device", ""),
                "value": comp.get("value", ""),
                "footprint": comp.get("footprint", ""),
                "pin_connections": pin_connections,
            }
    
    return None


def extract_mcu_interfaces(enet_data: dict) -> list[dict[str, Any]]:
    """
    从网表数据中提取 MCU 接口信息，并推断引脚用途
    
    Args:
        enet_data: parse_enet_file 返回的数据
        
    Returns:
        接口列表，每个接口包含引脚、网络、类型、用途等信息
    """
    interfaces = []
    
    mcu = find_mcu_in_enet(enet_data)
    if not mcu:
        return interfaces
    
    # 收集所有网络连接的元器件
    net_components = {}
    for comp_id, comp in enet_data.get("components", {}).items():
        props = comp.get("props", {})
        designator = props.get("Designator", "")
        device = props.get("DeviceName", "")
        
        for pin_id, pin_info in comp.get("pinInfoMap", {}).items():
            net = pin_info.get("net", "")
            if net:
                if net not in net_components:
                    net_components[net] = []
                net_components[net].append({
                    "designator": designator,
                    "device": device,
                })
    
    # 引脚用途推断规则
    pin_usage_rules = {
        # 舵机相关（带编号）
        'DLP1': ('舵机1', 'PWM'),
        'DLP2': ('舵机2', 'PWM'),
        'DLP3': ('舵机3', 'PWM'),
        'DLP4': ('舵机4', 'PWM'),
        'DLP5': ('舵机5', 'PWM'),
        'DLP6': ('舵机6', 'PWM'),
        'DLP7': ('舵机7', 'PWM'),
        'DLP8': ('舵机8', 'PWM'),
        'SERVO': ('舵机', 'PWM'),
        
        # 步进电机相关（带编号）
        'M_DIR1': ('步进电机1方向', 'GPIO'),
        'M_EN1': ('步进电机1使能', 'GPIO'),
        'M_STP1': ('步进电机1步进', 'GPIO'),
        'M_DIR2': ('步进电机2方向', 'GPIO'),
        'M_EN2': ('步进电机2使能', 'GPIO'),
        'M_STP2': ('步进电机2步进', 'GPIO'),
        
        # 直流电机相关（带通道）
        'AIN1': ('电机A方向1', 'GPIO'),
        'AIN2': ('电机A方向2', 'GPIO'),
        'BIN1': ('电机B方向1', 'GPIO'),
        'BIN2': ('电机B方向2', 'GPIO'),
        'PWMA': ('电机A速度', 'PWM'),
        'PWMB': ('电机B速度', 'PWM'),
        
        # 加热/温度
        'HOT': ('加热控制', 'GPIO'),
        'HEAT': ('加热控制', 'GPIO'),
        'TEMP': ('温度采集', 'ADC'),
        'TEMP_ADC': ('温度采集', 'ADC'),
        
        # 水泵（带编号）
        'WATER1': ('水泵1', 'GPIO'),
        'WATER2': ('水泵2', 'GPIO'),
        'PUMP': ('水泵', 'GPIO'),
        
        # 串口
        'DEBUG_TX': ('调试串口发送', 'UART'),
        'DEBUG_RX': ('调试串口接收', 'UART'),
        'ESP_TX': ('WiFi模块发送', 'UART'),
        'ESP_RX': ('WiFi模块接收', 'UART'),
        'LCD_TX': ('串口屏发送', 'UART'),
        'LCD_RX': ('串口屏接收', 'UART'),
        
        # 蜂鸣器
        'BEEP': ('蜂鸣器', 'PWM'),
        'BUZZER': ('蜂鸣器', 'PWM'),
        
        # LED
        'LED': ('LED指示灯', 'GPIO'),
    }
    
    for pin_id, pin_info in mcu.get("pin_connections", {}).items():
        pin_name = pin_info.get("pin_name", "")
        net = pin_info.get("net", "")
        
        # 跳过电源和地
        if net in ['+3.3V', '+5V', '+12V', 'GND', 'VCC', 'VDD', 'VBAT', 'VREF+', 'VDDA']:
            continue
        
        # 跳过调试接口
        if net in ['SWDIO', 'SWCLK', 'NRST', 'BOOT0']:
            continue
        
        # 确定引脚类型和用途
        pin_type = 'GPIO'
        usage = ''
        
        # 从网络名称推断用途
        for prefix, (usage_desc, ptype) in pin_usage_rules.items():
            if net.startswith(prefix) or net == prefix:
                pin_type = ptype
                usage = usage_desc
                break
        
        # 如果没有匹配规则，从连接的元器件推断
        if not usage:
            components = net_components.get(net, [])
            other_comps = [c for c in components if c['designator'] != mcu.get('designator', '')]
            
            if other_comps:
                # 分析连接的元器件类型
                comp_devices = [c['device'].upper() for c in other_comps]
                
                if any('TB6612' in d or 'L298' in d or 'L293' in d for d in comp_devices):
                    if 'PWM' in pin_name.upper() or 'TIM' in pin_name.upper():
                        usage = '电机速度控制'
                        pin_type = 'PWM'
                    else:
                        usage = '电机方向控制'
                        pin_type = 'GPIO'
                elif any('MOS' in d or 'FET' in d for d in comp_devices):
                    usage = 'MOS管驱动'
                    pin_type = 'GPIO'
                elif any('ESP' in d or 'WIFI' in d for d in comp_devices):
                    if 'TX' in net.upper():
                        usage = 'WiFi发送'
                        pin_type = 'UART'
                    elif 'RX' in net.upper():
                        usage = 'WiFi接收'
                        pin_type = 'UART'
                elif any('LCD' in d or 'TFT' in d or 'SCREEN' in d for d in comp_devices):
                    if 'TX' in net.upper():
                        usage = '串口屏发送'
                        pin_type = 'UART'
                    elif 'RX' in net.upper():
                        usage = '串口屏接收'
                        pin_type = 'UART'
                elif any('BUZZER' in d or 'BEEP' in d for d in comp_devices):
                    usage = '蜂鸣器'
                    pin_type = 'PWM'
                elif any('MOTOR' in d or 'STEP' in d for d in comp_devices):
                    usage = '步进电机控制'
                    pin_type = 'GPIO'
        
        # 如果还是没有用途，从信号名推断
        if not usage:
            if 'TIM' in pin_name.upper() and 'CH' in pin_name.upper():
                usage = '定时器输出'
                pin_type = 'PWM'
            elif 'ADC' in pin_name.upper():
                usage = '模拟输入'
                pin_type = 'ADC'
            elif 'USART' in pin_name.upper() or 'UART' in pin_name.upper():
                if 'TX' in pin_name.upper():
                    usage = '串口发送'
                elif 'RX' in pin_name.upper():
                    usage = '串口接收'
                pin_type = 'UART'
        
        # 确定引脚方向
        direction = 'OUTPUT'
        if 'RX' in net.upper() or 'RX' in pin_name.upper():
            direction = 'INPUT'
        elif 'ADC' in pin_name.upper() or 'TEMP' in net.upper():
            direction = 'INPUT'
        
        interfaces.append({
            "pin": pin_name,
            "pin_id": pin_id,
            "net": net,
            "type": pin_type,
            "direction": direction,
            "component": mcu.get("designator", ""),
            "usage": usage,
        })
    
    return interfaces


def generate_interface_summary(interfaces: list[dict]) -> dict[str, list]:
    """
    生成接口摘要，按类型分组
    
    Args:
        interfaces: extract_mcu_interfaces 返回的接口列表
        
    Returns:
        按类型分组的接口摘要
    """
    summary = {}
    
    for iface in interfaces:
        iface_type = iface.get("type", "GPIO")
        if iface_type not in summary:
            summary[iface_type] = []
        summary[iface_type].append(iface)
    
    return summary


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python enet_parser.py <file.enet>")
        sys.exit(1)
    
    enet_file = Path(sys.argv[1])
    if not enet_file.exists():
        print(f"文件不存在: {enet_file}")
        sys.exit(1)
    
    print(f"解析网表文件: {enet_file}")
    data = parse_enet_file(enet_file)
    
    print(f"\n版本: {data['version']}")
    print(f"元件数量: {len(data['components'])}")
    print(f"网络数量: {len(data['nets'])}")
    
    print("\n=== 查找 MCU ===")
    mcu = find_mcu_in_enet(data)
    if mcu:
        print(f"MCU: {mcu['designator']} ({mcu['device']})")
        print(f"封装: {mcu['footprint']}")
        print(f"\n引脚连接 ({len(mcu['pin_connections'])} 个):")
        for pin_id, pin_info in sorted(mcu['pin_connections'].items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            print(f"  Pin {pin_id:2s} ({pin_info['pin_name']:20s}) -> {pin_info['net']}")
    
    print("\n=== 接口列表 ===")
    interfaces = extract_mcu_interfaces(data)
    summary = generate_interface_summary(interfaces)
    
    for iface_type, ifaces in sorted(summary.items()):
        print(f"\n{iface_type}:")
        for iface in ifaces:
            print(f"  {iface['pin']:6s} -> {iface['net']}")
