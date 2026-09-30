from __future__ import annotations

import html as html_lib
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from .identity import canonicalize_wechat_url, parse_article_biz, wechat_article_key
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
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        # Reuse one client across accounts so the upstream two-second request
        # interval applies globally within a collection run.
        self.client = WeReadMobileClient(credentials)

    @staticmethod
    def _cooldown(cursor: dict, exc: Exception) -> ProviderResult:
        retry_after = (datetime.now(SHANGHAI).date() + timedelta(days=1)).isoformat()
        return ProviderResult(
            "weread",
            status="rate_limited",
            error=f"微信读书要求动态校验（{exc}），已冷却至次日",
            cursor={**cursor, "retry_after": retry_after, "last_error": str(exc)},
        )

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        today = datetime.now(SHANGHAI).date().isoformat()
        if str(cursor.get("retry_after") or "") > today:
            return ProviderResult(
                self.name,
                status="rate_limited",
                error=str(cursor.get("last_error") or "微信读书文章列表处于自动冷却期"),
                cursor=cursor,
                attempted=False,
            )
        try:
            articles = self.client.get_articles(
                account, count=30, synckey=int(cursor.get("synckey") or 0)
            )
        except WeReadAuthExpired:
            try:
                self.credentials = WeReadAuthClient().refresh(self.credentials)
                self.persist_credentials(self.credentials)
                self.client = WeReadMobileClient(self.credentials)
                articles = self.client.get_articles(account, count=30)
            except WeReadAuthExpired as exc:
                return ProviderResult(self.name, status="auth_expired", error=str(exc))
            except WeReadRateLimited as exc:
                return self._cooldown(cursor, exc)
            except Exception as exc:
                return ProviderResult(self.name, status="unavailable", error=str(exc))
        except WeReadRateLimited as exc:
            # -2041 is currently also used when WeRead requires its dynamic
            # web proof. Repeated requests cannot repair it and may increase
            # risk control, so do not try this provider again on the same day.
            return self._cooldown(cursor, exc)
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
        today = datetime.now(SHANGHAI).date().isoformat()
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

    @staticmethod
    def _is_challenge(source: str) -> bool:
        lowered = source.casefold()
        return (
            "antispider" in lowered
            or "请输入验证码" in source
            or "访问过于频繁" in source
            or "用户您好" in source
        )

    @staticmethod
    def _decode_js_string(value: str) -> str:
        value = html_lib.unescape(value).replace(r"\/", "/")
        replacements = {
            r"\x26": "&",
            r"\u0026": "&",
            r"\x3d": "=",
            r"\u003d": "=",
            r"\x3f": "?",
            r"\u003f": "?",
        }
        for escaped, decoded in replacements.items():
            value = value.replace(escaped, decoded)
        return value

    @classmethod
    def _redirect_from_script(cls, source: str) -> str:
        """Rebuild Sogou's short-lived WeChat URL without executing scripts."""
        # Current Sogou pages build the destination through a sequence such as
        # ``url += 'https://mp.weixin.qq.com'; url += '/s?...'``.
        # Try this before a direct URL match: the first literal is often only a
        # prefix and returning it early drops the timestamp/signature suffix.
        chunks = re.findall(
            r"(?:var\s+)?url\s*(?:=|\+=)\s*(['\"])(.*?)\1\s*;?",
            source,
            re.I | re.S,
        )
        if chunks:
            candidate = "".join(cls._decode_js_string(value) for _, value in chunks)
            if candidate.startswith("//mp.weixin.qq.com/"):
                candidate = "https:" + candidate
            if candidate.startswith("https://mp.weixin.qq.com/s"):
                return candidate

        direct = re.search(
            r"https?://mp\.weixin\.qq\.com/s[^\s\"'<>]+",
            source.replace(r"\/", "/"),
            re.I,
        )
        if direct:
            return cls._decode_js_string(direct.group(0))

        assigned = re.search(
            r"(?:location(?:\.href)?|window\.location(?:\.href)?)\s*=\s*"
            r"(['\"])(https?://mp\.weixin\.qq\.com/s.*?)\1",
            source,
            re.I | re.S,
        )
        return cls._decode_js_string(assigned.group(2)) if assigned else ""

    @staticmethod
    def _search_rows(source: str, base_url: str, account: WechatAccount) -> list[dict[str, str]]:
        soup = BeautifulSoup(source, "html.parser")
        wanted = re.sub(r"\s+", "", account.display_name).casefold()
        rows: list[dict[str, str]] = []
        for block in soup.select("li[id^='sogou_vr_'], li"):
            node = block.select_one(".txt-box h3 a[href], h3 a[href]")
            author_node = block.select_one(".s-p .all-time-y2, span.all-time-y2")
            if node is None or author_node is None:
                continue
            author = re.sub(r"\s+", "", author_node.get_text(" ", strip=True)).casefold()
            if author != wanted:
                continue
            script_text = " ".join(item.get_text(" ", strip=True) for item in block.select("script"))
            timestamp = re.search(r"timeConvert\(['\"]?(\d{10})", script_text)
            href = urljoin(base_url, str(node.get("href") or "").strip())
            if not href.startswith(("http://", "https://")):
                continue
            rows.append(
                {
                    "title": node.get_text(" ", strip=True),
                    "summary": (
                        block.select_one("p.txt-info").get_text(" ", strip=True)
                        if block.select_one("p.txt-info")
                        else ""
                    ),
                    "published_at": (
                        datetime.fromtimestamp(int(timestamp.group(1)), SHANGHAI).date().isoformat()
                        if timestamp
                        else _date_from_text(block.get_text(" ", strip=True))
                    ),
                    "redirect_url": href,
                }
            )
        return rows

    @classmethod
    def _resolve_article(
        cls,
        session: requests.Session,
        row: dict[str, str],
        *,
        referer: str,
        account: WechatAccount,
    ) -> WechatArticle | None:
        response = session.get(
            row["redirect_url"],
            headers={**BROWSER_HEADERS, "Referer": referer},
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code == 429 or cls._is_challenge(response.text):
            raise WeReadRateLimited("搜狗文章跳转要求验证码或已限频")
        response.raise_for_status()
        final_url = response.url if "mp.weixin.qq.com/s" in response.url else ""
        if not final_url:
            final_url = cls._redirect_from_script(response.text)
        if "mp.weixin.qq.com/s" not in final_url:
            return None

        article_source = response.text if "mp.weixin.qq.com" in response.url else ""
        if not article_source or parse_article_biz(final_url, article_source) != account.biz:
            article_response = session.get(
                final_url,
                headers={**BROWSER_HEADERS, "Referer": "https://weixin.sogou.com/"},
                timeout=20,
                allow_redirects=True,
            )
            if article_response.status_code == 429 or cls._is_challenge(article_response.text):
                return None
            article_response.raise_for_status()
            final_url = article_response.url
            article_source = article_response.text
        if not account.biz or parse_article_biz(final_url, article_source) != account.biz:
            return None

        title = row["title"]
        day = row["published_at"]
        key = wechat_article_key(
            final_url,
            publisher_id=account.id,
            title=title,
            published_at=day,
        )
        return WechatArticle(
            account.id,
            account.display_name,
            title,
            canonicalize_wechat_url(final_url),
            published_at=day,
            summary=row["summary"],
            article_key=key,
            provider=cls.name,
        )

    def sync(self, account: WechatAccount, cursor: dict) -> ProviderResult:
        today = datetime.now(SHANGHAI).date().isoformat()
        if cursor.get("queried_on") == today:
            return ProviderResult(
                self.name,
                status=str(cursor.get("last_status") or "degraded"),
                error=str(cursor.get("last_error") or "当天已完成低频查询"),
                cursor=cursor,
                attempted=False,
            )
        url = "https://weixin.sogou.com/weixin?type=2&ie=utf8&query=" + quote(account.display_name)
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        try:
            response = session.get(url, timeout=20)
            source = response.text
            if response.status_code == 429 or self._is_challenge(source):
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
            rows = self._search_rows(source, response.url, account)[:10]
            cutoff = (datetime.now(SHANGHAI).date() - timedelta(days=30)).isoformat()
            rows = [row for row in rows if not row["published_at"] or row["published_at"] >= cutoff]
            if not rows:
                previous = [str(item) for item in cursor.get("last_keys", [])]
                return ProviderResult(
                    self.name,
                    cursor={
                        "queried_on": today,
                        "last_keys": previous[:200],
                        "last_status": "healthy",
                        "last_error": "",
                    },
                )
            articles: list[WechatArticle] = []
            for row in rows:
                try:
                    article = self._resolve_article(
                        session,
                        row,
                        referer=response.url,
                        account=account,
                    )
                except WeReadRateLimited:
                    raise
                except requests.RequestException:
                    continue
                if article is not None:
                    articles.append(article)
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
        except WeReadRateLimited as exc:
            return ProviderResult(
                self.name,
                status="rate_limited",
                error=str(exc),
                cursor={
                    "queried_on": today,
                    "last_status": "rate_limited",
                    "last_error": str(exc),
                },
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
