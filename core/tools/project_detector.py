#!/usr/bin/env python3
"""
MCU Project Context Detector

扫描项目文件，自动识别 MCU 族、芯片型号、工具链。
支持 MCU 型号标准化映射。

用法:
  python3 project_detector.py <project_path>

输出: JSON
{
  "mcu_family": "esp32",
  "chip": "ESP32-S31",
  "arch": "xtensa",
  "toolchain_ide": "esp-idf",
  "toolchain_compiler": "gcc",
  "knowledge_platform": "esp32",
  "confidence": "high",
  "evidence": ["sdkconfig: CONFIG_IDF_TARGET=esp32s31"]
}
"""

import sys
import os
import re
import json
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1]
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from runtime.platforms.esp_idf import EspIdfAdapter

# 尝试导入 yaml，如果不可用则使用 json 降级
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_alias(alias_path: Path) -> dict:
    """加载 MCU 别名映射文件"""
    if not alias_path.exists():
        return {}

    try:
        if HAS_YAML:
            with open(alias_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        else:
            # 降级：尝试 json 格式
            json_path = alias_path.with_suffix(".json")
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass

    return {}


def normalize_mcu(raw: str, alias: dict) -> dict:
    """
    将原始 MCU 字符串标准化。

    输入: "esp32s31" / "ESP32-S31" / "ESP32S31"
    输出: {"family": "esp32", "chip": "ESP32-S31", "arch": "xtensa"}
    """
    if not raw or not alias:
        return {}

    key = raw.lower().replace("-", "").replace("_", "").replace(" ", "")

    # 遍历所有族
    for family, chips in alias.items():
        if not isinstance(chips, dict):
            continue
        for alias_key, alias_val in chips.items():
            if alias_key.lower().replace("-", "").replace("_", "") == key:
                return alias_val

    return {}


class ProjectDetector:
    """MCU 项目上下文检测器"""

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.evidence = []

        # 加载 alias 映射
        script_dir = Path(__file__).parent
        alias_path = script_dir.parent / "config" / "mcu-alias.yaml"
        self.alias = load_alias(alias_path)

        self.result = {
            "mcu_family": "",
            "chip": "",
            "arch": "",
            "toolchain_ide": "",
            "toolchain_compiler": "",
            "knowledge_platform": "",
            "confidence": "low",
            "evidence": []
        }

    def detect(self) -> dict:
        """执行检测，返回结果"""
        # 按优先级检测
        if self._detect_by_ioc():
            pass
        elif self._detect_by_sdkconfig():
            pass
        elif self._detect_by_platformio():
            pass
        elif self._detect_by_cproject():
            pass
        elif self._detect_by_uvprojx():
            pass
        elif self._detect_by_cmake_idf():
            pass

        # 推导 knowledge platform
        self._derive_knowledge_platform()

        # 设置置信度
        self._set_confidence()

        self.result["evidence"] = self.evidence
        topology = self.detect_workspace()
        self.result["workspace"] = topology["workspace"]
        self.result["projects"] = topology["projects"]
        return self.result

    def detect_workspace(self) -> dict:
        """Return every supported project candidate instead of a first-match root."""
        adapter = EspIdfAdapter()
        candidates = []
        seen = set()
        for cmake in self.project_path.rglob("CMakeLists.txt"):
            if "build" in cmake.parts or cmake.parent.name in {"main", "components"}:
                continue
            root = cmake.parent.resolve()
            if root in seen:
                continue
            detected = adapter.detect(root)
            if detected["platform"]:
                seen.add(root)
                try:
                    detected["relative_root"] = str(root.relative_to(self.project_path.resolve())) or "."
                except ValueError:
                    detected["relative_root"] = str(root)
                detected["id"] = root.name
                candidates.append(detected)
        return {
            "workspace": {"product_root": str(self.project_path.resolve())},
            "projects": sorted(candidates, key=lambda item: item["relative_root"]),
        }

    def _apply_alias(self, raw: str, source: str) -> bool:
        """应用 alias 映射标准化 MCU 字符串"""
        normalized = normalize_mcu(raw, self.alias)
        if normalized:
            self.result["mcu_family"] = normalized.get("family", "")
            self.result["chip"] = normalized.get("chip", "")
            self.result["arch"] = normalized.get("arch", "")
            self.evidence.append(f"{source}: {raw} -> {normalized.get('chip', raw)}")
            return True
        return False

    def _detect_by_ioc(self) -> bool:
        """通过 .ioc 文件检测 STM32"""
        ioc_files = list(self.project_path.rglob("*.ioc"))
        if not ioc_files:
            return False

        for ioc in ioc_files:
            try:
                content = ioc.read_text(encoding="utf-8", errors="ignore")

                # 提取芯片型号
                m = re.search(r"ProjectManager\.DeviceId=(\w+)", content)
                if m:
                    raw = m.group(1)
                    if self._apply_alias(raw, ioc.name):
                        self.result["toolchain_ide"] = "stm32cubeide"
                        self.result["toolchain_compiler"] = "gcc"
                        return True

                    # 降级：直接使用原始值
                    self.result["mcu_family"] = "stm32"
                    self.result["chip"] = raw
                    self.result["arch"] = "arm"
                    self.result["toolchain_ide"] = "stm32cubeide"
                    self.result["toolchain_compiler"] = "gcc"
                    self.evidence.append(f"{ioc.name}: DeviceId={raw}")
                    return True

                if "STM32" in content:
                    self.result["mcu_family"] = "stm32"
                    self.result["arch"] = "arm"
                    self.result["toolchain_ide"] = "stm32cubeide"
                    self.result["toolchain_compiler"] = "gcc"
                    self.evidence.append(f"{ioc.name}: STM32 reference found")
                    return True
            except Exception:
                continue

        return False

    def _detect_by_sdkconfig(self) -> bool:
        """通过 sdkconfig 检测 ESP32"""
        sdkconfig = self.project_path / "sdkconfig"
        if not sdkconfig.exists():
            configs = list(self.project_path.rglob("sdkconfig"))
            if configs:
                sdkconfig = configs[0]
            else:
                return False

        try:
            content = sdkconfig.read_text(encoding="utf-8", errors="ignore")

            m = re.search(r'CONFIG_IDF_TARGET="(\w+)"', content)
            if m:
                raw = m.group(1)
                if self._apply_alias(raw, "sdkconfig"):
                    self.result["toolchain_ide"] = "esp-idf"
                    self.result["toolchain_compiler"] = "gcc"
                    return True

                # 降级
                self.result["mcu_family"] = "esp32"
                self.result["chip"] = raw.upper()
                self.result["arch"] = self._esp32_arch(raw)
                self.result["toolchain_ide"] = "esp-idf"
                self.result["toolchain_compiler"] = "gcc"
                self.evidence.append(f"sdkconfig: IDF_TARGET={raw}")
                return True

            if "CONFIG_ESP32" in content or "CONFIG_IDF_TARGET" in content:
                self.result["mcu_family"] = "esp32"
                self.result["arch"] = "xtensa"
                self.result["toolchain_ide"] = "esp-idf"
                self.result["toolchain_compiler"] = "gcc"
                self.evidence.append("sdkconfig: ESP32 config found")
                return True
        except Exception:
            pass

        return False

    def _detect_by_platformio(self) -> bool:
        """通过 platformio.ini 检测"""
        pio = self.project_path / "platformio.ini"
        if not pio.exists():
            return False

        try:
            content = pio.read_text(encoding="utf-8", errors="ignore")

            if "espressif32" in content or "esp32" in content.lower():
                self.result["mcu_family"] = "esp32"
                self.result["arch"] = "xtensa"
                self.result["toolchain_ide"] = "platformio"
                self.result["toolchain_compiler"] = "gcc"

                m = re.search(r"board\s*=\s*(\w+)", content)
                if m:
                    raw = m.group(1)
                    normalized = normalize_mcu(raw, self.alias)
                    if normalized:
                        self.result["chip"] = normalized.get("chip", raw)
                        self.evidence.append(f"platformio.ini: board={raw} -> {normalized.get('chip', raw)}")
                    else:
                        self.result["chip"] = raw.upper()
                        self.evidence.append(f"platformio.ini: board={raw}")

                self.evidence.append("platformio.ini: platform=espressif32")
                return True

            if "ststm32" in content:
                self.result["mcu_family"] = "stm32"
                self.result["arch"] = "arm"
                self.result["toolchain_ide"] = "platformio"
                self.result["toolchain_compiler"] = "gcc"

                m = re.search(r"board\s*=\s*(\w+)", content)
                if m:
                    self.result["chip"] = m.group(1).upper()
                    self.evidence.append(f"platformio.ini: board={m.group(1)}")

                self.evidence.append("platformio.ini: platform=ststm32")
                return True
        except Exception:
            pass

        return False

    def _detect_by_cproject(self) -> bool:
        """通过 .cproject 检测 CH592 / TI C2000"""
        cproject_files = list(self.project_path.rglob(".cproject"))
        if not cproject_files:
            return False

        for cp in cproject_files:
            try:
                content = cp.read_text(encoding="utf-8", errors="ignore")

                if "CH592" in content or "CH591" in content or "WCH" in content:
                    chip = "CH592" if "CH592" in content else "CH591"
                    if self._apply_alias(chip, ".cproject"):
                        self.result["toolchain_ide"] = "mounriver"
                        self.result["toolchain_compiler"] = "riscv-gcc"
                        return True

                    self.result["mcu_family"] = "ch592"
                    self.result["chip"] = chip
                    self.result["arch"] = "riscv"
                    self.result["toolchain_ide"] = "mounriver"
                    self.result["toolchain_compiler"] = "riscv-gcc"
                    self.evidence.append(f".cproject: {chip} detected")
                    return True

                if "TMS320" in content or "com.ti.ccstudio" in content:
                    self.result["mcu_family"] = "ti-c2000"
                    self.result["arch"] = "c2000"
                    self.result["toolchain_ide"] = "ccs"
                    self.result["toolchain_compiler"] = "ti-cgt"

                    m = re.search(r"<deviceId>(TMS320\w+)</deviceId>", content)
                    if m:
                        raw = m.group(1)
                        normalized = normalize_mcu(raw, self.alias)
                        if normalized:
                            self.result["chip"] = normalized.get("chip", raw)
                        else:
                            self.result["chip"] = raw
                        self.evidence.append(f".cproject: deviceId={raw}")

                    self.evidence.append(".cproject: TI CCS project")
                    return True
            except Exception:
                continue

        return False

    def _detect_by_uvprojx(self) -> bool:
        """通过 .uvprojx 检测 STM32/GD32/PY32 (Keil)"""
        uvprojx_files = list(self.project_path.rglob("*.uvprojx"))
        if not uvprojx_files:
            return False

        for f in uvprojx_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")

                for pattern, family_hint in [
                    (r"<Device>(STM32\w+)</Device>", "stm32"),
                    (r"<Device>(GD32\w+)</Device>", "stm32"),
                    (r"<Device>(PY32\w+)</Device>", "stm32"),
                ]:
                    m = re.search(pattern, content)
                    if m:
                        raw = m.group(1)
                        if self._apply_alias(raw, f.name):
                            self.result["toolchain_ide"] = "keil"
                            self.result["toolchain_compiler"] = "armcc"
                            return True

                        # 降级
                        self.result["mcu_family"] = family_hint
                        self.result["chip"] = raw
                        self.result["arch"] = "arm"
                        self.result["toolchain_ide"] = "keil"
                        self.result["toolchain_compiler"] = "armcc"
                        self.evidence.append(f"{f.name}: Device={raw}")
                        return True
            except Exception:
                continue

        return False

    def _detect_by_cmake_idf(self) -> bool:
        """通过 CMakeLists.txt + idf_component.yml 检测 ESP-IDF"""
        cmake = self.project_path / "CMakeLists.txt"
        idf_yml = self.project_path / "main" / "idf_component.yml"

        if not cmake.exists() or not idf_yml.exists():
            return False

        try:
            cmake_content = cmake.read_text(encoding="utf-8", errors="ignore")
            if "idf_component_register" in cmake_content or "ESP_PLATFORM" in cmake_content:
                self.result["mcu_family"] = "esp32"
                self.result["arch"] = "xtensa"
                self.result["toolchain_ide"] = "esp-idf"
                self.result["toolchain_compiler"] = "gcc"
                self.evidence.append("CMakeLists.txt + idf_component.yml: ESP-IDF project")
                return True
        except Exception:
            pass

        return False

    def _esp32_arch(self, target: str) -> str:
        target = target.lower()
        if any(x in target for x in ["c2", "c3", "c5", "c6", "h2"]):
            return "riscv"
        return "xtensa"

    def _derive_knowledge_platform(self):
        family = self.result.get("mcu_family", "")
        platform_map = {
            "stm32": "stm32",
            "esp32": "esp32",
            "ch592": "ch592",
            "ch591": "ch592",
            "ch32": "ch592",
            "ti-c2000": "ti-c2000",
        }
        self.result["knowledge_platform"] = platform_map.get(family, "")

    def _set_confidence(self):
        n = len(self.evidence)
        if n >= 2:
            self.result["confidence"] = "high"
        elif n == 1:
            self.result["confidence"] = "medium"
        else:
            self.result["confidence"] = "low"


def main():
    if len(sys.argv) < 2:
        print("用法: python3 project_detector.py <project_path>")
        sys.exit(1)

    project_path = sys.argv[1]
    if not os.path.isdir(project_path):
        print(json.dumps({"error": f"路径不存在: {project_path}"}))
        sys.exit(1)

    detector = ProjectDetector(project_path)
    result = detector.detect()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
