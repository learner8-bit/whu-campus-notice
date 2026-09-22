"""Site-independent daily notice digest formatting."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field

from .delivery_policy import delivery_label
from .models import Notice


@dataclass
class DailyDigest:
    day: str
    keep: list[Notice] = field(default_factory=list)
    review: list[Notice] = field(default_factory=list)
    filtered_count: int = 0
    scan_status: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"武汉大学校园通知日报｜{self.day}"

    @property
    def candidate_count(self) -> int:
        return len(self.keep) + len(self.review)


def _title_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def build_digest(day: str, notices: list[Notice], scan_status: dict[str, str]) -> DailyDigest:
    digest = DailyDigest(day=day, scan_status=scan_status)
    # Exact/small-punctuation title duplicates from different campus sites share
    # one digest entry. Storage still keeps both original notices and URLs.
    preferred: dict[str, tuple[int, Notice]] = {}
    priority = {"filter": 0, "review": 1, "keep": 2}
    for notice in notices:
        key = _title_key(notice.title) or notice.notice_id
        rank = priority[delivery_label(notice)]
        current = preferred.get(key)
        if current is None or (rank, len(notice.body_text)) > (
            current[0], len(current[1].body_text)
        ):
            preferred[key] = (rank, notice)
    for notice in sorted(
        (value[1] for value in preferred.values()),
        key=lambda item: (item.published_at, item.title),
        reverse=True,
    ):
        label = delivery_label(notice)
        if label == "keep":
            digest.keep.append(notice)
        elif label == "review":
            digest.review.append(notice)
        else:
            digest.filtered_count += 1
    return digest


def _excerpt(notice: Notice, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", notice.summary or notice.body_text).strip()
    if not text and notice.fetch_error:
        return "详情页暂不可用，请点开官网原文核实。"
    return text[:limit] + ("…" if len(text) > limit else "")


def render_text(digest: DailyDigest) -> str:
    lines = [
        digest.title,
        f"今日发现：值得关注 {len(digest.keep)} 条，待复核 {len(digest.review)} 条；明显无关 {digest.filtered_count} 条。",
    ]
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        lines.append("注意：以下站点本次采集失败，日报可能不完整：" + "、".join(failures))
    if not digest.candidate_count:
        lines.append("今日暂无新增的有效候选信息。")
    for heading, rows in (("值得关注", digest.keep), ("待复核（可能有用）", digest.review)):
        if not rows:
            continue
        lines.append("")
        lines.append(f"【{heading}】")
        for index, notice in enumerate(rows, 1):
            lines.append(f"{index}. {notice.title}")
            lines.append(f"   {notice.site_name} / {notice.source_name} · 发布于 {notice.published_at}")
            excerpt = _excerpt(notice)
            if excerpt:
                lines.append(f"   {excerpt}")
            lines.append(f"   原文：{notice.url}")
            for attachment in notice.attachments[:3]:
                lines.append(f"   附件：{attachment.get('text') or '查看附件'} {attachment['url']}")
            if len(notice.attachments) > 3:
                lines.append(f"   另有 {len(notice.attachments) - 3} 个附件，请在原文查看。")
    lines.extend(("", "提示：规则筛选仍在完善；请以官网原文、报名条件和截止时间为准。"))
    return "\n".join(lines)


def render_html(digest: DailyDigest) -> str:
    out = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<body style="font:15px/1.65 Arial,sans-serif;max-width:760px;margin:28px auto;color:#222">',
        f'<h1 style="font-size:24px">{html.escape(digest.title)}</h1>',
        f'<p>值得关注 {len(digest.keep)} 条 · 待复核 {len(digest.review)} 条 · 已过滤 {digest.filtered_count} 条</p>',
    ]
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        out.append('<p style="color:#a35b00">部分站点采集失败：' + html.escape("、".join(failures)) + "；日报可能不完整。</p>")
    if not digest.candidate_count:
        out.append("<p>今日暂无新增的有效候选信息。</p>")
    for heading, rows in (("值得关注", digest.keep), ("待复核（可能有用）", digest.review)):
        if not rows:
            continue
        out.append(f"<h2>{heading}</h2>")
        for notice in rows:
            url = html.escape(notice.url, quote=True)
            out.append('<section style="padding:14px 0;border-top:1px solid #ddd">')
            out.append(f'<h3 style="margin:0 0 4px"><a href="{url}">{html.escape(notice.title)}</a></h3>')
            out.append(
                '<small style="color:#666">'
                + html.escape(f"{notice.site_name} / {notice.source_name} · 发布于 {notice.published_at}")
                + "</small>"
            )
            excerpt = _excerpt(notice, 260)
            if excerpt:
                out.append(f"<p>{html.escape(excerpt)}</p>")
            if notice.attachments:
                out.append("<p>附件：" + " · ".join(
                    f'<a href="{html.escape(item["url"], quote=True)}">'
                    f'{html.escape(item.get("text") or "查看附件")}</a>'
                    for item in notice.attachments[:5]
                ) + "</p>")
            out.append("</section>")
    out.append('<p style="color:#666">请以官网原文、报名条件和截止时间为准。</p></body></html>')
    return "".join(out)


def feishu_parts(digest: DailyDigest, max_chars: int = 3500) -> list[str]:
    """Keep webhook text messages comfortably below typical bot size limits."""
    header = digest.title + f"\n值得关注 {len(digest.keep)}｜待复核 {len(digest.review)}"
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        header += "\n⚠ 采集失败：" + "、".join(failures)
    entries: list[str] = []
    for marker, rows in (("✅", digest.keep), ("🔎", digest.review)):
        for notice in rows:
            entry = (
                f"{marker} {notice.title}\n"
                f"{notice.site_name}/{notice.source_name} · {notice.published_at}\n"
                f"{notice.url}"
            )
            if notice.fetch_error:
                entry += "\n详情受官网验证限制，请打开原文确认"
            entries.append(entry)
    if not entries:
        entries.append("今日暂无新增的有效候选信息。")
    parts: list[str] = []
    current = header
    for entry in entries:
        if len(current) + len(entry) + 2 > max_chars and current != header:
            parts.append(current)
            current = header
        if len(entry) > max_chars - len(header) - 2:
            entry = entry[: max_chars - len(header) - 3] + "…"
        current += "\n\n" + entry
    parts.append(current)
    if len(parts) > 1:
        parts = [part.replace(digest.title, f"{digest.title}（{index}/{len(parts)}）", 1)
                 for index, part in enumerate(parts, 1)]
    return parts


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
