from __future__ import annotations

import html as html_lib
import re
from abc import ABC, abstractmethod
from datetime import datetime
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
            return ProviderResult(self.name, status="rate_limited", error=str(exc))
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
            return ProviderResult(self.name)
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
            return ProviderResult(self.name, cursor=cursor)
        url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(account.display_name)
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
            text = response.text
            if response.status_code == 429 or "请输入验证码" in text or "访问过于频繁" in text:
                return ProviderResult(
                    self.name,
                    status="rate_limited",
                    error="搜狗要求验证码或已限频；当天不再重试",
                    cursor={"queried_on": today},
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
                cursor={"queried_on": today, "last_keys": keys},
                status="healthy" if articles else "degraded",
                error="" if articles else "搜索结果没有可验证文章",
            )
        except Exception as exc:
            return ProviderResult(
                self.name,
                status="unavailable",
                error=str(exc),
                cursor={"queried_on": today},
            )
