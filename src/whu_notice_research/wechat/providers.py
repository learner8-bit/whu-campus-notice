from __future__ import annotations

import html as html_lib
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from .identity import canonicalize_wechat_url, wechat_article_key
from .models import ProviderResult, WechatAccount, WechatArticle
from .weread import (
    WeReadAuthClient,
    WeReadAuthExpired,
    WeReadCredentials,
    WeReadMobileClient,
    WeReadRateLimited,
    datetime_from_timestamp,
    encode_web_id,
)


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class WechatDiscoveryProvider(ABC):
    name: str

    @abstractmethod
    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        raise NotImplementedError


class WeReadProvider(WechatDiscoveryProvider):
    name = "weread"

    def __init__(self, credentials: WeReadCredentials, *, persist_credentials):
        self.credentials = credentials
        self.persist_credentials = persist_credentials

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        today = datetime.now().date().isoformat()
        if str(cursor.get("retry_after") or "") > today:
            return ProviderResult(
                self.name,
                status="rate_limited",
                error=str(cursor.get("last_error") or "微信读书文章列表处于自动冷却期"),
                cursor=cursor,
                attempted=False,
            )
        try:
            articles = WeReadMobileClient(self.credentials).get_articles(
                account, count=30, synckey=int(cursor.get("synckey") or 0)
            )
        except WeReadAuthExpired:
            try:
                self.credentials = WeReadAuthClient().refresh(self.credentials)
                self.persist_credentials(self.credentials)
                articles = WeReadMobileClient(self.credentials).get_articles(account, count=30)
            except WeReadAuthExpired as exc:
                return ProviderResult(self.name, status="auth_expired", error=str(exc))
            except Exception as exc:
                return ProviderResult(self.name, status="unavailable", error=str(exc))
        except WeReadRateLimited as exc:
            # -2041 is currently also used when WeRead requires its dynamic
            # web proof. Repeated requests cannot repair it and may increase
            # risk control, so do not try this provider again on the same day.
            retry_after = (datetime.now().date() + timedelta(days=1)).isoformat()
            return ProviderResult(
                self.name,
                status="rate_limited",
                error=f"微信读书要求动态校验（{exc}），已冷却至次日",
                cursor={**cursor, "retry_after": retry_after, "last_error": str(exc)},
            )
        except Exception as exc:
            return ProviderResult(self.name, status="unavailable", error=str(exc))
        previous = {str(item) for item in cursor.get("last_keys", [])}
        unseen = [item for item in articles if item.article_key not in previous]
        keys = list(dict.fromkeys([item.article_key for item in articles] + list(previous)))[:200]
        return ProviderResult(
            self.name,
            articles=unseen,
            cursor={"last_keys": keys},
        )


def _articles_from_weread_web(
    payload: dict, account: WechatAccount, provider: str = "weread_web"
) -> list[WechatArticle]:
    """Convert the current WeRead Web MP response into unified articles."""
    groups = payload.get("reviews") if isinstance(payload.get("reviews"), list) else []
    articles: list[WechatArticle] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        values = group.get("subReviews") if isinstance(group.get("subReviews"), list) else [group]
        for value in values:
            if not isinstance(value, dict):
                continue
            review = value.get("review") if isinstance(value.get("review"), dict) else value
            info = review.get("mpInfo") if isinstance(review.get("mpInfo"), dict) else {}
            review_id = str(review.get("reviewId") or value.get("reviewId") or "").strip()
            title = str(info.get("title") or review.get("title") or "").strip()
            if not review_id or not title:
                continue
            url = next(
                (
                    str(candidate)
                    for candidate in (
                        info.get("doc_url"), info.get("docUrl"), info.get("url"), review.get("url")
                    )
                    if isinstance(candidate, str) and candidate.startswith("http")
                ),
                "",
            )
            original = str(info.get("originalId") or "").strip()
            if not url and original.startswith("http"):
                url = original
            elif not url and original.startswith("/s"):
                url = "https://mp.weixin.qq.com" + original
            elif not url and original:
                url = "https://mp.weixin.qq.com/s/" + quote(original, safe="._~-")
            published = int(
                info.get("time") or review.get("createTime") or value.get("createTime") or 0
            )
            day = datetime_from_timestamp(published)
            article = WechatArticle(
                publisher_id=account.id,
                publisher_name=account.display_name,
                title=title,
                url=url,
                published_at=day,
                summary=str(info.get("content") or review.get("content") or ""),
                provider=provider,
                raw={"review_id": review_id, "book_id": account.book_id},
            )
            article.article_key = wechat_article_key(
                url,
                publisher_id=account.id,
                title=title,
                published_at=day,
                upstream_id=review_id,
            )
            articles.append(article)
    return articles


class WeReadWebProvider(WechatDiscoveryProvider):
    """Use the official WeRead web app to generate its dynamic request proof."""

    name = "weread_web"

    def __init__(self, state_path, *, timeout_ms: int = 35_000):
        self.state_path = str(state_path)
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_context(self):
        if self._context is not None:
            return self._context
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "").strip()
        launch_options = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        if executable:
            launch_options["executable_path"] = executable
        self._browser = self._playwright.chromium.launch(**launch_options)
        self._context = self._browser.new_context(storage_state=self.state_path)
        return self._context

    def close(self) -> None:
        if self._context is not None:
            self._context.storage_state(path=self.state_path)
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        today = datetime.now().date().isoformat()
        if cursor.get("queried_on") == today:
            return ProviderResult(
                self.name,
                status=str(cursor.get("last_status") or "degraded"),
                error=str(cursor.get("last_error") or "当天已完成网页会话采集"),
                cursor=cursor,
                attempted=False,
            )
        if not account.book_id:
            return ProviderResult(self.name, status="unavailable", error="缺少 book_id")
        try:
            context = self._ensure_context()
            page = context.new_page()
            try:
                with page.expect_response(
                    lambda response: "/web/mp/articles" in response.url,
                    timeout=self.timeout_ms,
                ) as response_info:
                    page.goto(
                        f"https://weread.qq.com/web/mp/reader/{encode_web_id(account.book_id)}",
                        wait_until="domcontentloaded",
                        timeout=self.timeout_ms,
                    )
                payload = response_info.value.json()
            finally:
                page.close()
            if not isinstance(payload, dict):
                raise RuntimeError("微信读书网页端返回格式异常")
            code = int(payload.get("errCode", payload.get("errcode", 0)) or 0)
            if code in {-2010, -2012}:
                status = "auth_expired"
                error = "微信读书网页授权已失效，需要重新扫码"
            elif code:
                status = "rate_limited" if code == -2041 else "unavailable"
                error = f"微信读书网页端错误 {code}"
            else:
                articles = _articles_from_weread_web(payload, account, self.name)
                previous = {str(item) for item in cursor.get("last_keys", [])}
                unseen = [item for item in articles if item.article_key not in previous]
                keys = list(dict.fromkeys([item.article_key for item in articles] + list(previous)))[:200]
                status = "healthy" if articles else "degraded"
                error = "" if articles else "网页会话成功，但未解析到文章"
                return ProviderResult(
                    self.name,
                    articles=unseen,
                    status=status,
                    error=error,
                    cursor={
                        "queried_on": today,
                        "last_keys": keys,
                        "last_status": status,
                        "last_error": error,
                    },
                )
            return ProviderResult(
                self.name,
                status=status,
                error=error,
                cursor={"queried_on": today, "last_status": status, "last_error": error},
            )
        except Exception as exc:
            status = "auth_expired" if "Timeout" in type(exc).__name__ else "unavailable"
            error = (
                "未捕获到文章列表；网页授权可能已失效"
                if status == "auth_expired"
                else f"{type(exc).__name__}: {str(exc)[:180]}"
            )
            return ProviderResult(
                self.name,
                status=status,
                error=error,
                cursor={"queried_on": today, "last_status": status, "last_error": error},
            )


def _date_from_text(value: str) -> str:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", value)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    timestamp = re.search(r"(?:date|time|timestamp)[^\d]{0,12}(1\d{9})", value, re.I)
    return datetime.fromtimestamp(int(timestamp.group(1))).date().isoformat() if timestamp else ""


def _articles_from_html(source: str, base_url: str, account: WechatAccount, provider: str) -> list[WechatArticle]:
    soup = BeautifulSoup(source, "html.parser")
    candidates: list[tuple[str, str, str]] = []
    for node in soup.select("a[href]"):
        href = html_lib.unescape(str(node.get("href") or ""))
        resolved = urljoin(base_url, href)
        if "mp.weixin.qq.com/s" not in resolved:
            continue
        title = node.get_text(" ", strip=True) or str(node.get("title") or "").strip()
        candidates.append((resolved, title, _date_from_text(node.parent.get_text(" ", strip=True))))
    # Album and Sogou pages sometimes store escaped article URLs only in script.
    normalized = source.replace(r"\/", "/").replace(r"\u0026", "&").replace("&amp;", "&")
    for match in re.finditer(r"https?://mp\.weixin\.qq\.com/s(?:\?[^\"'<>\s]+|/[A-Za-z0-9_-]+)", normalized):
        candidates.append((match.group(0), "", ""))
    articles: list[WechatArticle] = []
    seen: set[str] = set()
    for url, title, day in candidates:
        canonical = canonicalize_wechat_url(url)
        key = wechat_article_key(
            canonical,
            publisher_id=account.id,
            title=title,
            published_at=day,
        )
        if key in seen:
            continue
        seen.add(key)
        articles.append(
            WechatArticle(
                publisher_id=account.id,
                publisher_name=account.display_name,
                title=title,
                url=canonical,
                published_at=day,
                article_key=key,
                provider=provider,
            )
        )
    return articles


class MessageAlbumProvider(WechatDiscoveryProvider):
    name = "message_album"

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        if not account.album_url:
            # This provider is optional and only applies to accounts that have
            # published a public collection page.
            return ProviderResult(
                self.name,
                status="unavailable",
                error="该公众号未配置公开合集页",
                attempted=False,
            )
        try:
            response = requests.get(account.album_url, headers=BROWSER_HEADERS, timeout=20)
            if response.status_code == 429:
                return ProviderResult(self.name, status="rate_limited", error="HTTP 429")
            response.raise_for_status()
            articles = _articles_from_html(response.text, response.url, account, self.name)
            if not articles:
                return ProviderResult(self.name, status="degraded", error="合集页未解析到文章")
            previous = {str(item) for item in cursor.get("last_keys", [])}
            unseen = [item for item in articles if item.article_key not in previous]
            keys = list(dict.fromkeys([item.article_key for item in articles] + list(previous)))[:200]
            return ProviderResult(
                self.name,
                articles=unseen,
                cursor={"last_keys": keys},
            )
        except Exception as exc:
            return ProviderResult(self.name, status="unavailable", error=str(exc))


class WebsiteMirrorProvider(WechatDiscoveryProvider):
    """Marks the separately collected official website as an active fallback."""

    name = "website_mirror"

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        if not account.related_web_sources:
            return ProviderResult(self.name, status="degraded", error="未关联官网来源")
        return ProviderResult(
            self.name,
            cursor={"related_web_sources": list(account.related_web_sources)},
        )


class SogouProvider(WechatDiscoveryProvider):
    name = "sogou"

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        today = datetime.now().date().isoformat()
        if cursor.get("queried_on") == today:
            return ProviderResult(
                self.name,
                status=str(cursor.get("last_status") or "degraded"),
                error=str(cursor.get("last_error") or "当天已完成低频查询"),
                cursor=cursor,
                attempted=False,
            )
        url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(account.display_name)
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
            text = response.text
            if response.status_code == 429 or "请输入验证码" in text or "访问过于频繁" in text:
                return ProviderResult(
                    self.name,
                    status="rate_limited",
                    error="搜狗要求验证码或已限频；当天不再重试",
                    cursor={
                        "queried_on": today,
                        "last_status": "rate_limited",
                        "last_error": "搜狗要求验证码或已限频；当天不再重试",
                    },
                )
            response.raise_for_status()
            articles = _articles_from_html(text, response.url, account, self.name)
            if not articles:
                soup = BeautifulSoup(text, "html.parser")
                for node in soup.select(".txt-box h3 a[href], h3 a[href]")[:10]:
                    candidate = str(node.get("href") or "")
                    if candidate.startswith("/"):
                        candidate = "https://weixin.sogou.com" + candidate
                    if not candidate.startswith("http"):
                        continue
                    try:
                        resolved = requests.get(
                            candidate,
                            headers={**BROWSER_HEADERS, "Referer": response.url},
                            timeout=15,
                            allow_redirects=True,
                        )
                    except requests.RequestException:
                        continue
                    final_url = resolved.url
                    if "mp.weixin.qq.com/s" not in final_url:
                        match = re.search(
                            r"(?:url|location\.href)\s*=\s*['\"](https?://mp\.weixin\.qq\.com/s[^'\"]+)",
                            resolved.text.replace(r"\/", "/").replace(r"\u0026", "&"),
                        )
                        final_url = html_lib.unescape(match.group(1)) if match else ""
                    if "mp.weixin.qq.com/s" not in final_url:
                        continue
                    title = node.get_text(" ", strip=True)
                    day = _date_from_text(node.parent.parent.get_text(" ", strip=True))
                    key = wechat_article_key(
                        final_url,
                        publisher_id=account.id,
                        title=title,
                        published_at=day,
                    )
                    articles.append(
                        WechatArticle(
                            account.id,
                            account.display_name,
                            title,
                            canonicalize_wechat_url(final_url),
                            published_at=day,
                            article_key=key,
                            provider=self.name,
                        )
                    )
            previous = {str(item) for item in cursor.get("last_keys", [])}
            unseen = [item for item in articles if item.article_key not in previous]
            keys = list(dict.fromkeys([item.article_key for item in articles] + list(previous)))[:200]
            return ProviderResult(
                self.name,
                articles=unseen,
                cursor={
                    "queried_on": today,
                    "last_keys": keys,
                    "last_status": "healthy" if articles else "degraded",
                    "last_error": "" if articles else "搜索结果没有可验证文章",
                },
                status="healthy" if articles else "degraded",
                error="" if articles else "搜索结果没有可验证文章",
            )
        except Exception as exc:
            return ProviderResult(
                self.name,
                status="unavailable",
                error=str(exc),
                cursor={
                    "queried_on": today,
                    "last_status": "unavailable",
                    "last_error": str(exc),
                },
            )
