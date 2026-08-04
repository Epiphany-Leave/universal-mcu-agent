#!/usr/bin/env python3
"""
MCU 文档生成器 — 同时生成 JSON + Markdown。

用法:
  python3 mcu_generate.py schematic <project_path>   # 硬件接口
  python3 mcu_generate.py requirements <project_path> # 需求
  python3 mcu_generate.py datasheet <project_path>    # 芯片手册
"""

import sys
import json
from pathlib import Path
from datetime import datetime

from project_config import configured_path


def ensure_output_dir(project_path: Path) -> Path:
    out = configured_path(project_path, "production") / "Hardware"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _warning(warnings: list, source: Path, exc: Exception) -> None:
    message = {"source": str(source), "error": f"{type(exc).__name__}: {exc}"}
    warnings.append(message)
    print(f"  [WARN] {source}: {message['error']}", file=sys.stderr)


# ── 硬件接口 ──────────────────────────────────────────

def generate_schematic(project_path: str):
    """解析硬件文件，生成 hardware.json + 硬件接口速查.md"""
    pp = Path(project_path)
    out = ensure_output_dir(pp)
    parse_warnings = []

    # 尝试解析 .ioc
    ioc_data = None
    for ioc in pp.rglob("*.ioc"):
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from parsers.ioc_parser import parse_ioc_file, ioc_to_hardware_interfaces
            raw = parse_ioc_file(str(ioc))
            ioc_data = ioc_to_hardware_interfaces(raw)
            ioc_data["_ioc_file"] = str(ioc.relative_to(pp))
            break
        except Exception as e:
            _warning(parse_warnings, ioc, e)

    # 尝试解析网表
    netlist_data = None
    for ext in ['.enet', '.net', '.xml', '.sch']:
        for f in pp.rglob(f"*{ext}"):
            try:
                from parsers.netlist_parser import detect_netlist_format, get_parser
                fmt = detect_netlist_format(f)
                parser = get_parser(fmt)
                if parser:
                    data = parser.parse(f)
                    netlist_data = {
                        "mcu": parser.find_mcu(data),
                        "interfaces": parser.extract_interfaces(data),
                        "file": str(f.relative_to(pp)),
                        "format": fmt,
                    }
                    break
            except Exception as exc:
                _warning(parse_warnings, f, exc)
        if netlist_data:
            break

    # 合并数据
    result = {
        "mcu": "",
        "package": "",
        "interfaces": [],
        "warnings": parse_warnings,
        "source": {"artifact": "UNKNOWN", "evidence_class": "UNTRUSTED_OR_MODIFIED", "confidence": "unknown"},
    }

    if ioc_data:
        result["mcu"] = ioc_data.get("mcu", "")
        result["package"] = ioc_data.get("package", "")
        result["interfaces"] = ioc_data.get("interfaces", [])
        result["source"] = {"artifact": ioc_data.get("_ioc_file", "UNKNOWN"), "evidence_class": "GENERATED_CONFIG", "confidence": "unverified"}
    elif netlist_data:
        mcu = netlist_data.get("mcu", {})
        result["mcu"] = mcu.get("device", "") if mcu else ""
        result["package"] = mcu.get("footprint", "") if mcu else ""
        result["interfaces"] = netlist_data.get("interfaces", [])
        result["source"] = {"artifact": netlist_data.get("file", "UNKNOWN"), "format": netlist_data.get("format", "UNKNOWN"), "evidence_class": "HARDWARE_FACT", "confidence": "unverified"}

    # 写 JSON
    json_path = out / "hardware.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [OK] {json_path.relative_to(pp)}")

    # 写 Markdown
    md_path = out / "硬件接口速查.md"
    md = _hardware_to_md(result)
    md_path.write_text(md, encoding="utf-8")
    print(f"  [OK] {md_path.relative_to(pp)}")

    return result


def _hardware_to_md(data: dict) -> str:
    lines = [
        "# 硬件接口速查表",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 数据来源: {json.dumps(data.get('source', {}), ensure_ascii=False)}",
        f"> MCU: {data.get('mcu') or 'UNKNOWN'}",
        "",
        "---",
        "",
    ]

    groups = {}
    for iface in data.get("interfaces", []):
        t = iface.get("type", "OTHER")
        groups.setdefault(t, []).append(iface)

    type_names = {
        "GPIO_OUTPUT": "GPIO 输出", "GPIO_INPUT": "GPIO 输入",
        "PWM": "PWM / 定时器", "ADC": "ADC 模拟采集",
        "UART": "串口 (UART)", "SPI": "SPI", "I2C": "I2C",
    }

    for t, ifaces in groups.items():
        lines.append(f"## {type_names.get(t, t)}")
        lines.append("")
        if t in ("GPIO_OUTPUT", "GPIO_INPUT"):
            lines.append("| 引脚 | 名称 | 信号 | 有效电平 | 默认状态 | 用途 |")
            lines.append("|------|------|------|---------|---------|------|")
            for f in ifaces:
                lines.append(f"| {f.get('pin') or 'UNKNOWN'} | {f.get('name') or 'UNKNOWN'} | {f.get('signal') or 'UNKNOWN'} | {f.get('active_level') or 'UNKNOWN'} | {f.get('default_state') or 'UNKNOWN'} | {f.get('usage') or 'UNKNOWN'} |")
        elif t == "PWM":
            lines.append("| 引脚 | 名称 | 信号 | 频率 | 用途 |")
            lines.append("|------|------|------|------|------|")
            for f in ifaces:
                lines.append(f"| {f.get('pin') or 'UNKNOWN'} | {f.get('name') or 'UNKNOWN'} | {f.get('signal') or 'UNKNOWN'} | {f.get('frequency') or 'UNKNOWN'} | {f.get('usage') or 'UNKNOWN'} |")
        elif t == "ADC":
            lines.append("| 引脚 | 名称 | 信号 | 分辨率 | 用途 |")
            lines.append("|------|------|------|--------|------|")
            for f in ifaces:
                lines.append(f"| {f.get('pin') or 'UNKNOWN'} | {f.get('name') or 'UNKNOWN'} | {f.get('signal') or 'UNKNOWN'} | {f.get('resolution') or 'UNKNOWN'} | {f.get('usage') or 'UNKNOWN'} |")
        elif t == "UART":
            lines.append("| 引脚 | 名称 | 信号 | 波特率 | 用途 |")
            lines.append("|------|------|------|--------|------|")
            for f in ifaces:
                lines.append(f"| {f.get('pin') or 'UNKNOWN'} | {f.get('name') or 'UNKNOWN'} | {f.get('signal') or 'UNKNOWN'} | {f.get('baudrate') or 'UNKNOWN'} | {f.get('usage') or 'UNKNOWN'} |")
        else:
            lines.append("| 引脚 | 名称 | 信号 | 类型 | 用途 |")
            lines.append("|------|------|------|------|------|")
            for f in ifaces:
                lines.append(f"| {f.get('pin') or 'UNKNOWN'} | {f.get('name') or 'UNKNOWN'} | {f.get('signal') or 'UNKNOWN'} | {t} | {f.get('usage') or 'UNKNOWN'} |")
        lines.append("")

    if data.get("warnings"):
        lines.append("## ⚠️ 硬件警告")
        lines.append("")
        for w in data["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


# ── 芯片手册 ──────────────────────────────────────────

def generate_datasheet(project_path: str):
    """从网表识别芯片，生成 datasheet.json + 芯片手册提取.md"""
    pp = Path(project_path)
    out = ensure_output_dir(pp)
    parse_warnings = []

    chips = []
    seen = set()

    # 从网表识别芯片
    for ext in ['.enet', '.net', '.xml', '.sch']:
        for f in pp.rglob(f"*{ext}"):
            try:
                from parsers.netlist_parser import detect_netlist_format, get_parser
                fmt = detect_netlist_format(f)
                parser = get_parser(fmt)
                if parser:
                    data = parser.parse(f)
                    for comp_id, comp in data.get("components", {}).items():
                        device = comp.get("device", "").upper()
                        designator = comp.get("designator", "")
                        if designator in seen:
                            continue
                        mcu_kw = ['STM32', 'GD32', 'CH32', 'ESP32', 'ATMEGA', 'PIC']
                        drv_kw = ['TB6612', 'L298', 'DRV', 'A4988', 'TMC']
                        pwr_kw = ['TPS', 'RT', 'AMS', 'LDO']
                        if any(k in device for k in mcu_kw + drv_kw + pwr_kw):
                            seen.add(designator)
                            chip_type = "mcu" if any(k in device for k in mcu_kw) else "driver" if any(k in device for k in drv_kw) else "power"
                            chips.append({"designator": designator, "device": comp.get("device", ""), "type": chip_type})
            except Exception as exc:
                _warning(parse_warnings, f, exc)

    result = {
        "chips": chips,
        "peripherals": {},
        "page_references": [],
        "source": {"evidence_class": "HARDWARE_FACT", "confidence": "unverified"},
        "warnings": parse_warnings,
    }

    # 写 JSON
    json_path = out / "datasheet.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [OK] {json_path.relative_to(pp)}")

    # 写 Markdown
    md_path = out / "芯片手册提取.md"
    md_lines = [
        "# 芯片手册提取",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 识别到的芯片: {', '.join(c.get('designator','') + '(' + c.get('device','') + ')' for c in chips)}",
        "",
        "---",
        "",
    ]
    for chip in chips:
        md_lines.append(f"## {chip['designator']} ({chip['device']})")
        md_lines.append(f"- 类型: {chip['type']}")
        md_lines.append("")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"  [OK] {md_path.relative_to(pp)}")

    return result


# ── 入口 ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("用法: python3 mcu_generate.py <schematic|requirements|datasheet> <project_path>")
        sys.exit(1)

    cmd, project_path = sys.argv[1], sys.argv[2]

    if cmd == "schematic":
        generate_schematic(project_path)
    elif cmd == "datasheet":
        generate_datasheet(project_path)
    elif cmd == "requirements":
        print("requirements 生成请直接用 Claude Code 分析文档后调用 Write 工具")
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
