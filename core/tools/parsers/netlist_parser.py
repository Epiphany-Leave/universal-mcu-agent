"""
通用网表解析器接口

支持多种 EDA 工具的网表格式：
- 嘉立创EDA (.enet) - JSON 格式
- Altium Designer (.NET) - Protel 格式
- KiCad (.net) - S-expression 格式
- Cadence Allegro (.xml/.txt)
- Eagle (.sch) - XML 格式
- OrCAD (.NET) - 同 Altium 格式
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class NetlistParser(ABC):
    """网表解析器基类"""
    
    @abstractmethod
    def parse(self, file_path: Path) -> dict[str, Any]:
        """
        解析网表文件，返回标准化格式
        
        Args:
            file_path: 网表文件路径
            
        Returns:
            标准化格式:
            {
                "version": "1.0",
                "components": {
                    "U1": {
                        "designator": "U1",
                        "device": "STM32G431CBU6",
                        "footprint": "LQFP-48",
                        "pins": {
                            "1": {"name": "PA0", "net": "DLP1"},
                            "2": {"name": "PA1", "net": "DLP2"},
                            ...
                        }
                    }
                },
                "nets": {
                    "DLP1": [
                        {"component": "U1", "pin": "1", "pin_name": "PA0"},
                        {"component": "CN1", "pin": "1", "pin_name": "1"},
                    ]
                }
            }
        """
        pass
    
    @abstractmethod
    def find_mcu(self, data: dict) -> dict | None:
        """
        查找 MCU 元件
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            MCU 元件信息，包含引脚连接
        """
        pass
    
    @abstractmethod
    def extract_interfaces(self, data: dict) -> list[dict[str, Any]]:
        """
        提取接口信息
        
        Args:
            data: parse() 返回的数据
            
        Returns:
            接口列表，每个接口包含引脚、网络、类型、用途等信息
        """
        pass


def detect_netlist_format(file_path: Path) -> str:
    """Detect an EDA format from both the extension and a content signature.

    A bare extension is not enough for ambiguous ``.net`` and ``.xml`` files.
    Detection failures are errors with diagnostics rather than an ``unknown``
    pseudo-format that can later look like a successful empty parse.
    """
    import json
    import re
    import xml.etree.ElementTree as ET

    path = Path(file_path)
    suffix = path.suffix.lower()
    try:
        content = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise ValueError(f"cannot read EDA file {path}: {exc}") from exc
    sample = content[:65536]
    lowered = sample.lower()

    # ENET is JSON. Require its structural marker even when the suffix agrees.
    if suffix in {".enet", ".json"} or sample.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            if suffix == ".enet":
                raise ValueError(f"invalid ENET JSON in {path}: {exc}") from exc
        else:
            if isinstance(payload, dict) and isinstance(payload.get("components"), (dict, list)):
                return "enet"
            if suffix == ".enet":
                raise ValueError(f"invalid ENET structure in {path}: missing components object")

    # KiCad legacy netlists are S-expressions with export/components/nets.
    if "(export" in lowered and "(components" in lowered and "(nets" in lowered:
        return "kicad"

    # XML can be either Eagle or Cadence; inspect root/tags, not just .xml.
    if suffix in {".xml", ".sch"} or sample.lstrip().startswith("<"):
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            if suffix in {".xml", ".sch"}:
                raise ValueError(f"invalid XML EDA file {path}: {exc}") from exc
        else:
            root_name = root.tag.rsplit("}", 1)[-1].lower()
            tags = {element.tag.rsplit("}", 1)[-1].lower() for element in root.iter()}
            if root_name == "eagle" or {"drawing", "schematic", "parts"} <= tags:
                return "eagle"
            if root_name in {"cadence", "allegro", "cadencenetlist", "netlist"} and {"component", "net"} <= tags:
                return "cadence"
            if suffix in {".xml", ".sch"}:
                raise ValueError(
                    f"unrecognized XML EDA structure in {path}: root=<{root_name}>, "
                    "expected Eagle or Cadence component/net tags"
                )

    # Protel/OrCAD/Altium and Cadence text exports use parenthesized blocks.
    has_component_block = re.search(r"\(\s*[A-Za-z]+\d+\s+\S+\s+\S+", sample) is not None
    has_node = re.search(r"\b[A-Za-z]+\d+-\d+\b", sample) is not None
    if suffix == ".txt" and has_component_block and has_node:
        return "cadence"
    if suffix == ".net" and has_component_block and has_node:
        return "altium"

    expected = ".enet JSON, KiCad .net S-expression, Eagle XML, Cadence XML/.txt, or Altium .NET"
    raise ValueError(
        f"unable to detect EDA format for {path} (extension {suffix or '<none>'}); expected {expected}"
    )


def validate_parsed_netlist(data: dict[str, Any], format_type: str) -> dict[str, Any]:
    """Reject malformed or connectivity-free parser output with a clear reason."""
    components = data.get("components")
    nets = data.get("nets")
    if not isinstance(components, dict) or not components:
        raise ValueError(f"{format_type} parser found no components")
    if not isinstance(nets, dict) or not nets:
        raise ValueError(f"{format_type} parser found no nets")
    connected = sum(len(nodes) for nodes in nets.values() if isinstance(nodes, list))
    if connected == 0:
        raise ValueError(f"{format_type} parser found nets but no connected nodes")
    return data


def _import_parser(module_name: str, class_name: str):
    """动态导入解析器，支持仓库和部署两种目录结构。"""
    import importlib
    # 先尝试从 parsers/ 子目录导入（部署模式）
    try:
        mod = importlib.import_module(f"parsers.{module_name}")
        return getattr(mod, class_name)
    except (ImportError, ModuleNotFoundError):
        pass
    # 回退到 src.parsers 导入（仓库模式）
    try:
        mod = importlib.import_module(f"src.parsers.{module_name}")
        return getattr(mod, class_name)
    except (ImportError, ModuleNotFoundError):
        pass
    # 直接导入（同目录模式）
    mod = importlib.import_module(module_name)
    return getattr(mod, class_name)


def get_parser(format_type: str) -> NetlistParser | None:
    """
    获取对应格式的解析器

    Args:
        format_type: 格式标识

    Returns:
        解析器实例，如果格式不支持则返回 None
    """
    _map = {
        'enet': ('enet_parser', 'EnetParser'),
        'altium': ('altium_parser', 'AltiumNetlistParser'),
        'kicad': ('kicad_parser', 'KiCadNetlistParser'),
        'eagle': ('eagle_parser', 'EagleNetlistParser'),
        'cadence': ('cadence_parser', 'CadenceNetlistParser'),
        'orcad': ('altium_parser', 'AltiumNetlistParser'),
    }
    if format_type in _map:
        cls = _import_parser(*_map[format_type])
        return cls()
    return None


def get_supported_formats() -> list[dict[str, str]]:
    """获取支持的网表格式列表"""
    return [
        {
            "name": "嘉立创EDA",
            "format": "enet",
            "extension": ".enet",
            "type": "JSON",
            "status": "✅ 已实现",
        },
        {
            "name": "Altium Designer",
            "format": "altium",
            "extension": ".NET",
            "type": "Protel",
            "status": "✅ 已实现",
        },
        {
            "name": "KiCad",
            "format": "kicad",
            "extension": ".net",
            "type": "S-expression",
            "status": "✅ 已实现",
        },
        {
            "name": "Eagle",
            "format": "eagle",
            "extension": ".sch/.xml",
            "type": "XML",
            "status": "✅ 已实现",
        },
        {
            "name": "Cadence Allegro",
            "format": "cadence",
            "extension": ".xml/.txt",
            "type": "XML/文本",
            "status": "✅ 已实现",
        },
        {
            "name": "OrCAD",
            "format": "orcad",
            "extension": ".NET",
            "type": "Protel",
            "status": "✅ 已实现",
        },
    ]
