"""Config-driven collector for Wuhan University Visual SiteBuilder websites."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

from ..eis import canonicalize_url, clean_text, fetch_text, parse_detail_page
from ..models import Notice


FULL_DATE = re.compile(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}")
SHORT_DATE = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)")
SPLIT_DATE = re.compile(
    r'class="[^"]*\btop\b[^"]*"[^>]*>\s*(\d{1,2})\s*</div>'
    r'.{0,240}?class="[^"]*\bbottom\b[^"]*"[^>]*>'
    r'\s*(20\d{2})[/.](\d{1,2})\s*</div>',
    re.I | re.S,
)
ANCHOR = re.compile(
    r"<a\b(?P<before>[^>]*)href=[\"'](?P<href>[^\"']+)[\"']"
    r"(?P<after>[^>]*)>(?P<inner>.*?)</a>",
    re.I | re.S,
)


@dataclass
class Source:
    id: str
    name: str
    url: str
    include_url_regex: str
    site_id: str
    site_name: str
    allow_short_date: bool = False


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        key = (values.get("name") or values.get("property") or "").lower()
        if key:
            self.meta[key] = values.get("content", "")


def _without_tags(value: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", value))


def _clean_title(value: str) -> str:
    value = _without_tags(value)
    value = re.sub(r"^\d{1,2}[-/]\d{1,2}\s+20\d{2}\s+", "", value)
    value = re.sub(r"\s+20\d{2}[-./]\d{1,2}[-./]\d{1,2}\s*$", "", value)
    return value.strip(" \t\r\n|-")


def _nearest_date(page_html: str, start: int, end: int, *, allow_short: bool) -> str:
    window_start = max(0, start - 220)
    window_end = min(len(page_html), end + 260)
    center = (start + end) // 2
    candidates: list[tuple[int, str]] = []
    for match in FULL_DATE.finditer(page_html, window_start, window_end):
        candidates.append((abs((match.start() + match.end()) // 2 - center), match.group()))
    if candidates:
        return min(candidates)[1].replace(".", "-").replace("/", "-")
    split_candidates: list[tuple[int, str]] = []
    for match in SPLIT_DATE.finditer(page_html, window_start, window_end):
        day, year, month = match.groups()
        try:
            value = date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
        split_candidates.append((abs((match.start() + match.end()) // 2 - center), value))
    if split_candidates:
        return min(split_candidates)[1]
    if not allow_short:
        return ""
    short_candidates: list[tuple[int, int, int]] = []
    for match in SHORT_DATE.finditer(page_html, window_start, window_end):
        short_candidates.append(
            (abs((match.start() + match.end()) // 2 - center), int(match.group(1)), int(match.group(2)))
        )
    if not short_candidates:
        return ""
    _, month, day = min(short_candidates)
    today = date.today()
    try:
        result = date(today.year, month, day)
    except ValueError:
        return ""
    if result > today + timedelta(days=45):
        result = date(today.year - 1, month, day)
    return result.isoformat()


def parse_list_page(page_html: str, page_url: str, source: Source) -> list[Notice]:
    include = re.compile(source.include_url_regex, re.I)
    rows: list[Notice] = []
    seen: set[str] = set()
    for match in ANCHOR.finditer(page_html):
        raw_url = html.unescape(match.group("href"))
        absolute = canonicalize_url(urljoin(page_url, raw_url))
        if not include.search(absolute) or absolute in seen:
            continue
        attributes = match.group("before") + match.group("after")
        title_match = re.search(r"\btitle=[\"']([^\"']+)", attributes, re.I | re.S)
        title = _clean_title(html.unescape(title_match.group(1))) if title_match else ""
        inner = match.group("inner")
        heading_match = re.search(
            r'<(?:div|span|h\d)\b[^>]*class="[^"]*\bh\b[^"]*"[^>]*>'
            r'(.*?)</(?:div|span|h\d)>',
            inner,
            re.I | re.S,
        )
        inner_title = _clean_title(heading_match.group(1) if heading_match else inner)
        if not title or len(inner_title) > len(title):
            title = inner_title
        if not title or title in {"更多", "更多 +", "查看详情", "MORE", "MORE>"}:
            continue
        published_at = _nearest_date(
            page_html, match.start(), match.end(), allow_short=source.allow_short_date
        )
        if not published_at:
            continue
        seen.add(absolute)
        rows.append(
            Notice(
                site_id=source.site_id,
                site_name=source.site_name,
                source_id=source.id,
                source_name=source.name,
                published_at=published_at,
                title=title,
                url=absolute,
            )
        )
    return rows


def parse_generic_detail(page_html: str, page_url: str) -> dict:
    meta_parser = _MetaParser()
    meta_parser.feed(page_html)
    common = parse_detail_page(page_html, page_url)
    meta = meta_parser.meta
    meta_date = (meta.get("pubdate") or meta.get("publishdate") or "").split(" ", 1)[0]
    common.update(
        {
            "title": clean_text(meta.get("articletitle", "")) or common["title"],
            "column": clean_text(meta.get("columnname", "")) or common["column"],
            "summary": clean_text(meta.get("description", "")),
            "published_at": meta_date or common["published_at"],
        }
    )
    return common


def enrich_notice(notice: Notice) -> Notice:
    try:
        page_html, resolved_url = fetch_text(notice.url)
        detail = parse_generic_detail(page_html, resolved_url)
        notice.url = canonicalize_url(resolved_url)
        notice.detail_title = detail["title"]
        if detail["title"]:
            notice.title = detail["title"]
        notice.detail_column = detail["column"]
        notice.body_text = detail["body_text"]
        notice.summary = detail["summary"] or notice.summary
        notice.attachments = detail["attachments"]
        notice.links = detail["links"]
        if FULL_DATE.fullmatch(detail["published_at"]):
            notice.published_at = detail["published_at"].replace(".", "-").replace("/", "-")
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
    sources = list(sources)
    known_urls = known_urls or set()
    rows: list[Notice] = []
    seen: set[str] = set()
    source_errors: list[Exception] = []
    for source in sources:
        try:
            page_html, resolved_url = fetch_text(source.url)
            candidates = parse_list_page(page_html, resolved_url, source)
        except Exception as exc:
            source_errors.append(exc)
            continue
        for notice in candidates:
            if notice.published_at < cutoff.isoformat() or notice.url in seen:
                continue
            seen.add(notice.url)
            if incremental and notice.url in known_urls:
                continue
            rows.append(enrich_notice(notice))
    if not rows and source_errors and len(source_errors) == len(sources):
        raise source_errors[0]
    rows.sort(key=lambda item: (item.published_at, item.source_name, item.title), reverse=True)
    return rows
