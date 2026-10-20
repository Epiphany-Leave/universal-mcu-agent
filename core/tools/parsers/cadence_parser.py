"""
Cadence Allegro 网表解析器 (.xml/.txt)

解析 Cadence Allegro 导出的网表文件。

Cadence Allegro 支持多种导出格式：
1. XML 格式 - 标准 XML 结构
2. 文本格式 - 类似 Altium 的 Protel 格式
3. Allegro 专有格式 - 需要专用工具

参考项目:
- OpenAllegroParser: https://github.com/Werni2A/OpenAllegroParser
- PCBpy: https://github.com/mvsoliveira/PCBpy
- OrCAD Netlist: https://github.com/jadedeng/orcad-netlist
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    from .netlist_parser import NetlistParser, validate_parsed_netlist
except ImportError:
    from netlist_parser import NetlistParser, validate_parsed_netlist


class CadenceNetlistParser(NetlistParser):
    """Cadence Allegro 网表解析器"""
    
    def parse(self, file_path: Path) -> dict[str, Any]:
        """
        解析 Cadence 网表文件
        
        Args:
            file_path: .xml 或 .txt 文件路径
            
        Returns:
            标准化格式的网表数据
        """
        suffix = file_path.suffix.lower()
        
        if suffix == '.xml':
            return self._parse_xml(file_path)
        else:
            return self._parse_text(file_path)
    
    def _parse_xml(self, file_path: Path) -> dict[str, Any]:
        """解析 XML 格式网表"""
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        result = {
            "version": "Cadence",
            "file": str(file_path),
            "components": {},
            "nets": {},
        }
        
        # 解析元件
        for comp in root.findall('.//component'):
            ref = comp.get('ref', '')
            value = comp.get('value', '')
            footprint = comp.get('footprint', '')
            
            pins = {}
            for pin in comp.findall('.//pin'):
                pin_name = pin.get('name', '')
                net = pin.get('net', '')
                pins[pin_name] = {
                    "name": pin_name,
                    "net": net,
                }
            
            result["components"][ref] = {
                "designator": ref,
                "device": value,
                "footprint": footprint,
                "pins": pins,
            }
        
        # 解析网络
        for net in root.findall('.//net'):
            net_name = net.get('name', '')
            nodes = []
            
            for node in net.findall('.//node'):
                ref = node.get('ref', '')
                pin = node.get('pin', '')
                nodes.append({
                    "component": ref,
                    "pin": pin,
                    "pin_name": pin,
                })
                
                # 更新元件引脚
                if ref in result["components"]:
                    if pin in result["components"][ref]["pins"]:
                        result["components"][ref]["pins"][pin]["net"] = net_name
            
            result["nets"][net_name] = nodes
        
        return validate_parsed_netlist(result, "Cadence XML")
    
    def _parse_text(self, file_path: Path) -> dict[str, Any]:
        """解析文本格式网表（类似 Protel 格式）"""
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        
        result = {
            "version": "Cadence",
            "file": str(file_path),
            "components": {},
            "nets": {},
        }
        
        # 解析元件块
        component_pattern = r'\(\s*([A-Z]+\d+)\s+(\S+)\s+(\S+)\s+(.*?)\)'
        for match in re.finditer(component_pattern, content, re.DOTALL):
            designator = match.group(1)
            footprint = match.group(2)
            value = match.group(3)
            pins_str = match.group(4)
            
            pins = {}
            pin_names = re.findall(r'(\S+)', pins_str)
            for i, pin_name in enumerate(pin_names, 1):
                pins[str(i)] = {
                    "name": pin_name,
                    "net": "",
                }
            
            result["components"][designator] = {
                "designator": designator,
                "device": value,
                "footprint": footprint,
                "pins": pins,
            }
        
        # 解析网络块
        net_pattern = r'\(\s*(\S+)\s+(.*?)\)'
        for match in re.finditer(net_pattern, content, re.DOTALL):
            net_name = match.group(1)
            nodes_str = match.group(2)
            
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
                
                if comp in result["components"]:
                    if pin in result["components"][comp]["pins"]:
                        result["components"][comp]["pins"][pin]["net"] = net_name
            
            result["nets"][net_name] = nodes
        
        return validate_parsed_netlist(result, "Cadence text")
    
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
            
            if any(k in device or k in value for k in mcu_keywords):
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
        
        pin_usage_rules = self._get_pin_usage_rules()
        
        for pin_id, pin_info in mcu.get("pin_connections", {}).items():
            pin_name = pin_info.get("pin_name", "")
            net = pin_info.get("net", "")
            
            if net in ['+3.3V', '+5V', '+12V', 'GND', 'VCC', 'VDD', 'VBAT', 'VREF+', 'VDDA']:
                continue
            
            if net in ['SWDIO', 'SWCLK', 'NRST', 'BOOT0']:
                continue
            
            pin_type = 'GPIO'
            usage = ''
            
            for prefix, (usage_desc, ptype) in pin_usage_rules.items():
                if net.startswith(prefix) or net == prefix:
                    pin_type = ptype
                    usage = usage_desc
                    break
            
            if not usage:
                usage = self._infer_usage_from_components(net, net_components, mcu, pin_name)
            
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
        return {
            'DLP1': ('舵机1', 'PWM'), 'DLP2': ('舵机2', 'PWM'),
            'DLP3': ('舵机3', 'PWM'), 'DLP4': ('舵机4', 'PWM'),
            'DLP5': ('舵机5', 'PWM'), 'DLP6': ('舵机6', 'PWM'),
            'DLP7': ('舵机7', 'PWM'), 'DLP8': ('舵机8', 'PWM'),
            'SERVO': ('舵机', 'PWM'),
            'M_DIR1': ('步进电机1方向', 'GPIO'), 'M_EN1': ('步进电机1使能', 'GPIO'),
            'M_STP1': ('步进电机1步进', 'GPIO'), 'M_DIR2': ('步进电机2方向', 'GPIO'),
            'M_EN2': ('步进电机2使能', 'GPIO'), 'M_STP2': ('步进电机2步进', 'GPIO'),
            'AIN1': ('电机A方向1', 'GPIO'), 'AIN2': ('电机A方向2', 'GPIO'),
            'BIN1': ('电机B方向1', 'GPIO'), 'BIN2': ('电机B方向2', 'GPIO'),
            'PWMA': ('电机A速度', 'PWM'), 'PWMB': ('电机B速度', 'PWM'),
            'HOT': ('加热控制', 'GPIO'), 'HEAT': ('加热控制', 'GPIO'),
            'TEMP': ('温度采集', 'ADC'), 'TEMP_ADC': ('温度采集', 'ADC'),
            'WATER1': ('水泵1', 'GPIO'), 'WATER2': ('水泵2', 'GPIO'),
            'DEBUG_TX': ('调试串口发送', 'UART'), 'DEBUG_RX': ('调试串口接收', 'UART'),
            'ESP_TX': ('WiFi模块发送', 'UART'), 'ESP_RX': ('WiFi模块接收', 'UART'),
            'LCD_TX': ('串口屏发送', 'UART'), 'LCD_RX': ('串口屏接收', 'UART'),
            'BEEP': ('蜂鸣器', 'PWM'), 'BUZZER': ('蜂鸣器', 'PWM'),
        }
    
    def _infer_usage_from_components(self, net, net_components, mcu, pin_name):
        components = net_components.get(net, [])
        other_comps = [c for c in components if c['designator'] != mcu.get('designator', '')]
        if not other_comps:
            return ''
        comp_devices = [c['device'].upper() for c in other_comps]
        if any('TB6612' in d or 'L298' in d for d in comp_devices):
            return '电机速度控制' if 'TIM' in pin_name.upper() else '电机方向控制'
        elif any('MOS' in d or 'FET' in d for d in comp_devices):
            return 'MOS管驱动'
        return ''
    
    def _infer_direction(self, pin_name, net):
        if 'RX' in net.upper() or 'RX' in pin_name.upper():
            return 'INPUT'
        elif 'ADC' in pin_name.upper() or 'TEMP' in net.upper():
            return 'INPUT'
        return 'OUTPUT'
