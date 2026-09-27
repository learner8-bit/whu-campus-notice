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
    # Cross-channel metadata. Website adapters can keep the defaults; WeChat
    # adapters populate these fields so storage, filtering and delivery remain
    # independent from the discovery mechanism.
    channel: str = "website"
    publisher_id: str = ""
    publisher_name: str = ""
    discovered_at: str = ""
    canonical_url: str = ""
    wechat_article_key: str = ""
    discovery_provider: str = ""
    content_quality: str = ""
    related_web_source_ids: list[str] = field(default_factory=list)
    delivery_not_before: str = ""
    # Runtime AI result. Cached separately so prompt/model changes do not make
    # an unchanged source notice look updated.
    ai_analysis: dict = field(default_factory=dict)

    @property
    def notice_id(self) -> str:
        identity = self.wechat_article_key or self.canonical_url or self.url
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        stable = self.to_dict()
        stable.pop("fetched_at", None)
        stable.pop("discovered_at", None)
        stable.pop("discovery_provider", None)
        stable.pop("fetch_error", None)
        stable.pop("ai_analysis", None)
        payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Notice":
        # SQLite rows written by newer versions may be opened by a checkout
        # whose dataclass has fewer fields, and vice versa. Ignore unknown keys
        # while allowing newly added optional fields to use their defaults.
        allowed = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in allowed})

