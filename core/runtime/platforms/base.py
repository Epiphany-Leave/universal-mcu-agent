"""Base types for platform adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict


class PlatformAdapter(ABC):
    name: str

    @abstractmethod
    def detect(self, root: Path) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validate(self, root: Path, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError
