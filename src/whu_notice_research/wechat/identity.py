from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


TRACKING_KEYS = {
    "scene",
    "srcid",
    "sharer_shareinfo",
    "sharer_shareinfo_first",
    "from",
    "isappinstalled",
    "clicktime",
    "enterid",
}


def canonicalize_wechat_url(url: str) -> str:
    """Keep stable article identity parameters and discard share tracking."""
    value = str(url or "").strip().replace("&amp;", "&")
    parsed = urlparse(value)
    if (parsed.hostname or "").lower() != "mp.weixin.qq.com":
        return value
    params = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() not in TRACKING_KEYS:
            params.append((key, item))
    path = re.sub(r"/{2,}", "/", parsed.path or "/s")
    params.sort()
    return urlunparse(("https", "mp.weixin.qq.com", path, "", urlencode(params), ""))


def parse_article_identity(url: str) -> tuple[str, str, str]:
    """Return (__biz, mid, idx) when a long-form article URL exposes them."""
    parsed = urlparse(str(url or "").replace("&amp;", "&"))
    values = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return (
        str(values.get("__biz") or values.get("biz") or "").strip(),
        str(values.get("mid") or "").strip(),
        str(values.get("idx") or "").strip(),
    )


def parse_article_biz(url: str, source: str = "") -> str:
    """Extract and normalize the official-account identity from URL or HTML."""
    biz, _, _ = parse_article_identity(url)
    if biz:
        return biz
    patterns = (
        r"(?:window\.)?biz\s*=\s*['\"]([^'\"]+)['\"]",
        r"(?:window\.)?__biz\s*=\s*['\"]([^'\"]+)['\"]",
        r"(?:window\.)?msg_link\s*=\s*['\"](.+?)['\"]\s*;",
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.I | re.S)
        if not match:
            continue
        value = html.unescape(match.group(1)).replace(r"\/", "/")
        value = value.replace(r"\x26", "&").replace(r"\u0026", "&")
        nested, _, _ = parse_article_identity(value)
        candidate = nested or value
        if re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", candidate):
            return candidate
    return ""


def wechat_article_key(
    url: str,
    *,
    publisher_id: str = "",
    title: str = "",
    published_at: str = "",
    upstream_id: str = "",
) -> str:
    biz, mid, idx = parse_article_identity(url)
    if biz and mid and idx:
        return f"wechat:{biz}:{mid}:{idx}"
    canonical = canonicalize_wechat_url(url)
    if canonical and "/s" in urlparse(canonical).path:
        return "wechat-url:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if upstream_id:
        return f"wechat-upstream:{publisher_id}:{upstream_id}"
    fingerprint = "\x1f".join((publisher_id.strip(), title.strip(), published_at.strip()))
    return "wechat-fallback:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
