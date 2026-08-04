"""Classify user requests before workflow selection."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict


IMPLEMENTATION_TERMS = (
    "写", "实现", "开发", "创建", "制作", "构建", "优化", "做一个", "write",
    "implement", "create", "build", "develop", "optimize",
)
EVIDENCE_TERMS = ("证据", "资料", "手册", "原理图", "evidence", "datasheet", "schematic")
DEBUG_TERMS = ("调试", "报错", "编译失败", "debug", "build error", "compile error")
REVIEW_TERMS = ("审查", "评审", "分析", "review", "audit", "analyze")


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _scope(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("ui", "界面", "lvgl", "animation", "动画")):
        return "ui"
    if re.search(r"qmc\s*-?\s*5883", lowered):
        return "qmc5883"
    if any(term in lowered for term in ("freertos", "任务调度", "scheduler")):
        return "scheduler"
    if any(term in lowered for term in ("lcd", "屏幕", "display")):
        return "display"
    return "project"


class IntentManager:
    """Create a stable intent record with an explicit expansion boundary."""

    def classify(self, user_input: str, *, mode: str = "production") -> Dict[str, Any]:
        statement = user_input.strip()
        if not statement:
            raise ValueError("user_input must not be empty")

        if _contains(statement, IMPLEMENTATION_TERMS):
            intent_type = "implementation"
        elif _contains(statement, DEBUG_TERMS):
            intent_type = "debug"
        elif _contains(statement, EVIDENCE_TERMS):
            intent_type = "evidence"
        elif _contains(statement, REVIEW_TERMS):
            intent_type = "review"
        else:
            intent_type = "implementation"

        return {
            "schema_version": "2.0",
            "intent_id": f"intent-{uuid.uuid4().hex[:12]}",
            "type": intent_type,
            "request": statement,
            "scope": [_scope(statement)],
            "priority": "user_request",
            "expansion": "register_only" if intent_type == "implementation" else "dependency_only",
            "mode": mode,
            "authority": "user",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
