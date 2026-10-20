"""Create executable work packages from intent records."""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List

from .workflow_registry import WorkflowRegistry


InputRole = str | Dict[str, str]


def _task(
    task_id: str,
    title: str,
    *,
    requirements: List[Dict[str, Any]] | None = None,
    verification: str = "host",
    verification_profile: str | None = None,
    allowed_paths: List[str] | None = None,
    handler: str = "firmware-development",
    input_roles: List[InputRole] | None = None,
    input_scope: str = "product",
    outputs: List[str] | None = None,
    execution_mode: str = "read_write",
) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "state": "READY",
        "requirements": requirements or [],
        "debt_ids": [],
        "verification_tier": verification,
        "verification_profile": verification_profile or verification,
        # Source-producing tasks deliberately start with no promised artifact.
        # Evidence tasks declare canonical roles explicitly at their call sites.
        "outputs": outputs or [],
        "handler": handler,
        "allowed_paths": allowed_paths or ["."],
        "input_roles": input_roles or [],
        "input_scope": input_scope,
        "execution_mode": execution_mode,
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

    def __init__(self, workflow_registry: WorkflowRegistry | None = None):
        self.workflows = workflow_registry or WorkflowRegistry()

    def create(
        self,
        intent: Dict[str, Any],
        *,
        platform: str | None = None,
        implementation_input_roles: Iterable[InputRole] = (),
        enforce_platform: bool = False,
    ) -> Dict[str, Any]:
        scope = intent.get("scope", ["project"])[0]
        request = intent["request"]
        # Evidence requests (datasheet/schematic/requirements extraction) must be
        # dispatched to the evidence pipeline first; a request like "提取 OV3660
        # 寄存器信息" mentions a device but is still an extraction request, not
        # a driver implementation.
        if intent["type"] == "project_setup":
            package_type = "PROJECT_SETUP"
            target = "project"
            tasks = [_task(
                "project_setup",
                "Create the canonical product workspace and tracked project contract",
                verification="analysis",
                handler="project-setup",
                allowed_paths=[
                    "hardware", "software", ".mcu-agent/project-contract.json",
                    ".mcu-agent/policy.lock", ".mcu-agent/config.yaml",
                    ".mcu-agent/artifact-manifest.yaml",
                ],
            )]
        elif intent["type"] == "evidence":
            package_type = "HARDWARE_EVIDENCE_RESOLUTION"
            target = scope
            tasks = self._evidence_tasks(scope, request)
        elif intent["type"] == "debug":
            package_type = "VALIDATION"
            target = scope
            tasks = [_task(
                "debug_build",
                request,
                verification="build",
                handler="build-debug",
                requirements=self._platform_requirements(platform),
            )]
        elif intent["type"] == "review":
            package_type = "VALIDATION"
            target = scope
            tasks = [_task(
                "review_code",
                request,
                verification="analysis",
                handler="code-qa",
                execution_mode="read_only",
                requirements=self._platform_requirements(platform),
            )]
        elif scope == "ui":
            package_type = "UI_FEATURE"
            target = "ui"
            tasks = self._ui_tasks()
        elif scope == "camera":
            package_type = "CAMERA_DRIVER"
            target = "camera"
            tasks = self._camera_tasks(platform)
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
        else:
            package_type = "IMPLEMENTATION"
            target = scope
            tasks = [_task(
                "implement_requested_feature",
                request,
                verification="build",
                handler="firmware-development",
                requirements=self._platform_requirements(platform),
            )]

        required_inputs: List[InputRole] = [
            role if isinstance(role, dict) else {"role": role, "scope": "project"}
            for role in implementation_input_roles
        ]
        for task in tasks:
            if intent["type"] == "implementation" and task.get("handler") == "firmware-development":
                existing_roles = task.setdefault("input_roles", [])
                for role in required_inputs:
                    if role not in existing_roles:
                        existing_roles.append(role)
            if enforce_platform and task.get("handler") in {"firmware-development", "build-debug"}:
                requirements = task.setdefault("requirements", [])
                platform_requirement = self._platform_requirements(platform, required=True)[0]
                if not any(item.get("capability") == "platform.project" for item in requirements):
                    requirements.append(dict(platform_requirement))
            self.workflows.bind_task(task)

        return {
            "schema_version": "2.0",
            "work_package_id": f"wp-{uuid.uuid4().hex[:12]}",
            "intent_id": intent["intent_id"],
            "intent_request": intent["request"],
            "type": package_type,
            "target": target,
            "mode": intent["mode"],
            "platform": platform or "",
            "state": "PROPOSED",
            "priority": intent["priority"],
            "expansion": intent["expansion"],
            "tasks": tasks,
            "debt_ids": [],
        }

    @staticmethod
    def _platform_requirements(platform: str | None, *, required: bool = False) -> List[Dict[str, Any]]:
        if not platform and not required:
            return []
        return [{
            "capability": "platform.project",
            "facts": ["root"],
            "gate": "REQUIRED",
            "on_missing": "BLOCKED_CONFIG",
        }]

    @staticmethod
    def _evidence_tasks(scope: str, request: str) -> List[Dict[str, Any]]:
        """Evidence requests decompose into the extraction pipeline tasks.

        Each task resolves its source artifact through the manifest, extracts
        cited facts, and registers its output as a canonical artifact — so the
        next task in the pipeline (or a later driver task) can consume it.
        """
        lowered = request.lower()
        tasks = []
        # Requirements analysis runs first when the request mentions requirements/需求.
        if any(term in lowered for term in ("需求", "prd", "requirement", "acceptance", "验收")):
            tasks.append(_task(
                "requirements_extraction",
                "Extract structured requirements and acceptance tests",
                verification="analysis",
                allowed_paths=["docs/requirements"],
                handler="requirements-analysis",
                input_roles=["requirements.primary"],
                outputs=["requirements.structured", "requirements.acceptance_tests"],
            ))
        # Datasheet extraction when the request mentions datasheet/手册/寄存器.
        if any(term in lowered for term in ("手册", "datasheet", "寄存器", "register", "数据手册")):
            tasks.append(_task(
                "datasheet_extraction",
                "Extract device capability facts from the datasheet",
                verification="analysis",
                allowed_paths=["docs/hardware"],
                handler="datasheet-extraction",
                input_roles=[{"role": "device." + scope + ".datasheet", "scope": "product"}],
                outputs=["device." + scope + ".capabilities"],
            ))
        # Schematic/peripheral extraction when the request mentions 原理图/schematic/引脚/外设.
        if any(term in lowered for term in ("原理图", "schematic", "引脚", "外设", "接线", "peripheral", "netlist")):
            tasks.append(_task(
                "hardware_extraction",
                "Extract peripheral connections from the schematic",
                verification="analysis",
                allowed_paths=["docs/hardware"],
                handler="schematic-analysis",
                input_roles=["hardware.schematic.primary"],
                outputs=["hardware.peripheral_connections"],
            ))
        if not tasks:
            tasks.append(_task(
                "review_selected_evidence",
                "Resolve selected evidence debt",
                verification="analysis",
                handler="evidence-resolution",
                allowed_paths=["docs"],
            ))
        return tasks

    @staticmethod
    def _camera_tasks(platform: str | None) -> List[Dict[str, Any]]:
        component_requirements: List[Dict[str, Any]] = []
        if platform:
            component_requirements.append({
                "capability": "platform.project",
                "facts": ["root"],
                "gate": "REQUIRED",
                "on_missing": "BLOCKED_CONFIG",
            })
        return [
            _task("camera_interface", "Define camera driver API and frame format", verification="host", allowed_paths=["components"]),
            _task("camera_config", "Implement camera sensor configuration (format, resolution)", verification="host", allowed_paths=["components"]),
            _task("camera_capture_test", "Test capture path with mock/simulated sensor", verification="host", allowed_paths=["tests", "components"]),
            _task("platform_component", "Create platform component", requirements=component_requirements, verification="build", allowed_paths=["components"]),
            _task(
                "board_binding",
                "Bind camera to board DVP/CSI pins and BSP",
                requirements=[_hardware_requirement("board.camera", "identity")],
                verification="target",
                allowed_paths=["components", "main"],
                input_roles=["hardware.peripheral_connections"],
            ),
            _task(
                "frame_validation",
                "Validate captured frames on physical sensor",
                requirements=[_hardware_requirement("board.camera", "identity")],
                verification="target",
                allowed_paths=["tests", "components"],
                input_roles=["hardware.peripheral_connections"],
            ),
        ]

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
                input_roles=["hardware.peripheral_connections"],
            ),
            _task(
                "display_hardware_test",
                "Validate UI on physical display",
                requirements=[_hardware_requirement("lcd.display", "panel.identity")],
                verification="target",
                allowed_paths=["tests", "components"],
                input_roles=["hardware.peripheral_connections"],
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
                input_roles=["hardware.peripheral_connections"],
            ),
            _task(
                "angle_calibration",
                "Calibrate and validate physical angle measurement",
                requirements=[_hardware_requirement("board.qmc5883", "mounting_orientation")],
                verification="target",
                allowed_paths=["tests", "components"],
            ),
        ]
