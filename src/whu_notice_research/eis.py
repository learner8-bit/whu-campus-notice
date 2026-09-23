from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .models import Notice


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
    "WHU-notice-research/0.1"
)


class BlockedPageError(RuntimeError):
    """A site returned its verification page instead of the requested notice."""


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def canonicalize_url(value: str) -> str:
    parsed = urlparse(value)
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    return urlunparse((scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, ""))


@dataclass
class Source:
    id: str
    name: str
    url: str
    include_for_sampling: bool = True


class _ListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.next_href: str | None = None
        self._item: dict[str, str] | None = None
        self._li_depth = 0
        self._capture: str | None = None
        self._capture_parts: list[str] = []
        self._inside_title = False
        self._inside_description = False
        self._inside_next = False

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        if tag == "li" and attr.get("id", "").startswith("line_"):
            self._item = {"date": "", "title": "", "href": "", "summary": ""}
            self._li_depth = 1
            return
        if self._item is not None:
            if tag == "li":
                self._li_depth += 1
            if tag == "p" and "month" in classes:
                self._capture = "date"
                self._capture_parts = []
            elif tag == "h3" and "title" in classes:
                self._inside_title = True
            elif tag == "p" and "des" in classes:
                self._inside_description = True
            elif tag == "a" and self._inside_title:
                self._item["href"] = attr.get("href", "")
                self._item["title"] = attr.get("title", "")
                self._capture = "title"
                self._capture_parts = []
            elif tag == "a" and self._inside_description:
                self._capture = "summary"
                self._capture_parts = []
        if tag == "span" and "p_next" in classes:
            self._inside_next = True
        elif tag == "a" and self._inside_next and not self.next_href:
            self.next_href = attr.get("href") or None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is not None:
            if tag == "a" and self._capture in {"title", "summary"}:
                value = clean_text("".join(self._capture_parts))
                if value and not self._item.get(self._capture, ""):
                    self._item[self._capture] = value
                self._capture = None
                self._capture_parts = []
            elif tag == "p" and self._capture == "date":
                self._item["date"] = clean_text("".join(self._capture_parts))
                self._capture = None
                self._capture_parts = []
            elif tag == "h3" and self._inside_title:
                self._inside_title = False
            elif tag == "p" and self._inside_description:
                self._inside_description = False
            if tag == "li":
                self._li_depth -= 1
                if self._li_depth == 0:
                    if self._item.get("href") and self._item.get("title"):
                        self.items.append(self._item)
                    self._item = None
        if tag == "span" and self._inside_next:
            self._inside_next = False


class _DetailParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.column = ""
        self.body_text = ""
        self.links: list[dict[str, str]] = []
        self._capture: str | None = None
        self._parts: list[str] = []
        self._in_content = False
        self._content_depth = 0
        self._link: dict[str, str] | None = None
        self._link_parts: list[str] = []
        self._link_is_content = False
        self._ignored_tag: str | None = None

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        if not self._in_content and tag == "h3" and "title" in classes:
            self._capture, self._parts = "title", []
        elif not self._in_content and tag == "h3" and "chyg-column-h3" in classes:
            self._capture, self._parts = "column", []
        if tag == "div" and "v_news_content" in classes and not self._in_content:
            self._in_content = True
            self._content_depth = 1
            return
        if self._in_content:
            if tag in {"script", "style"}:
                self._ignored_tag = tag
                return
            if tag == "div":
                self._content_depth += 1
            if tag in {"p", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
                self._parts.append("\n")
        if tag == "a" and attr.get("href"):
            href = attr["href"]
            path = urlparse(href).path.lower()
            is_attachment = "download" in path or bool(
                re.search(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar)(?:$|\?)", href, re.I)
            )
            # Visual SiteBuilder sometimes renders attachment links immediately
            # after v_news_content instead of inside it, so attachments must be
            # observed across the detail page. Ordinary links stay content-only.
            if self._in_content or is_attachment:
                self._link = {"url": urljoin(self.base_url, href), "text": ""}
                self._link_parts = []
                self._link_is_content = self._in_content

    def handle_data(self, data: str) -> None:
        if (self._capture in {"title", "column"} or self._in_content) and not self._ignored_tag:
            self._parts.append(data)
        if self._link is not None:
            self._link_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_tag == tag:
            self._ignored_tag = None
            return
        if self._link is not None and tag == "a":
            self._link["text"] = clean_text("".join(self._link_parts))
            self.links.append(self._link)
            self._link = None
            self._link_parts = []
            self._link_is_content = False
        if self._capture == "title" and tag == "h3":
            self.title = clean_text("".join(self._parts))
            self._capture, self._parts = None, []
        elif self._capture == "column" and tag == "h3":
            self.column = clean_text("".join(self._parts))
            self._capture, self._parts = None, []
        if self._in_content and tag == "div":
            self._content_depth -= 1
            if self._content_depth == 0:
                self.body_text = clean_text("".join(self._parts))
                self._in_content = False
                self._parts = []


def fetch_text(url: str, *, timeout: float = 30.0, retries: int = 3) -> tuple[str, str]:
    error: Exception | None = None
    candidates = [url]
    parsed = urlparse(url)
    if parsed.scheme == "https":
        candidates.append(urlunparse(parsed._replace(scheme="http")))
    for candidate in candidates:
        for attempt in range(retries):
            request = Request(candidate, headers={"User-Agent": USER_AGENT})
            try:
                with urlopen(request, timeout=timeout) as response:
                    resolved_url = response.geturl()
                    if "/system/resource/visitcode/" in urlparse(resolved_url).path:
                        raise BlockedPageError(f"verification page: {resolved_url}")
                    raw = response.read()
                    header_encoding = response.headers.get_content_charset()
                    head = raw[:4096].decode("ascii", errors="ignore")
                    meta_match = re.search(
                        r"charset\s*=\s*[\"']?([\w-]+)", head, re.I
                    )
                    encodings = [
                        value
                        for value in (
                            meta_match.group(1) if meta_match else None,
                            header_encoding,
                            "utf-8",
                            "gb18030",
                        )
                        if value
                    ]
                    text = ""
                    for encoding in dict.fromkeys(encodings):
                        try:
                            text = raw.decode(encoding)
                            break
                        except (LookupError, UnicodeDecodeError):
                            continue
                    if not text:
                        text = raw.decode("utf-8", errors="replace")
                    return text, resolved_url
            except (HTTPError, URLError, TimeoutError) as exc:
                error = exc
                if attempt + 1 < retries:
                    time.sleep(0.5 * (attempt + 1))
    assert error is not None
    raise error


def parse_list_page(page_html: str, page_url: str) -> tuple[list[dict[str, str]], str | None]:
    parser = _ListParser()
    parser.feed(page_html)
    for item in parser.items:
        item["url"] = urljoin(page_url, item.pop("href"))
    next_url = urljoin(page_url, parser.next_href) if parser.next_href else None
    return parser.items, next_url


def parse_detail_page(page_html: str, page_url: str) -> dict:
    parser = _DetailParser(page_url)
    parser.feed(page_html)
    date_match = re.search(
        r'class=["\']time["\'][^>]*>.*?(20\d{2}[-./]\d{1,2}[-./]\d{1,2})',
        page_html,
        re.I | re.S,
    )
    attachments = []
    ordinary_links = []
    seen_links: set[str] = set()
    for link in parser.links:
        canonical_link = canonicalize_url(link["url"])
        if canonical_link in seen_links:
            continue
        seen_links.add(canonical_link)
        link = {**link, "url": canonical_link}
        path = urlparse(link["url"]).path.lower()
        is_attachment = "download" in path or bool(
            re.search(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar)(?:$|\?)", link["url"], re.I)
        )
        (attachments if is_attachment else ordinary_links).append(link)

    for match in re.finditer(r'showVsbpdfIframe\(\s*["\']([^"\']+\.pdf)["\']', page_html, re.I):
        pdf_url = canonicalize_url(urljoin(page_url, html.unescape(match.group(1))))
        if pdf_url in seen_links:
            continue
        seen_links.add(pdf_url)
        filename = unquote(urlparse(pdf_url).path.rsplit("/", 1)[-1]) or "embedded.pdf"
        attachments.append({"url": pdf_url, "text": filename})

    # The redesigned site renders some legacy and new download endpoints for
    # the same visible file. Prefer one entry per displayed attachment name.
    unique_attachments: list[dict[str, str]] = []
    seen_attachment_names: set[str] = set()
    for link in attachments:
        key = clean_text(link.get("text", "")).casefold() or link["url"]
        if key in seen_attachment_names:
            continue
        seen_attachment_names.add(key)
        unique_attachments.append(link)
    return {
        "title": parser.title,
        "column": parser.column,
        "published_at": date_match.group(1).replace(".", "-").replace("/", "-")
        if date_match
        else "",
        "body_text": parser.body_text,
        "attachments": unique_attachments,
        "links": ordinary_links,
    }


def load_sources(path: Path) -> list[Source]:
    with path.open("r", encoding="utf-8") as handle:
        return [Source(**row) for row in json.load(handle)]


def collect_source(
    source: Source,
    cutoff: date,
    *,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    notices: list[Notice] = []
    page_url: str | None = source.url
    visited_pages: set[str] = set()
    known_urls = known_urls or set()
    while page_url and page_url not in visited_pages:
        visited_pages.add(page_url)
        page_html, resolved_page_url = fetch_text(page_url)
        items, next_url = parse_list_page(page_html, resolved_page_url)
        dated_items = []
        for item in items:
            try:
                item_date = datetime.strptime(item["date"], "%Y.%m.%d").date()
            except ValueError:
                continue
            dated_items.append((item_date, item))
            if item_date >= cutoff:
                canonical = canonicalize_url(item["url"])
                if incremental and canonical in known_urls:
                    continue
                notices.append(
                    Notice(
                        source_id=source.id,
                        source_name=source.name,
                        published_at=item_date.isoformat(),
                        title=item["title"],
                        url=canonical,
                        summary=item["summary"],
                        site_id="eis",
                        site_name="武汉大学电子信息学院",
                    )
                )
        if dated_items and max(item_date for item_date, _ in dated_items) < cutoff:
            break
        page_url = next_url
    return notices


def enrich_notice(notice: Notice) -> Notice:
    try:
        page_html, resolved_url = fetch_text(notice.url)
        detail = parse_detail_page(page_html, resolved_url)
        notice.url = canonicalize_url(resolved_url)
        notice.detail_title = detail["title"]
        notice.detail_column = detail["column"]
        notice.body_text = detail["body_text"]
        notice.attachments = detail["attachments"]
        notice.links = detail["links"]
        if detail["published_at"]:
            notice.published_at = detail["published_at"]
    except Exception as exc:  # keep list-level data for later review
        notice.fetch_error = f"{type(exc).__name__}: {exc}"
    notice.fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return notice


def collect_all(
    sources: Iterable[Source],
    days: int,
    *,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    cutoff = date.today() - timedelta(days=days)
    rows: list[Notice] = []
    seen: set[str] = set()
    for source in sources:
        if not source.include_for_sampling:
            continue
        for notice in collect_source(
            source,
            cutoff,
            known_urls=known_urls,
            incremental=incremental,
        ):
            key = canonicalize_url(notice.url)
            if key in seen:
                continue
            seen.add(key)
            rows.append(enrich_notice(notice))
    rows.sort(key=lambda row: (row.published_at, row.source_name, row.title), reverse=True)
    return rows


def write_jsonl(path: Path, notices: Iterable[Notice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for notice in notices:
            handle.write(json.dumps(notice.to_dict(), ensure_ascii=False) + "\n")
