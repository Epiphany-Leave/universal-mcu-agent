"""Bound evidence review loops by input fingerprint and round count."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable


def artifact_fingerprint(artifacts: Iterable[Dict[str, Any]]) -> str:
    identity = [
        {
            "id": item.get("id"),
            "identity": item.get("identity"),
            "revision": item.get("revision"),
            "sha256": item.get("sha256"),
        }
        for item in artifacts
    ]
    encoded = json.dumps(sorted(identity, key=lambda item: str(item["id"])), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ReviewBudget:
    def __init__(self, ledger: Dict[str, Any] | None = None):
        self.ledger = ledger if ledger is not None else {"schema_version": "2.0", "reviews": {}}

    def request(
        self,
        phase_id: str,
        artifacts: Iterable[Dict[str, Any]],
        *,
        max_rounds: int = 2,
        previous_conclusion: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        fingerprint = artifact_fingerprint(artifacts)
        record = self.ledger["reviews"].setdefault(phase_id, {"rounds": 0, "fingerprints": [], "conclusion": None})

        if fingerprint in record["fingerprints"]:
            if previous_conclusion is not None:
                record["conclusion"] = previous_conclusion
                return {
                    "action": "CONCLUSION_RECORDED",
                    "status": previous_conclusion.get("status"),
                    "fingerprint": fingerprint,
                    "rounds": record["rounds"],
                    "conclusion": previous_conclusion,
                }
            return {
                "action": "RETURN_EXISTING",
                "status": "WAITING_EXTERNAL",
                "message": "No new evidence detected.",
                "fingerprint": fingerprint,
                "rounds": record["rounds"],
                "conclusion": record.get("conclusion"),
            }
        if record["rounds"] >= max_rounds:
            return {
                "action": "CLOSE_WITH_DEBT",
                "status": "COMPLETED",
                "result": "PASS_WITH_DEBT",
                "fingerprint": fingerprint,
                "rounds": record["rounds"],
            }

        record["rounds"] += 1
        record["fingerprints"].append(fingerprint)
        if previous_conclusion is not None:
            record["conclusion"] = previous_conclusion
        return {
            "action": "REVIEW_ALLOWED",
            "status": "ACTIVE",
            "fingerprint": fingerprint,
            "rounds": record["rounds"],
            "remaining": max_rounds - record["rounds"],
        }
