"""
Altium Designer 网表解析器 (.NET)

解析 Altium Designer 导出的 Protel 格式网表文件。
OrCAD 使用相同的格式。

格式示例:
[
  (
    U1
    LQFP-48
    STM32G431CBU6
    PA0
    PA1
    ...
  )
]
"""

import re
from pathlib import Path
from typing import Any

try:
    from .netlist_parser import NetlistParser, validate_parsed_netlist
except ImportError:
    from netlist_parser import NetlistParser, validate_parsed_netlist


class AltiumNetlistParser(NetlistParser):
    """Altium Designer 网表解析器"""
    
    def parse(self, file_path: Path) -> dict[str, Any]:
        """
        解析 Altium 网表文件
        
        Args:
            file_path: .NET 文件路径
            
        Returns:
            标准化格式的网表数据
        """
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        
        result = {
            "version": "Altium",
            "file": str(file_path),
            "components": {},
            "nets": {},
        }
        
        # 解析元件块: ( designator footprint value pin1 pin2 ... )
        component_pattern = r'\(\s*([A-Z]+\d+)\s+(\S+)\s+(\S+)\s+(.*?)\)'
        for match in re.finditer(component_pattern, content, re.DOTALL):
            designator = match.group(1)
            footprint = match.group(2)
            value = match.group(3)
            pins_str = match.group(4)
            
            # 解析引脚
            pins = {}
            pin_names = re.findall(r'(\S+)', pins_str)
            for i, pin_name in enumerate(pin_names, 1):
                pins[str(i)] = {
                    "name": pin_name,
                    "net": "",  # 需要从 nets 部分填充
                }
            
            result["components"][designator] = {
                "designator": designator,
                "device": value,
                "footprint": footprint,
                "pins": pins,
            }
        
        # 解析网络块: ( net_name node1 node2 ... )
        # 节点格式: component-pin
        net_pattern = r'\(\s*(\S+)\s+(.*?)\)'
        for match in re.finditer(net_pattern, content, re.DOTALL):
            net_name = match.group(1)
            nodes_str = match.group(2)
            
            # 跳过元件块（第一个词是 designator）
            if re.match(r'^[A-Z]+\d+$', net_name):
                continue
            
            nodes = []
            node_matches = re.findall(r'([A-Z]+\d+)-(\d+)', nodes_str)
            for comp, pin in node_matches:
                nodes.append({
                    "component": comp,
                    "pin": pin,
                    "pin_name": "",
                })
                
                # 更新元件的引脚网络
                if comp in result["components"]:
                    if pin in result["components"][comp]["pins"]:
                        result["components"][comp]["pins"][pin]["net"] = net_name
            
            result["nets"][net_name] = nodes
        
        return validate_parsed_netlist(result, "Altium")
    
    def find_mcu(self, data: dict) -> dict | None:
        """
        查找 MCU 元件
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            MCU 元件信息
        """
        mcu_keywords = [
            'STM32', 'GD32', 'CH32', 'ESP32', 'ESP8266', 'ESP-12',
            'ATMEGA', 'ATTINY', 'PIC', 'MSP430', 'NRF52',
            'MCU', 'CPU', 'PROCESSOR',
        ]
        
        for designator, comp in data.get("components", {}).items():
            device = comp.get("device", "").upper()
            value = comp.get("value", "").upper() if comp.get("value") else ""
            
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
    
    def extract_interfaces(self, data: dict) -> list[dict[str, Any]]:
        """
        提取接口信息
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            接口列表
        """
        interfaces = []
        
        mcu = self.find_mcu(data)
        if not mcu:
            return interfaces
        
        # 收集所有网络连接的元器件
        net_components = {}
        for comp_id, comp in data.get("components", {}).items():
            for pin_id, pin_info in comp.get("pins", {}).items():
                net = pin_info.get("net", "")
                if net:
                    if net not in net_components:
                        net_components[net] = []
                    net_components[net].append({
                        "designator": comp.get("designator", ""),
                        "device": comp.get("device", ""),
                    })
        
        # 引脚用途推断规则（同 enet_parser.py）
        pin_usage_rules = self._get_pin_usage_rules()
        
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
                usage = self._infer_usage_from_components(net, net_components, mcu, pin_name)
            
            # 确定引脚方向
            direction = self._infer_direction(pin_name, net)
            
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
    
    def _get_pin_usage_rules(self) -> dict:
        """获取引脚用途推断规则"""
        return {
            'DLP1': ('舵机1', 'PWM'),
            'DLP2': ('舵机2', 'PWM'),
            'DLP3': ('舵机3', 'PWM'),
            'DLP4': ('舵机4', 'PWM'),
            'DLP5': ('舵机5', 'PWM'),
            'DLP6': ('舵机6', 'PWM'),
            'DLP7': ('舵机7', 'PWM'),
            'DLP8': ('舵机8', 'PWM'),
            'SERVO': ('舵机', 'PWM'),
            'M_DIR1': ('步进电机1方向', 'GPIO'),
            'M_EN1': ('步进电机1使能', 'GPIO'),
            'M_STP1': ('步进电机1步进', 'GPIO'),
            'M_DIR2': ('步进电机2方向', 'GPIO'),
            'M_EN2': ('步进电机2使能', 'GPIO'),
            'M_STP2': ('步进电机2步进', 'GPIO'),
            'AIN1': ('电机A方向1', 'GPIO'),
            'AIN2': ('电机A方向2', 'GPIO'),
            'BIN1': ('电机B方向1', 'GPIO'),
            'BIN2': ('电机B方向2', 'GPIO'),
            'PWMA': ('电机A速度', 'PWM'),
            'PWMB': ('电机B速度', 'PWM'),
            'HOT': ('加热控制', 'GPIO'),
            'HEAT': ('加热控制', 'GPIO'),
            'TEMP': ('温度采集', 'ADC'),
            'TEMP_ADC': ('温度采集', 'ADC'),
            'WATER1': ('水泵1', 'GPIO'),
            'WATER2': ('水泵2', 'GPIO'),
            'PUMP': ('水泵', 'GPIO'),
            'DEBUG_TX': ('调试串口发送', 'UART'),
            'DEBUG_RX': ('调试串口接收', 'UART'),
            'ESP_TX': ('WiFi模块发送', 'UART'),
            'ESP_RX': ('WiFi模块接收', 'UART'),
            'LCD_TX': ('串口屏发送', 'UART'),
            'LCD_RX': ('串口屏接收', 'UART'),
            'BEEP': ('蜂鸣器', 'PWM'),
            'BUZZER': ('蜂鸣器', 'PWM'),
            'LED': ('LED指示灯', 'GPIO'),
        }
    
    def _infer_usage_from_components(self, net: str, net_components: dict, mcu: dict, pin_name: str) -> str:
        """从连接的元器件推断用途"""
        components = net_components.get(net, [])
        other_comps = [c for c in components if c['designator'] != mcu.get('designator', '')]
        
        if not other_comps:
            return ''
        
        comp_devices = [c['device'].upper() for c in other_comps]
        
        if any('TB6612' in d or 'L298' in d or 'L293' in d for d in comp_devices):
            if 'TIM' in pin_name.upper():
                return '电机速度控制'
            else:
                return '电机方向控制'
        elif any('MOS' in d or 'FET' in d for d in comp_devices):
            return 'MOS管驱动'
        elif any('ESP' in d or 'WIFI' in d for d in comp_devices):
            if 'TX' in net.upper():
                return 'WiFi发送'
            elif 'RX' in net.upper():
                return 'WiFi接收'
        elif any('LCD' in d or 'TFT' in d for d in comp_devices):
            if 'TX' in net.upper():
                return '串口屏发送'
            elif 'RX' in net.upper():
                return '串口屏接收'
        elif any('BUZZER' in d or 'BEEP' in d for d in comp_devices):
            return '蜂鸣器'
        elif any('MOTOR' in d or 'STEP' in d for d in comp_devices):
            return '步进电机控制'
        
        return ''
    
    def _infer_direction(self, pin_name: str, net: str) -> str:
        """推断引脚方向"""
        if 'RX' in net.upper() or 'RX' in pin_name.upper():
            return 'INPUT'
        elif 'ADC' in pin_name.upper() or 'TEMP' in net.upper():
            return 'INPUT'
        return 'OUTPUT'
