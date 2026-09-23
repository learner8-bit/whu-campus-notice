"""Bounded attachment and external-page enrichment for newly discovered notices."""

from __future__ import annotations

import ipaddress
import re
import socket
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from .eis import USER_AGENT, clean_text
from .models import Notice


MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_EXTERNAL_PAGE_BYTES = 1536 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ATTACHMENTS_PER_NOTICE = 4
MAX_EXTERNAL_LINKS_PER_NOTICE = 3
MAX_EXTRACTED_CHARS = 20_000
MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024

IMPORTANT_TERMS = (
    "报名", "申请", "申报", "截止", "对象", "条件", "要求", "资格", "材料", "附件",
    "时间", "地点", "费用", "名额", "赛道", "组别", "联系人", "联系电话", "邮箱",
    "本科生", "年级", "专业", "提交", "系统", "网址",
)


@dataclass(frozen=True)
class Resource:
    data: bytes
    content_type: str
    final_url: str


class UnsafeResourceURL(ValueError):
    pass


class ResourceTooLarge(ValueError):
    pass


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeResourceURL("only public HTTP/HTTPS URLs are allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise UnsafeResourceURL("non-standard network ports are not allowed")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeResourceURL("hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise UnsafeResourceURL("private or non-public network address is not allowed")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        target = urljoin(req.full_url, newurl)
        _validate_public_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _fetch_resource(
    url: str, *, max_bytes: int, timeout: int = 25, referer: str = ""
) -> Resource:
    url = quote(url, safe=":/?&=%#[]@!$'()*+,;-._~")
    _validate_public_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/pdf,application/vnd.openxmlformats-officedocument.*,*/*;q=0.5",
    }
    if referer:
        headers["Referer"] = quote(referer, safe=":/?&=%#[]@!$'()*+,;-._~")
    request = Request(url, headers=headers)
    opener = build_opener(_SafeRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_public_url(final_url)
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            raise ResourceTooLarge(f"resource exceeds {max_bytes // (1024 * 1024)} MB limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ResourceTooLarge(f"resource exceeds {max_bytes // (1024 * 1024)} MB limit")
        content_type = response.headers.get_content_type().lower()
    return Resource(b"".join(chunks), content_type, final_url)


def _normalize_extracted_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()[:MAX_EXTRACTED_CHARS]


def extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is installed in Actions
        raise RuntimeError("pypdf is not installed") from exc
    reader = PdfReader(BytesIO(data))
    pages: list[str] = []
    for page in reader.pages[:80]:
        pages.append(page.extract_text() or "")
    return _normalize_extracted_text("\n".join(pages))


def extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ElementTree.fromstring(_read_zip_member(archive, "word/document.xml"))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            lines.append(text)
    return _normalize_extracted_text("\n".join(lines))


def extract_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(_read_zip_member(archive, "xl/sharedStrings.xml"))
            for item in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                shared.append("".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
        lines: list[str] = []
        sheet_names = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        for index, name in enumerate(sheet_names[:20], 1):
            lines.append(f"工作表{index}")
            root = ElementTree.fromstring(_read_zip_member(archive, name))
            for row in root.iter(ns + "row"):
                values: list[str] = []
                for cell in row.iter(ns + "c"):
                    cell_type = cell.attrib.get("t", "")
                    value_node = cell.find(ns + "v")
                    if cell_type == "inlineStr":
                        value = "".join(node.text or "" for node in cell.iter(ns + "t"))
                    else:
                        value = value_node.text if value_node is not None and value_node.text else ""
                        if cell_type == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                    if value:
                        values.append(value)
                if values:
                    lines.append("\t".join(values))
                if len(lines) >= 2000:
                    break
    return _normalize_extracted_text("\n".join(lines))


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ZIP_MEMBER_BYTES:
        raise ResourceTooLarge("Office document contains an oversized XML part")
    return archive.read(info)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return _normalize_extracted_text(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _normalize_extracted_text(data.decode("utf-8", errors="replace"))


def _resource_kind(name: str, content_type: str, final_url: str) -> str:
    candidate = " ".join((name, urlparse(final_url).path)).lower()
    for extension in ("pdf", "docx", "xlsx", "xlsm", "csv", "txt", "doc", "xls"):
        if re.search(rf"\.{extension}(?:\b|$)", candidate):
            return extension
    mapping = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/csv": "csv",
        "text/plain": "txt",
        "text/html": "html",
        "application/xhtml+xml": "html",
    }
    return mapping.get(content_type, "unknown")


def _extract_document(resource: Resource, name: str) -> tuple[str, str]:
    kind = _resource_kind(name, resource.content_type, resource.final_url)
    if kind == "pdf":
        if not resource.data.lstrip().startswith(b"%PDF-"):
            raise RuntimeError("attachment endpoint did not return a PDF file")
        return kind, extract_pdf(resource.data)
    if kind == "docx":
        if not resource.data.startswith(b"PK"):
            raise RuntimeError("attachment endpoint did not return a DOCX file")
        return kind, extract_docx(resource.data)
    if kind in {"xlsx", "xlsm"}:
        if not resource.data.startswith(b"PK"):
            raise RuntimeError("attachment endpoint did not return an XLSX file")
        return kind, extract_xlsx(resource.data)
    if kind in {"csv", "txt"}:
        return kind, _decode_text(resource.data)
    if kind in {"doc", "xls"}:
        raise RuntimeError(f"legacy {kind.upper()} is link-only in V1")
    raise RuntimeError("unsupported attachment format")


def important_highlights(text: str, limit: int = 5) -> list[str]:
    fragments = re.split(r"(?<=[。！？；;])|\n+", text)
    selected: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        value = clean_text(fragment)
        key = re.sub(r"\W+", "", value)
        if len(value) < 6 or key in seen or not any(term in value for term in IMPORTANT_TERMS):
            continue
        seen.add(key)
        selected.append(value[:240] + ("…" if len(value) > 240 else ""))
        if len(selected) >= limit:
            break
    return selected


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "li", "br", "tr", "h1", "h2", "h3"} and not self.skip_depth:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


def extract_html(data: bytes) -> tuple[str, str]:
    parser = _HTMLTextParser()
    parser.feed(_decode_text(data))
    return clean_text(" ".join(parser.title_parts)), _normalize_extracted_text("".join(parser.text_parts))


def _external_http_link(notice_url: str, link_url: str) -> bool:
    parsed = urlparse(link_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    notice_host = (urlparse(notice_url).hostname or "").lower()
    return parsed.hostname.lower() != notice_host


def enrich_notice(notice: Notice, *, remaining_bytes: list[int] | None = None) -> Notice:
    budget = remaining_bytes if remaining_bytes is not None else [MAX_TOTAL_BYTES]
    summary_additions: list[str] = []
    body_additions: list[str] = []
    enriched_attachments: list[dict[str, str]] = []
    for index, original in enumerate(notice.attachments):
        item = dict(original)
        if index >= MAX_ATTACHMENTS_PER_NOTICE:
            item["status"] = "skipped_limit"
            enriched_attachments.append(item)
            continue
        if budget[0] <= 0:
            item["status"] = "skipped_budget"
            enriched_attachments.append(item)
            continue
        try:
            resource = _fetch_resource(
                item["url"], max_bytes=min(MAX_ATTACHMENT_BYTES, budget[0]), referer=notice.url
            )
            budget[0] -= len(resource.data)
            kind, text = _extract_document(resource, item.get("text", ""))
            item["format"] = kind
            item["status"] = "parsed" if text else "empty_or_scanned"
            item["final_url"] = resource.final_url
            highlights = important_highlights(text)
            if highlights:
                item["highlights"] = "；".join(highlights)
                summary_additions.append(f"附件 {item.get('text') or kind}：{item['highlights']}")
            if text:
                body_additions.append(f"【附件：{item.get('text') or kind}】\n{text}")
        except Exception as exc:  # one broken attachment must not fail the site scan
            item["status"] = "unsupported" if "legacy" in str(exc) or "unsupported" in str(exc) else "failed"
            item["parse_note"] = str(exc)[:180]
        enriched_attachments.append(item)
    notice.attachments = enriched_attachments

    enriched_links: list[dict[str, str]] = []
    followed = 0
    for original in notice.links:
        item = dict(original)
        if not _external_http_link(notice.url, item.get("url", "")):
            enriched_links.append(item)
            continue
        item["kind"] = "external"
        if followed >= MAX_EXTERNAL_LINKS_PER_NOTICE or budget[0] <= 0:
            item["status"] = "skipped_limit"
            enriched_links.append(item)
            continue
        followed += 1
        try:
            resource = _fetch_resource(
                item["url"], max_bytes=min(MAX_EXTERNAL_PAGE_BYTES, budget[0]), referer=notice.url
            )
            budget[0] -= len(resource.data)
            kind = _resource_kind(item.get("text", ""), resource.content_type, resource.final_url)
            if kind == "html":
                resolved_title, text = extract_html(resource.data)
            else:
                resolved_title = item.get("text", "")
                kind, text = _extract_document(resource, item.get("text", ""))
            item["format"] = kind
            item["status"] = "parsed" if text else "empty"
            item["final_url"] = resource.final_url
            if resolved_title:
                item["resolved_title"] = resolved_title
            highlights = important_highlights(text)
            if highlights:
                item["highlights"] = "；".join(highlights)
                summary_additions.append(
                    f"外部页面 {resolved_title or item.get('text') or item['url']}：{item['highlights']}"
                )
            if text:
                body_additions.append(
                    f"【外部页面：{resolved_title or item.get('text') or item['url']}】\n{text}"
                )
        except Exception as exc:  # one blocked external page must not fail the site scan
            item["status"] = "failed"
            item["parse_note"] = str(exc)[:180]
        enriched_links.append(item)
    notice.links = enriched_links

    if summary_additions:
        addition = " ".join(summary_additions)
        notice.summary = (notice.summary + " " + addition).strip()[:3000]
    if body_additions:
        notice.body_text = (notice.body_text + "\n\n" + "\n\n".join(body_additions)).strip()
    return notice


def enrich_notices(notices: list[Notice]) -> None:
    remaining = [MAX_TOTAL_BYTES]
    for notice in notices:
        if remaining[0] <= 0:
            break
        enrich_notice(notice, remaining_bytes=remaining)
