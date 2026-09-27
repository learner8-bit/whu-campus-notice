from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import Notice


@dataclass
class AdapterCollectionResult:
    notices: list[Notice] = field(default_factory=list)
    cursor: dict[str, Any] = field(default_factory=dict)
    status: str = "healthy"
    errors: list[str] = field(default_factory=list)


class SourceAdapter(Protocol):
    """Site-independent collection contract used by websites and WeChat."""

    def collect(self, cursor: dict[str, Any]) -> AdapterCollectionResult:
        ...
