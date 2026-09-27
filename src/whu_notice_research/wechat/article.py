from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .identity import canonicalize_wechat_url
from .models import WechatArticle


BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "Chrome/125.0 Mobile Safari/537.36 MicroMessenger/8.0"
)
CHALLENGE_MARKERS = ("当前环境异常", "完成验证后即可继续访问", "访问过于频繁", "操作频繁")
ATTACHMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv", ".txt")


class WechatContentBlocked(RuntimeError):
    pass


def _published_date(soup: BeautifulSoup, source: str) -> str:
    node = soup.select_one("#publish_time, em#publish_time")
    if node:
        text = node.get_text(" ", strip=True)
        match = re.search(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}", text)
        if match:
            parts = [int(item) for item in re.findall(r"\d+", match.group(0))]
            return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
    for pattern in (r"(?:create_time|publish_time)\s*[:=]\s*['\"]?(\d{10})", r"ct\s*[:=]\s*['\"](\d{10})"):
        match = re.search(pattern, source)
        if match:
            return datetime.fromtimestamp(int(match.group(1))).date().isoformat()
    return ""


def parse_article_html(
    source: str,
    url: str,
    *,
    publisher_id: str,
    publisher_name: str,
    provider: str,
) -> WechatArticle:
    if any(marker in source for marker in CHALLENGE_MARKERS):
        raise WechatContentBlocked("公众号页面要求人工验证，系统不会尝试绕过")
    soup = BeautifulSoup(source, "html.parser")
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    title_node = soup.select_one("#activity-name") or soup.select_one("h1.rich_media_title")
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    title = (
        title_node.get_text(" ", strip=True) if title_node else
        str(meta_title.get("content") or "").strip() if meta_title else ""
    )
    if content is None:
        raise WechatContentBlocked("页面没有公众号正文，可能是特殊文章或访问受限")
    for node in content.select("script, style, iframe, object, embed, form"):
        node.decompose()
    links: list[dict[str, str]] = []
    attachments: list[dict[str, str]] = []
    for node in content.select("a[href]"):
        href = html_lib.unescape(str(node.get("href") or "").strip())
        if not href or href.lower().startswith(("javascript:", "data:")):
            continue
        resolved = urljoin(url, href)
        text = node.get_text(" ", strip=True) or "正文链接"
        row = {"text": text[:200], "url": resolved}
        if any(resolved.lower().split("?", 1)[0].endswith(ext) for ext in ATTACHMENT_EXTENSIONS):
            attachments.append(row)
        else:
            links.append(row)
    images: list[str] = []
    for node in content.select("img"):
        source_url = str(node.get("data-src") or node.get("data-original") or node.get("src") or "").strip()
        if source_url.startswith(("http://", "https://")) and source_url not in images:
            images.append(source_url)
    body = re.sub(r"\n{3,}", "\n\n", content.get_text("\n", strip=True)).strip()
    return WechatArticle(
        publisher_id=publisher_id,
        publisher_name=publisher_name,
        title=title,
        url=canonicalize_wechat_url(url),
        published_at=_published_date(soup, source),
        body_text=body[:60000],
        attachments=attachments[:20],
        links=links[:40],
        images=images[:30],
        provider=provider,
        content_quality="full_text" if len(body) >= 80 else "partial",
    )


def fetch_article(
    article: WechatArticle,
    *,
    timeout: float = 20.0,
    session: requests.Session | None = None,
) -> WechatArticle:
    client = session or requests.Session()
    response = client.get(
        article.url,
        timeout=timeout,
        allow_redirects=True,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://mp.weixin.qq.com/",
        },
    )
    if response.status_code == 429:
        raise WechatContentBlocked("公众号正文读取被限频")
    response.raise_for_status()
    parsed = parse_article_html(
        response.text,
        response.url,
        publisher_id=article.publisher_id,
        publisher_name=article.publisher_name,
        provider=article.provider,
    )
    parsed.title = parsed.title or article.title
    parsed.summary = article.summary
    parsed.published_at = parsed.published_at or article.published_at
    parsed.article_key = article.article_key
    parsed.raw = article.raw
    return parsed
