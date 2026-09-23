from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass
class Notice:
    """Site-independent notice representation used by every adapter."""

    source_id: str
    source_name: str
    published_at: str
    title: str
    url: str
    summary: str = ""
    detail_title: str = ""
    detail_column: str = ""
    body_text: str = ""
    attachments: list[dict[str, str]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    fetched_at: str = ""
    fetch_error: str = ""
    site_id: str = "eis"
    site_name: str = "武汉大学电子信息学院"
    # Runtime AI result. Cached separately so prompt/model changes do not make
    # an unchanged source notice look updated.
    ai_analysis: dict = field(default_factory=dict)

    @property
    def notice_id(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        stable = self.to_dict()
        stable.pop("fetched_at", None)
        stable.pop("fetch_error", None)
        stable.pop("ai_analysis", None)
        payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Notice":
        return cls(**value)

