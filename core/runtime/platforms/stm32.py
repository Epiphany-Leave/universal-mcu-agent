"""STM32 platform adapter: CubeMX .ioc facts → capabilities + layout validation.

The runtime previously had no ARM support: stm32 was `pending` in the platform
registry, `.ioc` parsing lived in the legacy tools layer and never fed the gate.
This adapter closes that gap:

- `extract_facts()` turns a CubeMX .ioc pin table into capability records
  (e.g. `board.i2c1.scl_pin = PA8 [CONFIRMED]`), so ARM hardware facts can
  auto-unblock driver tasks exactly like the ESP32 config.yaml facts do.
- `detect()` / `validate()` recognize STM32 project layout (.ioc + toolchain).

The .ioc parser is intentionally self-contained (no dependency on the legacy
tools package) so the runtime works standalone.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import PlatformAdapter

# ── minimal .ioc parser ──────────────────────────────────────────

_PIN_LINE = re.compile(r"^Mcu\.Pin\d+=(.+)$")
_PROPERTY = re.compile(r"^([A-Za-z0-9_\-]+)\.(Signal|Label|Mode|GPIO_Label)=(.*)$")
_MCU = re.compile(r"^Mcu\.(?:CPN|Name|UserName)=(.*)$")


def parse_ioc(ioc_path: str | Path) -> Dict[str, Any]:
    """Parse a CubeMX .ioc file into {mcu, pins: {pin: {signal, label, mode}}}."""
    path = Path(ioc_path)
    if not path.is_file():
        raise FileNotFoundError(f".ioc file not found: {ioc_path}")
    result: Dict[str, Any] = {"mcu": "", "pins": {}}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pin_match = _PIN_LINE.match(stripped)
        if pin_match:
            result["pins"].setdefault(
                pin_match.group(1).strip(),
                {"signal": "", "label": "", "mode": ""},
            )
            continue
        mcu_match = _MCU.match(stripped)
        if mcu_match and not result["mcu"]:
            result["mcu"] = mcu_match.group(1).strip()
            continue
        prop = _PROPERTY.match(stripped)
        if prop:
            pin, key, value = prop.groups()
            if pin in result["pins"]:
                if key == "GPIO_Label":
                    result["pins"][pin]["label"] = value.strip()
                elif key.lower() in result["pins"][pin]:
                    result["pins"][pin][key.lower()] = value.strip()
    return result


# ── signal → capability/fact mapping ────────────────────────────

_BUS = r"(I2C|I2S|USART|LPUART|UART|SPI|ADC|DAC|TIM|SDMMC|SDIO|FDCAN|CAN|USB|ETH|QSPI|FMC|DCMI|RCC|SYS|PWR|GPIO)"
_SIGNAL = re.compile(rf"^({_BUS})(\d*)(?:_(.+))?$")


def _signal_to_fact(signal: str) -> Optional[Tuple[str, str]]:
    """Map a CubeMX signal (e.g. I2C1_SCL) to (capability_id, fact_name)."""
    match = _SIGNAL.match(signal)
    if not match:
        return None
    bus, number, function = match.group(2), match.group(3), match.group(4) or ""
    number = number or "1"
    function = function.upper()

    if bus == "I2C":
        if function == "SCL":
            return f"board.i2c{number}", "scl_pin"
        if function == "SDA":
            return f"board.i2c{number}", "sda_pin"
    if bus in {"USART", "UART", "LPUART"}:
        pin_map = {"TX": "tx_pin", "RX": "rx_pin", "RTS": "rts_pin", "CTS": "cts_pin",
                   "CK": "ck_pin", "DE": "de_pin"}
        if function in pin_map:
            return f"board.uart{number}", pin_map[function]
    if bus == "SPI":
        pin_map = {"SCK": "sck_pin", "MISO": "miso_pin", "MOSI": "mosi_pin",
                   "NSS": "cs_pin", "CS": "cs_pin"}
        if function in pin_map:
            return f"board.spi{number}", pin_map[function]
    if bus == "ADC":
        if function.startswith("IN"):
            return f"board.adc{number}", f"channel_{function}"
    if bus == "I2S":
        pin_map = {"SCK": "sck_pin", "WS": "ws_pin", "SD": "sd_pin", "MCK": "mclk_pin"}
        if function in pin_map:
            return f"board.i2s{number}", pin_map[function]
    if bus == "TIM":
        if function.startswith("CH"):
            return f"board.tim{number}", f"channel_{function.lower()}"
        if function in {"BKIN", "ETR"}:
            return f"board.tim{number}", function.lower()
    if bus == "SDMMC":
        if function in {"CLK", "CMD", "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "CK"}:
            return f"board.sdmmc{number}", f"{function.lower()}_pin"
    if bus in {"CAN", "FDCAN"}:
        pin_map = {"RX": "rx_pin", "TX": "tx_pin"}
        if function in pin_map:
            return f"board.can{number}", pin_map[function]
    if bus == "USB":
        pin_map = {"DM": "dm_pin", "DP": "dp_pin", "VBUS": "vbus_pin", "ID": "id_pin"}
        if function in pin_map:
            return f"board.usb{number}", pin_map[function]
    if bus == "ETH":
        pin_map = {"RXD0": "rxd0_pin", "RXD1": "rxd1_pin", "TXD0": "txd0_pin",
                   "TXD1": "txd1_pin", "MDC": "mdc_pin", "MDIO": "mdio_pin",
                   "CRS": "crs_pin", "TXEN": "txen_pin", "REF_CLK": "ref_clk_pin"}
        if function in pin_map:
            return f"board.eth{number}", pin_map[function]
    if bus == "DCMI":
        if function in {"D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7"}:
            return f"board.dcmi{number}", f"data_{function.lower()}_pin"
        pin_map = {"PCLK": "pclk_pin", "HSYNC": "hsync_pin", "VSYNC": "vsync_pin"}
        if function in pin_map:
            return f"board.dcmi{number}", pin_map[function]
    return None


def extract_facts(ioc_path: str | Path) -> Dict[str, Any]:
    """Return capability records derived from a CubeMX .ioc pin table."""
    data = parse_ioc(ioc_path)
    capabilities: Dict[str, Dict[str, Any]] = {}
    for pin, info in data["pins"].items():
        signal = info.get("signal", "")
        if not signal:
            continue
        fact = _signal_to_fact(signal)
        if not fact:
            continue
        capability_id, fact_name = fact
        capability = capabilities.setdefault(
            capability_id,
            {
                "schema_version": "2.0",
                "id": capability_id,
                "state": "AVAILABLE",
                "facts": {},
            },
        )
        capability["facts"][fact_name] = {
            "state": "CONFIRMED",
            "value": pin,
            "evidence_ids": [f"ioc:{Path(ioc_path).name}:{pin}"],
        }
    return {
        "schema_version": "2.0",
        "source": str(ioc_path),
        "mcu": data.get("mcu", ""),
        "capabilities": list(capabilities.values()),
    }


class Stm32Adapter(PlatformAdapter):
    name = "stm32"

    @staticmethod
    def find_ioc(root: Path) -> Optional[Path]:
        """Locate a confined primary .ioc file (root or one level deep)."""
        root = root.resolve()
        if not root.is_dir():
            return root if root.is_file() and root.suffix.lower() == ".ioc" else None
        candidates = sorted(root.glob("*.ioc")) or sorted(root.glob("*/*.ioc"))
        for candidate in candidates:
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                # A symlinked child must not make an external IOC authoritative.
                continue
            if resolved.is_file():
                return resolved
        return None

    def detect(self, root: Path) -> Dict[str, Any]:
        root = root.resolve()
        project_root = root.parent if root.is_file() else root
        evidence: List[str] = []
        ioc = self.find_ioc(root)
        if ioc:
            evidence.append(f"{ioc.name}")
        for suffix in (".uvprojx", ".uvproj", ".cproject", ".project"):
            if any(self._confined(path, project_root) and path.is_file() for path in project_root.glob(f"*{suffix}")):
                evidence.append(suffix[1:])
        if self._confined(project_root / "CMakeLists.txt", project_root) and (project_root / "CMakeLists.txt").is_file():
            evidence.append("CMakeLists.txt")
        if self._confined(project_root / "Core", project_root) and (project_root / "Core").is_dir():
            evidence.append("Core/ (CubeMX layout)")
        return {
            "platform": self.name if evidence else None,
            "root": str(root),
            "kind": "application" if evidence else "unknown",
            "confidence": "high" if len(evidence) >= 2 else "medium" if evidence else "low",
            "evidence": evidence,
        }

    @staticmethod
    def _confined(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def validate(self, root: Path, **kwargs: Any) -> Dict[str, Any]:
        root = root.resolve()
        project_root = root.parent if root.is_file() else root
        errors: List[str] = []
        warnings: List[str] = []
        ioc = self.find_ioc(root)
        if not ioc:
            errors.append("no CubeMX .ioc file found")
        else:
            data = parse_ioc(ioc)
            if not data["pins"]:
                warnings.append(f"{ioc.name} declares no pins")
        mcu = None
        if ioc:
            mcu = parse_ioc(ioc).get("mcu") or None
        toolchain_files = [
            path for pattern in ("*.uvprojx", "*.uvproj")
            for path in project_root.glob(pattern)
            if self._confined(path, project_root) and path.is_file()
        ]
        cmake = project_root / "CMakeLists.txt"
        if not toolchain_files and not (self._confined(cmake, project_root) and cmake.is_file()):
            warnings.append("no Keil project or CMakeLists.txt found")
        return {
            "platform": self.name,
            "scope": str(root),
            "mcu": mcu,
            "status": "PASS" if not errors else "FAIL",
            "ioc": str(ioc) if ioc else None,
            "errors": errors,
            "warnings": warnings,
        }
