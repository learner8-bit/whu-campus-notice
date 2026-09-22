from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

from ..eis import canonicalize_url, clean_text, fetch_text, parse_detail_page
from ..models import Notice


SITE_ID = "undergraduate_school"
SITE_NAME = "武汉大学本科生院"


@dataclass
class Source:
    id: str
    name: str
    url: str


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        attr = {key.lower(): value or "" for key, value in attrs}
        key = attr.get("name", "").lower()
        if key:
            self.meta[key] = attr.get("content", "")


def parse_list_page(page_html: str, page_url: str) -> tuple[list[dict[str, str]], str | None]:
    pattern = re.compile(
        r'<li[^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*'
        r'<span[^>]*>(.*?)</span>\s*<i[^>]*>(20\d{2}[-.]\d{1,2}[-.]\d{1,2})</i>\s*'
        r'</a>\s*</li>',
        re.I | re.S,
    )
    items = []
    for match in pattern.finditer(page_html):
        items.append(
            {
                "url": urljoin(page_url, html.unescape(match.group(1))),
                "title": clean_text(re.sub(r"<[^>]+>", " ", match.group(2))),
                "date": match.group(3).replace(".", "-"),
                "summary": "",
            }
        )
    next_match = re.search(
        r'<span[^>]+class=["\'][^"\']*p_next[^"\']*["\'][^>]*>\s*'
        r'<a[^>]+href=["\']([^"\']+)["\']',
        page_html,
        re.I | re.S,
    )
    next_url = urljoin(page_url, html.unescape(next_match.group(1))) if next_match else None
    return items, next_url


def parse_uc_detail_page(page_html: str, page_url: str) -> dict:
    meta_parser = _MetaParser()
    meta_parser.feed(page_html)
    common = parse_detail_page(page_html, page_url)
    pub_date = meta_parser.meta.get("pubdate", "").split(" ", 1)[0]
    common.update(
        {
            "title": clean_text(meta_parser.meta.get("articletitle", "")) or common["title"],
            "column": clean_text(meta_parser.meta.get("columnname", "")) or common["column"],
            "published_at": pub_date or common["published_at"],
            "summary": clean_text(meta_parser.meta.get("description", "")),
        }
    )
    return common


def discover_source(
    source: Source,
    cutoff: date,
    *,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    notices: list[Notice] = []
    page_url: str | None = source.url
    visited: set[str] = set()
    known_urls = known_urls or set()
    while page_url and page_url not in visited:
        visited.add(page_url)
        page_html, resolved_page_url = fetch_text(page_url)
        items, next_url = parse_list_page(page_html, resolved_page_url)
        for item in items:
            try:
                item_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if item_date < cutoff:
                continue
            canonical = canonicalize_url(item["url"])
            if incremental and canonical in known_urls:
                continue
            notices.append(
                Notice(
                    site_id=SITE_ID,
                    site_name=SITE_NAME,
                    source_id=source.id,
                    source_name=source.name,
                    published_at=item_date.isoformat(),
                    title=item["title"],
                    url=canonical,
                    summary="",
                )
            )
        dated = [item["date"] for item in items if re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", item["date"])]
        if dated and max(datetime.strptime(value, "%Y-%m-%d").date() for value in dated) < cutoff:
            break
        page_url = next_url
    return notices


def enrich_notice(notice: Notice) -> Notice:
    try:
        page_html, resolved_url = fetch_text(notice.url)
        detail = parse_uc_detail_page(page_html, resolved_url)
        notice.url = canonicalize_url(resolved_url)
        notice.detail_title = detail["title"]
        notice.detail_column = detail["column"]
        notice.body_text = detail["body_text"]
        notice.summary = detail["summary"] or notice.summary
        notice.attachments = detail["attachments"]
        notice.links = detail["links"]
        if detail["published_at"]:
            notice.published_at = detail["published_at"]
    except Exception as exc:
        notice.fetch_error = f"{type(exc).__name__}: {exc}"
    notice.fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return notice


def collect_all(
    sources: Iterable[Source],
    cutoff: date,
    *,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    rows: list[Notice] = []
    seen: set[str] = set()
    for source in sources:
        for notice in discover_source(
            source,
            cutoff,
            known_urls=known_urls,
            incremental=incremental,
        ):
            if notice.url in seen:
                continue
            seen.add(notice.url)
            rows.append(enrich_notice(notice))
    rows.sort(key=lambda item: (item.published_at, item.source_name, item.title), reverse=True)
    return rows
