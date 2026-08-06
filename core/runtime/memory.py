"""Project-scoped memory capture, retrieval, approval, and promotion."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


class ProjectMemoryError(ValueError):
    pass


class ProjectMemoryStore:
    """Keep project memories isolated until a human explicitly promotes them."""

    def __init__(self, product_root: str | Path):
        self.product_root = Path(product_root).resolve()
        self.memory_root = self.product_root / ".mcu-agent" / "runtime" / "memories"
        self.knowledge_root = self.product_root / ".mcu-agent" / "knowledge"

    def capture(
        self,
        *,
        summary: str,
        problem: str = "",
        solution: str = "",
        source_task: str = "",
        work_package: str = "",
        platform: str = "",
        chip: str = "",
        source_files: Iterable[str] = (),
        source_artifacts: Iterable[str] = (),
        verification: Dict[str, Any] | None = None,
        confidence: str = "draft",
        tags: Iterable[str] = (),
    ) -> Dict[str, Any]:
        if not summary.strip():
            raise ProjectMemoryError("memory summary must not be empty")
        if confidence not in {"verified", "draft", "experimental"}:
            raise ProjectMemoryError(f"invalid confidence: {confidence}")
        sources = list(source_files)
        artifacts = list(source_artifacts)
        fingerprint_payload = json.dumps(
            {"summary": summary, "problem": problem, "solution": solution, "source_files": sources},
            ensure_ascii=False,
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
        for existing in self._records():
            if existing.get("fingerprint") == fingerprint and existing.get("status") != "REJECTED":
                existing["deduplicated"] = True
                return existing

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "schema_version": "2.0",
            "memory_id": f"mem-{uuid.uuid4().hex[:12]}",
            "scope": "project",
            "status": "DRAFT",
            "summary": summary.strip(),
            "problem": problem.strip(),
            "solution": solution.strip(),
            "source_task": source_task,
            "work_package": work_package,
            "platform": platform,
            "chip": chip,
            "source_files": sources,
            "source_artifacts": artifacts,
            "verification": verification,
            "confidence": confidence,
            "tags": list(tags),
            "fingerprint": fingerprint,
            "created_at": now,
            "updated_at": now,
        }
        self._write(record)
        return record

    def search(self, query: str, *, platform: str | None = None, limit: int = 5) -> List[Dict[str, Any]]:
        if limit < 1:
            return []
        normalized = query.strip().lower()
        terms = [term for term in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized) if term]
        matches = []
        for record in self._records():
            if record.get("status") in {"REJECTED", "SUPERSEDED"}:
                continue
            if platform and record.get("platform") and record.get("platform") != platform:
                continue
            haystack = json.dumps(record, ensure_ascii=False).lower()
            score = (2 if normalized and normalized in haystack else 0) + sum(haystack.count(term) for term in terms)
            if score:
                item = dict(record)
                item["match_score"] = score
                matches.append(item)
        matches.sort(key=lambda item: (item["match_score"], item.get("updated_at", "")), reverse=True)
        return matches[:limit]

    def approve(self, memory_id: str, *, reviewer: str, notes: str = "") -> Dict[str, Any]:
        record = self._get(memory_id)
        if record["status"] not in {"DRAFT", "APPROVED_PROJECT"}:
            raise ProjectMemoryError(f"memory {memory_id} cannot be approved from {record['status']}")
        record.update({
            "status": "APPROVED_PROJECT",
            "reviewer": reviewer,
            "review_notes": notes,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        self._write(record)
        return record

    def promote(self, memory_id: str, *, target: str, reviewer: str) -> Dict[str, Any]:
        if target not in {"common", "platform"}:
            raise ProjectMemoryError("promotion target must be common or platform")
        record = self._get(memory_id)
        if record["status"] != "APPROVED_PROJECT":
            raise ProjectMemoryError("memory must be APPROVED_PROJECT before promotion")
        if target == "platform" and not record.get("platform"):
            raise ProjectMemoryError("platform promotion requires memory.platform")
        destination = self.knowledge_root / ("common" if target == "common" else f"platform/{record['platform']}")
        destination.mkdir(parents=True, exist_ok=True)
        filename = f"{record['memory_id']}.md"
        path = destination / filename
        path.write_text(self._markdown(record), encoding="utf-8")
        record.update({
            "status": "PROMOTED",
            "promotion_target": target,
            "promoted_path": str(path),
            "promoted_by": reviewer,
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        self._write(record)
        return record

    def _records(self) -> List[Dict[str, Any]]:
        if not self.memory_root.is_dir():
            return []
        records = []
        for path in sorted(self.memory_root.glob("mem-*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProjectMemoryError(f"cannot read memory {path}: {exc}") from exc
            if isinstance(value, dict):
                records.append(value)
        return records

    def _get(self, memory_id: str) -> Dict[str, Any]:
        for record in self._records():
            if record.get("memory_id") == memory_id:
                return record
        raise ProjectMemoryError(f"unknown project memory: {memory_id}")

    def _write(self, record: Dict[str, Any]) -> Path:
        self.memory_root.mkdir(parents=True, exist_ok=True)
        path = self.memory_root / f"{record['memory_id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    @staticmethod
    def _markdown(record: Dict[str, Any]) -> str:
        def yaml_value(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False)

        return "\n".join([
            "---",
            f"name: {record['memory_id']}",
            f"description: {record['summary']}",
            "category: [project-experience]",
            f"platform: [{record.get('platform', '')}]" if record.get("platform") else "platform: []",
            f"keywords: {yaml_value(record.get('tags', []))}",
            "related_workflows: [firmware-development, build-debug, code-qa]",
            "source: [project-experience]",
            f"confidence: {record.get('confidence', 'draft')}",
            f"version: \"1.0.0\"",
            "---",
            "",
            f"# {record['summary']}",
            "",
            f"## Problem\n{record.get('problem', '')}",
            "",
            f"## Solution\n{record.get('solution', '')}",
            "",
            f"## Evidence\n{yaml_value(record.get('verification'))}",
            "",
            f"Source task: `{record.get('source_task', '')}`",
            f"Source files: {yaml_value(record.get('source_files', []))}",
            "",
        ])
