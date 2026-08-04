"""Create executable work packages from intent records."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List


def _task(
    task_id: str,
    title: str,
    *,
    requirements: List[Dict[str, Any]] | None = None,
    verification: str = "host",
    allowed_paths: List[str] | None = None,
    handler: str = "firmware-development",
) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "state": "READY",
        "requirements": requirements or [],
        "debt_ids": [],
        "verification_tier": verification,
        "outputs": [],
        "handler": handler,
        "allowed_paths": allowed_paths or ["."],
        "input_roles": [],
    }


def _hardware_requirement(capability: str, *facts: str) -> Dict[str, Any]:
    return {
        "capability": capability,
        "facts": list(facts),
        "gate": "REQUIRED",
        "on_missing": "BLOCKED_HARDWARE",
    }


class WorkPackageFactory:
    """Map an intent to a typed, independently schedulable work package."""

    def create(self, intent: Dict[str, Any], *, platform: str | None = None) -> Dict[str, Any]:
        scope = intent.get("scope", ["project"])[0]
        request = intent["request"]
        if scope == "ui":
            package_type = "UI_FEATURE"
            target = "ui"
            tasks = self._ui_tasks()
        elif scope == "qmc5883" or re.search(r"qmc\s*-?\s*5883", request, re.IGNORECASE):
            package_type = "SENSOR_DRIVER"
            target = "QMC5883"
            tasks = self._qmc5883_tasks(platform)
        elif scope == "scheduler":
            package_type = "SCHEDULER_OPTIMIZATION"
            target = "FreeRTOS"
            tasks = [
                _task("analyze_scheduler", "Analyze current scheduler", verification="analysis"),
                _task("implement_scheduler_change", "Implement scheduler optimization", verification="build"),
                _task("validate_scheduler", "Validate timing and load", verification="target"),
            ]
        elif intent["type"] == "evidence":
            package_type = "HARDWARE_EVIDENCE_RESOLUTION"
            target = scope
            tasks = [_task("review_selected_evidence", "Review selected manifest artifacts", verification="analysis")]
        else:
            package_type = "IMPLEMENTATION"
            target = scope
            tasks = [_task("implement_requested_feature", request, verification="build")]

        return {
            "schema_version": "2.0",
            "work_package_id": f"wp-{uuid.uuid4().hex[:12]}",
            "intent_id": intent["intent_id"],
            "intent_request": intent["request"],
            "type": package_type,
            "target": target,
            "mode": intent["mode"],
            "state": "PROPOSED",
            "priority": intent["priority"],
            "expansion": intent["expansion"],
            "tasks": tasks,
            "debt_ids": [],
        }

    @staticmethod
    def _ui_tasks() -> List[Dict[str, Any]]:
        return [
            _task("ui_architecture", "Create UI architecture", verification="host", allowed_paths=["main", "components"]),
            _task("ui_state_machine", "Implement UI state machine", verification="host", allowed_paths=["main", "components"]),
            _task("ui_animation", "Implement animation", verification="host", allowed_paths=["components"]),
            _task("mock_display_backend", "Implement mock display backend", verification="host", allowed_paths=["components"]),
            _task(
                "lcd_binding",
                "Bind UI to the physical LCD",
                requirements=[_hardware_requirement("lcd.display", "panel.identity")],
                verification="target",
                allowed_paths=["components", "main"],
            ),
            _task(
                "display_hardware_test",
                "Validate UI on physical display",
                requirements=[_hardware_requirement("lcd.display", "panel.identity")],
                verification="target",
                allowed_paths=["tests", "components"],
            ),
        ]

    @staticmethod
    def _qmc5883_tasks(platform: str | None) -> List[Dict[str, Any]]:
        component_requirements: List[Dict[str, Any]] = []
        if platform:
            component_requirements.append({
                "capability": "platform.project",
                "facts": ["root"],
                "gate": "REQUIRED",
                "on_missing": "BLOCKED_CONFIG",
            })
        return [
            _task("portable_driver_api", "Define portable QMC5883 driver API", verification="host", allowed_paths=["components"]),
            _task("register_driver", "Implement QMC5883 register layer", verification="host", allowed_paths=["components"]),
            _task("fake_i2c_test", "Test driver with fake I2C backend", verification="host", allowed_paths=["tests", "components"]),
            _task("platform_component", "Create platform component", requirements=component_requirements, verification="build", allowed_paths=["components"]),
            _task(
                "board_binding",
                "Bind QMC5883 to board I2C pins",
                requirements=[_hardware_requirement("board.qmc5883", "sda_pin", "scl_pin")],
                verification="target",
                allowed_paths=["components", "main"],
            ),
            _task(
                "angle_calibration",
                "Calibrate and validate physical angle measurement",
                requirements=[_hardware_requirement("board.qmc5883", "mounting_orientation")],
                verification="target",
                allowed_paths=["tests", "components"],
            ),
        ]
