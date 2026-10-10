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
from .providers import (
    MessageAlbumProvider,
    SogouProvider,
    WebsiteMirrorProvider,
    WeReadProvider,
    WeReadWebProvider,
)
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


def _provider_map(credentials_path: Path, web_state_path: Path):
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
    if web_state_path.exists():
        providers["weread_web"] = WeReadWebProvider(web_state_path)
    return providers


def _merge_articles(values: list[WechatArticle]) -> list[WechatArticle]:
    priority = {"weread_web": 4, "weread": 3, "message_album": 2, "sogou": 1}
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
            _merge_content(article, current)
            merged[article.article_key] = article
        else:
            _merge_content(current, article)
    return list(merged.values())


def _merge_content(target: WechatArticle, other: WechatArticle) -> None:
    for name in ("title", "published_at", "url", "summary"):
        setattr(target, name, getattr(target, name) or getattr(other, name))
    if len(other.body_text) > len(target.body_text):
        target.body_text = other.body_text
        target.content_quality = other.content_quality
    for name in ("attachments", "links", "images"):
        rows = getattr(target, name)
        rows.extend(item for item in getattr(other, name) if item not in rows)


def _pending_articles(store: NoticeStore, accounts: list[WechatAccount], today: str):
    by_id = {account.id: account for account in accounts}
    for notice in store.pending_wechat_content(today):
        account = by_id.get(notice.publisher_id or notice.source_id)
        if account:
            yield account, WechatArticle(
                publisher_id=account.id, publisher_name=account.display_name,
                title=notice.title, url=notice.url, published_at=notice.published_at,
                summary=notice.summary, body_text=notice.body_text,
                attachments=notice.attachments, links=notice.links,
                article_key=notice.wechat_article_key, provider=notice.discovery_provider,
                content_quality=notice.content_quality,
            )


def _read_content(article: WechatArticle, account: WechatAccount, store: NoticeStore) -> Notice:
    try:
        if article.content_quality not in {"full_text", "full_text_ocr"}:
            article = fetch_article(article)
    except WechatContentBlocked as exc:
        article.raw["content_error"] = str(exc)
    except Exception as exc:
        # No raw request/response in health reports: upstream URLs may contain tokens.
        article.raw["content_error"] = f"{type(exc).__name__}: 公众号正文读取失败"
    if article.images and len(article.body_text) < 500:
        try:
            ocr_text, engine = ocr_article_images(article.images, store)
            if ocr_text:
                article.body_text = (article.body_text + "\n\n【海报 OCR】\n" + ocr_text).strip()
                article.content_quality = "full_text_ocr"
                article.raw["ocr_engine"] = engine
        except Exception as exc:
            article.raw["content_error"] = f"{type(exc).__name__}: 海报 OCR 失败"
    if article.content_quality in {"metadata", "partial"}:
        article.raw.setdefault("content_error", "公众号正文不完整，关键条件可能缺失")
    notice = _to_notice(article, account)
    notice.fetch_error = str(article.raw.get("content_error") or "")
    return notice


def retry_wechat_content(*, project_root: Path, store: NoticeStore) -> list[Notice]:
    """Repair pending bodies without requerying any article-discovery provider."""
    today = datetime.now(SHANGHAI).date().isoformat()
    registry = Path(os.getenv("WECHAT_ACCOUNTS_FILE", "").strip() or project_root / "config" / "wechat_accounts.json")
    accounts = [a for a in load_accounts(registry) if a.enabled and a.verified]
    repaired = []
    for account, article in _pending_articles(store, accounts, today):
        notice = _read_content(article, account, store)
        store.record_wechat_content_attempt(notice, today, recovered=not bool(notice.fetch_error))
        repaired.append(notice)
    return repaired


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
    web_state_path = Path(
        os.getenv("WECHAT_WEB_STATE_FILE", "").strip()
        or project_root / "data" / "private" / "weread_web_state.json"
    )
    today = datetime.now(SHANGHAI).date().isoformat()
    accounts = [
        item for item in load_accounts(registry)
        if item.enabled and item.verified and (not item.enabled_after or item.enabled_after <= today)
    ]
    if not accounts:
        return CollectionResult()
    providers = _provider_map(credentials_path, web_state_path)
    result = CollectionResult()
    threshold = (datetime.now(SHANGHAI).date() - timedelta(days=max(days, 1))).isoformat()
    all_articles: list[tuple[WechatAccount, WechatArticle]] = []
    for account in accounts:
        account_results: list[ProviderResult] = []
        for provider_name in account.providers:
            provider = providers.get(provider_name)
            if provider is None:
                status = "auth_expired" if provider_name in {"weread", "weread_web"} else "unavailable"
                provider_result = ProviderResult(
                    provider_name,
                    status=status,
                    error=(
                        "微信读书凭证不存在"
                        if provider_name in {"weread", "weread_web"}
                        else "采集器不可用"
                    ),
                )
            else:
                cursor = store.get_cursor(account.id, provider_name)
                provider_result = provider.sync(account, cursor)
                if provider_result.cursor:
                    store.save_cursor(account.id, provider_name, provider_result.cursor)
            failures = 0
            if provider_result.attempted:
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

    for provider in providers.values():
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    # Union all providers first. Exact WeChat identity wins over provider name.
    unique: dict[str, tuple[WechatAccount, WechatArticle]] = {}
    for account, article in all_articles:
        if not article.url:
            continue
        unique.setdefault(article.article_key, (account, article))
    pending = list(_pending_articles(store, accounts, today))
    retry_keys = {article.article_key for _, article in pending}
    for account, article in pending:
        unique.setdefault(article.article_key, (account, article))
    for account, article in unique.values():
        identity = _to_notice(article, account).notice_id
        previous = store.notice_by_id(identity)
        if previous and (
            not store.wechat_content_attempt_allowed(identity, today)
            or (previous.content_quality in {"full_text", "full_text_ocr"} and not previous.fetch_error)
        ):
            # Another provider can rediscover the same URL after its discovery
            # cursor advances. It must not bypass the content retry budget or
            # overwrite an already complete article with a metadata-only row.
            result.notices.append(previous)
            continue
        notice = _read_content(article, account, store)
        store.record_wechat_content_attempt(
            notice, today, recovered=article.article_key in retry_keys and not notice.fetch_error,
        )
        result.notices.append(notice)
    return result
