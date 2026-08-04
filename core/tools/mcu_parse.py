#!/usr/bin/env python3
"""
MCU 解析工具 — 供 Claude Code 调用的命令行接口。

用法:
  python3 mcu_parse.py ioc <file.ioc>           # 解析 .ioc 文件
  python3 mcu_parse.py netlist <file>            # 解析网表文件
  python3 mcu_parse.py docs <project_path>       # 收集项目文档
  python3 mcu_parse.py pdf <file.pdf> [keywords] # 提取 PDF 文本
  python3 mcu_parse.py chips <project_path>      # 识别芯片
"""

import sys
import json
import io
from pathlib import Path

from project_config import candidate_paths

# Windows 控制台 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 解析器在 parsers/ 子目录下
_script_dir = Path(__file__).parent
sys.path.insert(0, str(_script_dir))


def _import_ioc():
    from parsers.ioc_parser import parse_ioc_file, ioc_to_hardware_interfaces
    return parse_ioc_file, ioc_to_hardware_interfaces


def _import_netlist():
    from parsers.netlist_parser import detect_netlist_format, get_parser
    return detect_netlist_format, get_parser


def cmd_ioc(file_path: str):
    """解析 .ioc 文件，输出结构化引脚信息。"""
    parse_ioc_file, ioc_to_hardware_interfaces = _import_ioc()
    d = parse_ioc_file(file_path)
    result = ioc_to_hardware_interfaces(d)
    result["raw_pins"] = d.get("pins", {})
    result["peripherals"] = d.get("peripherals", [])
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_netlist(file_path: str):
    """解析网表文件，输出接口信息。"""
    detect_netlist_format, get_parser = _import_netlist()
    p = Path(file_path)
    fmt = detect_netlist_format(p)
    parser = get_parser(fmt)
    if not parser:
        print(json.dumps({"error": f"不支持的格式: {fmt}", "supported": ["enet", "altium", "kicad", "eagle", "cadence"]}))
        return
    data = parser.parse(p)
    mcu = parser.find_mcu(data)
    interfaces = parser.extract_interfaces(data)
    print(json.dumps({
        "file": file_path,
        "format": fmt,
        "mcu": mcu,
        "interfaces": interfaces,
        "components_count": len(data.get("components", {})),
        "nets_count": len(data.get("nets", {})),
    }, ensure_ascii=False, indent=2))


def cmd_docs(project_path: str):
    """收集项目管理目录下的文档文本（纯标准库，无外部依赖）。"""
    pp = Path(project_path)
    docs = {}
    pdf_files = []
    warnings = []

    # 配置的英文目录优先，保留旧中文目录兼容。
    for pm_dir in candidate_paths(pp, "requirements"):
        if pm_dir.exists():
            for fp in pm_dir.rglob("*"):
                if not fp.is_file() or fp.name.startswith("."):
                    continue
                if fp.suffix.lower() in [".md", ".txt"]:
                    try:
                        content = fp.read_text(encoding="utf-8", errors="ignore")
                        if len(content.strip()) > 10:
                            docs[str(fp.relative_to(pp))] = content[:3000]
                    except (OSError, UnicodeError) as exc:
                        warnings.append({"source": str(fp), "error": f"{type(exc).__name__}: {exc}"})
                elif fp.suffix.lower() == ".pdf":
                    pdf_files.append(str(fp.relative_to(pp)))

    for ref_dir in candidate_paths(pp, "examples"):
        if ref_dir.exists():
            for fp in ref_dir.rglob("*"):
                if not fp.is_file() or fp.name.startswith("."):
                    continue
                if fp.suffix.lower() in [".md", ".txt"]:
                    try:
                        content = fp.read_text(encoding="utf-8", errors="ignore")
                        if len(content.strip()) > 10:
                            docs[str(fp.relative_to(pp))] = content[:3000]
                    except (OSError, UnicodeError) as exc:
                        warnings.append({"source": str(fp), "error": f"{type(exc).__name__}: {exc}"})
                elif fp.suffix.lower() == ".pdf":
                    pdf_files.append(str(fp.relative_to(pp)))

    result = {"text_docs": docs, "warnings": warnings}
    if pdf_files:
        result["pdf_files"] = pdf_files
        result["note"] = "发现 PDF 文件，请用 mcu_parse.py pdf 命令提取内容"

    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_pdf(file_path: str, keywords_str: str = None):
    """提取 PDF 文本，可按关键词过滤页面。"""
    try:
        import fitz
    except ImportError:
        print(json.dumps({"error": "需要安装 PyMuPDF: pip install PyMuPDF"}))
        return

    if keywords_str:
        keywords = keywords_str.split(",")
    else:
        keywords = ['GPIO', 'USART', 'UART', 'SPI', 'I2C', 'ADC', 'TIM', 'PWM',
                     'register', 'pin', 'voltage', 'current', 'pinout']

    doc = fitz.open(file_path)
    pages = []
    total_pages = doc.page_count
    for page in doc:
        text = page.get_text()
        if any(k.lower() in text.lower() for k in keywords):
            pages.append({
                "page": page.number + 1,
                "text": text[:4000],
            })
        if len(pages) >= 20:
            break
    doc.close()

    print(json.dumps({
        "file": file_path,
        "total_pages": total_pages,
        "matched_pages": len(pages),
        "pages": pages,
    }, ensure_ascii=False, indent=2))


def cmd_chips(project_path: str):
    """从网表中识别芯片和模块。"""
    detect_netlist_format, get_parser = _import_netlist()

    pp = Path(project_path)
    chips = []
    seen = set()

    # 搜索网表文件
    search_dirs = candidate_paths(pp, "schematic")
    warnings = []

    mcu_keywords = ['STM32', 'GD32', 'CH32', 'ESP32', 'ESP8266', 'ATMEGA', 'PIC']
    driver_keywords = ['TB6612', 'L298', 'L293', 'DRV', 'A4988', 'TMC']
    power_keywords = ['TPS', 'RT', 'AMS', 'LM', 'LDO']

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for ext in ['.enet', '.net', '.xml', '.sch']:
            for f in search_dir.rglob(f"*{ext}"):
                try:
                    fmt = detect_netlist_format(f)
                    parser = get_parser(fmt)
                    if not parser:
                        continue
                    data = parser.parse(f)
                    for comp_id, comp in data.get("components", {}).items():
                        device = comp.get("device", "").upper()
                        designator = comp.get("designator", "")
                        if designator in seen:
                            continue
                        if any(k in device for k in mcu_keywords + driver_keywords + power_keywords):
                            seen.add(designator)
                            chips.append({
                                "designator": designator,
                                "device": comp.get("device", ""),
                                "type": "mcu" if any(k in device for k in mcu_keywords) else
                                        "driver" if any(k in device for k in driver_keywords) else "power",
                                "source": str(f.relative_to(pp)),
                            })
                except Exception as exc:
                    warnings.append({"source": str(f), "error": f"{type(exc).__name__}: {exc}"})

    print(json.dumps({"chips": chips, "warnings": warnings}, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "ioc" and len(sys.argv) >= 3:
        cmd_ioc(sys.argv[2])
    elif cmd == "netlist" and len(sys.argv) >= 3:
        cmd_netlist(sys.argv[2])
    elif cmd == "docs" and len(sys.argv) >= 3:
        cmd_docs(sys.argv[2])
    elif cmd == "pdf" and len(sys.argv) >= 3:
        keywords = sys.argv[3] if len(sys.argv) > 3 else None
        cmd_pdf(sys.argv[2], keywords)
    elif cmd == "chips" and len(sys.argv) >= 3:
        cmd_chips(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
