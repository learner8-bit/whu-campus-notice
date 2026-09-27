from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import Notice
from ..storage import NoticeStore, now_shanghai
from .article import WechatContentBlocked, fetch_article
from .identity import canonicalize_wechat_url, wechat_article_key
from .models import CollectionResult, ProviderResult, WechatAccount, WechatArticle
from .ocr import ocr_article_images
from .providers import MessageAlbumProvider, SogouProvider, WebsiteMirrorProvider, WeReadProvider
from .weread import load_credentials, save_credentials


SHANGHAI = ZoneInfo("Asia/Shanghai")


class WechatSourceAdapter:
    def __init__(self, *, project_root: Path, store: NoticeStore, days: int) -> None:
        self.project_root = project_root
        self.store = store
        self.days = days

    def collect(self, cursor: dict) -> CollectionResult:
        days = int(cursor.get("days") or self.days)
        return collect_wechat(project_root=self.project_root, store=self.store, days=days)


def load_accounts(path: Path) -> list[WechatAccount]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("accounts", payload) if isinstance(payload, dict) else payload
    accounts = [WechatAccount.from_mapping(item) for item in rows]
    invalid = [item.display_name or item.id for item in accounts if not item.id or not item.display_name]
    if invalid:
        raise ValueError("公众号配置缺少 id/display_name: " + "、".join(invalid))
    return accounts


def _provider_map(credentials_path: Path):
    providers = {
        "message_album": MessageAlbumProvider(),
        "sogou": SogouProvider(),
        "website_mirror": WebsiteMirrorProvider(),
    }
    if credentials_path.exists():
        credentials = load_credentials(credentials_path)
        providers["weread"] = WeReadProvider(
            credentials,
            persist_credentials=lambda value: save_credentials(credentials_path, value),
        )
    return providers


def _merge_articles(values: list[WechatArticle]) -> list[WechatArticle]:
    priority = {"weread": 3, "message_album": 2, "sogou": 1}
    merged: dict[str, WechatArticle] = {}
    for article in values:
        article.url = canonicalize_wechat_url(article.url)
        article.article_key = article.article_key or wechat_article_key(
            article.url,
            publisher_id=article.publisher_id,
            title=article.title,
            published_at=article.published_at,
        )
        current = merged.get(article.article_key)
        if current is None:
            merged[article.article_key] = article
            continue
        if priority.get(article.provider, 0) > priority.get(current.provider, 0):
            article.title = article.title or current.title
            article.published_at = article.published_at or current.published_at
            merged[article.article_key] = article
        else:
            current.title = current.title or article.title
            current.published_at = current.published_at or article.published_at
            current.url = current.url or article.url
    return list(merged.values())


def _to_notice(article: WechatArticle, account: WechatAccount) -> Notice:
    return Notice(
        source_id=account.id,
        source_name="公众号文章",
        published_at=article.published_at,
        title=article.title or "公众号文章（标题待从原文读取）",
        url=article.url,
        summary=article.summary,
        body_text=article.body_text,
        attachments=article.attachments,
        links=article.links,
        fetched_at=now_shanghai(),
        site_id="wechat",
        site_name=account.display_name,
        channel="wechat",
        publisher_id=account.id,
        publisher_name=account.display_name,
        discovered_at=now_shanghai(),
        canonical_url=canonicalize_wechat_url(article.url),
        wechat_article_key=article.article_key,
        discovery_provider=article.provider,
        content_quality=article.content_quality,
        related_web_source_ids=list(account.related_web_sources),
        delivery_not_before=account.shadow_until,
    )


def collect_wechat(
    *,
    project_root: Path,
    store: NoticeStore,
    days: int = 30,
) -> CollectionResult:
    registry = Path(os.getenv("WECHAT_ACCOUNTS_FILE", "").strip() or project_root / "config" / "wechat_accounts.json")
    credentials_path = Path(
        os.getenv("WECHAT_CREDENTIALS_FILE", "").strip()
        or project_root / "data" / "private" / "wechat_credentials.json"
    )
    today = datetime.now(SHANGHAI).date().isoformat()
    accounts = [
        item for item in load_accounts(registry)
        if item.enabled and item.verified and (not item.enabled_after or item.enabled_after <= today)
    ]
    if not accounts:
        return CollectionResult()
    providers = _provider_map(credentials_path)
    result = CollectionResult()
    threshold = (datetime.now(SHANGHAI).date() - timedelta(days=max(days, 1))).isoformat()
    all_articles: list[tuple[WechatAccount, WechatArticle]] = []
    for account in accounts:
        account_results: list[ProviderResult] = []
        for provider_name in account.providers:
            provider = providers.get(provider_name)
            if provider is None:
                status = "auth_expired" if provider_name == "weread" else "unavailable"
                provider_result = ProviderResult(
                    provider_name,
                    status=status,
                    error="微信读书凭证不存在" if provider_name == "weread" else "采集器不可用",
                )
            else:
                cursor = store.get_cursor(account.id, provider_name)
                provider_result = provider.sync(account, cursor)
                if provider_result.cursor:
                    store.save_cursor(account.id, provider_name, provider_result.cursor)
            failures = store.record_provider_health(
                account.id,
                provider_name,
                provider_result.status,
                error=provider_result.error,
            )
            # Only escalate a source after three consecutive failures. A
            # healthy alternate provider keeps coverage in degraded mode.
            if failures >= 3:
                result.degraded_accounts.append(
                    f"{account.display_name}/{provider_name}: {provider_result.error}"
                )
            account_results.append(provider_result)
            result.provider_results.append((account, provider_result))
        for article in _merge_articles(
            [item for provider_result in account_results for item in provider_result.articles]
        ):
            if article.published_at and article.published_at < threshold:
                continue
            all_articles.append((account, article))

    # Union all providers first. Exact WeChat identity wins over provider name.
    unique: dict[str, tuple[WechatAccount, WechatArticle]] = {}
    for account, article in all_articles:
        if not article.url:
            continue
        unique.setdefault(article.article_key, (account, article))
    for account, article in unique.values():
        if article.url:
            try:
                article = fetch_article(article)
            except WechatContentBlocked as exc:
                article.content_quality = "metadata"
                article.raw["content_error"] = str(exc)
            except Exception as exc:
                article.content_quality = "metadata"
                article.raw["content_error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        # Image-heavy posts frequently keep the deadline only in a poster.
        # Avoid OCR for ordinary long-form articles to conserve the free tier.
        if article.images and len(article.body_text) < 500:
            ocr_text, engine = ocr_article_images(article.images, store)
            if ocr_text:
                article.body_text = (article.body_text + "\n\n【海报 OCR】\n" + ocr_text).strip()
                article.content_quality = "full_text_ocr"
                article.raw["ocr_engine"] = engine
        notice = _to_notice(article, account)
        if article.raw.get("content_error"):
            notice.fetch_error = str(article.raw["content_error"])
        result.notices.append(notice)
    return result
