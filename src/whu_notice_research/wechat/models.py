from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..source_adapter import AdapterCollectionResult


HEALTH_STATES = {"healthy", "degraded", "auth_expired", "rate_limited", "unavailable"}


@dataclass(frozen=True)
class WechatAccount:
    id: str
    display_name: str
    verified_by: str = ""
    wechat_id: str = ""
    biz: str = ""
    book_id: str = ""
    seed_url: str = ""
    album_url: str = ""
    providers: tuple[str, ...] = ("weread", "message_album", "sogou", "website_mirror")
    related_web_sources: tuple[str, ...] = ()
    enabled: bool = False
    verified: bool = False
    rollout_rank: int = 999
    enabled_after: str = ""
    shadow_until: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "WechatAccount":
        return cls(
            id=str(value.get("id") or "").strip(),
            display_name=str(value.get("display_name") or "").strip(),
            verified_by=str(value.get("verified_by") or "").strip(),
            wechat_id=str(value.get("wechat_id") or "").strip(),
            biz=str(value.get("biz") or "").strip(),
            book_id=str(value.get("book_id") or "").strip(),
            seed_url=str(value.get("seed_url") or "").strip(),
            album_url=str(value.get("album_url") or "").strip(),
            providers=tuple(str(item) for item in value.get("providers", [])),
            related_web_sources=tuple(str(item) for item in value.get("related_web_sources", [])),
            enabled=bool(value.get("enabled", False)),
            verified=bool(value.get("verified", False)),
            rollout_rank=int(value.get("rollout_rank") or 999),
            enabled_after=str(value.get("enabled_after") or "").strip(),
            shadow_until=str(value.get("shadow_until") or "").strip(),
        )


@dataclass
class WechatArticle:
    publisher_id: str
    publisher_name: str
    title: str
    url: str
    published_at: str = ""
    summary: str = ""
    article_key: str = ""
    provider: str = ""
    body_text: str = ""
    attachments: list[dict[str, str]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    content_quality: str = "metadata"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    provider: str
    articles: list[WechatArticle] = field(default_factory=list)
    cursor: dict[str, Any] = field(default_factory=dict)
    status: str = "healthy"
    error: str = ""
    # False means this provider was intentionally skipped (for example, a
    # once-per-day query already ran or the account has no public album).
    # Skips must not reset or increment consecutive health failures.
    attempted: bool = True

    def __post_init__(self) -> None:
        if self.status not in HEALTH_STATES:
            raise ValueError(f"invalid provider health state: {self.status}")


@dataclass
class CollectionResult(AdapterCollectionResult):
    provider_results: list[tuple[WechatAccount, ProviderResult]] = field(default_factory=list)
    degraded_accounts: list[str] = field(default_factory=list)
