"""
KiCad 网表解析器 (.net)

解析 KiCad 导出的 S-expression 格式网表文件。

格式示例:
(export (version "E")
  (design ...)
  (components
    (comp (ref "U1")
      (value "STM32G431")
      (footprint "Package_QFP:LQFP-48")
    )
  )
  (nets
    (net (code "1") (name "GND")
      (node (ref "U1") (pin "7"))
      (node (ref "C1") (pin "2"))
    )
  )
)
"""

from pathlib import Path
from typing import Any

try:
    from .netlist_parser import NetlistParser, validate_parsed_netlist
except ImportError:
    from netlist_parser import NetlistParser, validate_parsed_netlist


class SExpressionError(ValueError):
    """Raised for malformed KiCad S-expression input."""


def tokenize_sexpression(content: str) -> list[str]:
    """Tokenize parentheses, atoms, and escaped quoted strings."""
    tokens: list[str] = []
    index = 0
    length = len(content)
    while index < length:
        char = content[index]
        if char.isspace():
            index += 1
            continue
        if char == ";":
            newline = content.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char in "()":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            value: list[str] = []
            while index < length:
                char = content[index]
                if char == '"':
                    index += 1
                    tokens.append("".join(value))
                    break
                if char == "\\":
                    index += 1
                    if index >= length:
                        raise SExpressionError("unterminated escape in quoted string")
                    escaped = content[index]
                    value.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
                    index += 1
                    continue
                value.append(char)
                index += 1
            else:
                raise SExpressionError("unterminated quoted string")
            continue
        end = index
        while end < length and not content[end].isspace() and content[end] not in "();":
            end += 1
        if end == index:
            raise SExpressionError(f"unexpected character at offset {index}")
        tokens.append(content[index:end])
        index = end
    return tokens


def parse_sexpression(content: str) -> list:
    """Parse one complete S-expression into nested Python lists."""
    tokens = tokenize_sexpression(content)
    stack: list[list] = []
    roots: list = []
    for token in tokens:
        if token == "(":
            expression: list = []
            if stack:
                stack[-1].append(expression)
            else:
                roots.append(expression)
            stack.append(expression)
        elif token == ")":
            if not stack:
                raise SExpressionError("unexpected closing parenthesis")
            stack.pop()
        else:
            if not stack:
                raise SExpressionError(f"atom outside expression: {token!r}")
            stack[-1].append(token)
    if stack:
        raise SExpressionError("unclosed parenthesis")
    if len(roots) != 1:
        raise SExpressionError(f"expected one root expression, found {len(roots)}")
    return roots[0]


def _children(expression: list, name: str) -> list[list]:
    return [item for item in expression[1:] if isinstance(item, list) and item and item[0] == name]


def _field(expression: list, name: str, default: str = "") -> str:
    matches = _children(expression, name)
    if not matches or len(matches[0]) < 2 or isinstance(matches[0][1], list):
        return default
    return str(matches[0][1])


class KiCadNetlistParser(NetlistParser):
    """KiCad 网表解析器"""
    
    def parse(self, file_path: Path) -> dict[str, Any]:
        """Parse a KiCad legacy netlist using its real nested grammar."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8-sig", errors="strict")
        try:
            root = parse_sexpression(content)
        except (UnicodeError, SExpressionError) as exc:
            raise ValueError(f"invalid KiCad S-expression in {path}: {exc}") from exc
        if not root or root[0] != "export":
            raise ValueError(f"invalid KiCad netlist in {path}: root expression is not export")

        component_sections = _children(root, "components")
        net_sections = _children(root, "nets")
        if len(component_sections) != 1 or len(net_sections) != 1:
            raise ValueError(f"invalid KiCad netlist in {path}: expected components and nets sections")

        components: dict[str, dict[str, Any]] = {}
        for expression in _children(component_sections[0], "comp"):
            ref = _field(expression, "ref")
            if not ref:
                raise ValueError(f"invalid KiCad component in {path}: missing ref")
            components[ref] = {
                "designator": ref,
                "device": _field(expression, "value"),
                "footprint": _field(expression, "footprint"),
                "pins": {},
            }

        nets: dict[str, list[dict[str, str]]] = {}
        for expression in _children(net_sections[0], "net"):
            name = _field(expression, "name")
            code = _field(expression, "code")
            if not name:
                name = f"Net-{code}" if code else ""
            if not name:
                raise ValueError(f"invalid KiCad net in {path}: missing name and code")
            nodes = nets.setdefault(name, [])
            for node in _children(expression, "node"):
                ref = _field(node, "ref")
                pin = _field(node, "pin")
                if not ref or not pin:
                    raise ValueError(f"invalid KiCad node on net {name!r}: missing ref or pin")
                pin_name = _field(node, "pinfunction", pin)
                nodes.append({"component": ref, "pin": pin, "pin_name": pin_name})
                if ref in components:
                    components[ref]["pins"][pin] = {"name": pin_name, "net": name}

        result = {
            "version": "KiCad",
            "file": str(path),
            "components": components,
            "nets": nets,
        }
        return validate_parsed_netlist(result, "KiCad")
    
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
        
        # 引脚用途推断规则
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
