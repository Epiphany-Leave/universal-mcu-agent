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
    """
    检测网表文件格式
    
    Args:
        file_path: 网表文件路径
        
    Returns:
        格式标识: 'enet', 'altium', 'kicad', 'eagle', 'unknown'
    """
    suffix = file_path.suffix.lower()
    
    if suffix == '.enet':
        return 'enet'
    elif suffix == '.net':
        # 检查内容判断是 Altium 还是 KiCad
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')[:2000]
            if '(export' in content or '(components' in content:
                return 'kicad'
            else:
                return 'altium'
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot inspect netlist format for {file_path}: {exc}") from exc
    elif suffix == '.xml':
        return 'eagle'
    elif suffix == '.sch':
        # Eagle .sch 是 XML 格式
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')[:500]
            if '<?xml' in content or '<eagle' in content:
                return 'eagle'
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot inspect schematic format for {file_path}: {exc}") from exc
        return 'unknown'
    else:
        return 'unknown'


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
