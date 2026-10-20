"""JSON Schema validation at runtime persistence boundaries."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ContractValidationError(ValueError):
    pass


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"cannot load runtime schema {schema_name}: {exc}") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_contract(value: Any, schema_name: str, *, label: str | None = None) -> None:
    """Raise one deterministic error containing every schema violation."""
    errors = sorted(
        _validator(schema_name).iter_errors(value),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if not errors:
        return
    details = []
    for error in errors[:20]:
        location = ".".join(str(item) for item in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    prefix = label or schema_name
    raise ContractValidationError(f"{prefix} violates its JSON Schema: " + "; ".join(details))
