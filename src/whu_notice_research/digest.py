"""Site-independent daily notice digest formatting."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

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
        return f"学校信息日报｜{self.day}"

    @property
    def candidate_count(self) -> int:
        return len(self.keep) + len(self.review)


def _title_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _effective_label(notice: Notice) -> str:
    ai = notice.ai_analysis
    if ai.get("schema_version"):
        if ai.get("audience_match") is False or ai.get("actionable") is False:
            return "filter"
        if ai.get("needs_review") is True:
            return "review"
        return "keep"
    return delivery_label(notice)


def _dedupe_key(notice: Notice) -> str:
    event_key = str(notice.ai_analysis.get("event_key") or "")
    category = str(notice.ai_analysis.get("category") or "")
    if event_key:
        normalized = _title_key(category + event_key)
        if normalized:
            return "event:" + normalized
    return "title:" + (_title_key(notice.title) or notice.notice_id)


def _category(notice: Notice) -> str:
    category = str(notice.ai_analysis.get("category") or "")
    if category and category != "其他":
        return category
    title = notice.title
    for name, words in (
        ("竞赛", ("竞赛", "比赛", "大赛")),
        ("奖学金", ("奖学金", "助学金")),
        ("国际交流", ("交流", "交换", "访学")),
        ("科研机会", ("科研", "实验室", "招募")),
        ("学术讲座", ("讲座", "论坛", "报告")),
        ("志愿服务", ("志愿", "志愿者")),
        ("社会实践", ("实践",)),
    ):
        if any(word in title for word in words):
            return name
    return "通知"


def _material_names(notice: Notice) -> list[str]:
    names: list[str] = []
    for value in notice.ai_analysis.get("materials", []):
        text = str(value).strip()
        if text and text not in names:
            names.append(text)
    return names[:10]


def _attachment_rows(notice: Notice) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in notice.attachments:
        name = str(item.get("text") or "附件").strip()
        url = str(item.get("url") or "").strip()
        key = (name, url)
        if key not in seen:
            seen.add(key)
            rows.append(key)
    return rows[:10]


def _deadline_rows(notice: Notice) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    values = notice.ai_analysis.get("deadlines", [])
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, dict):
                continue
            label = str(value.get("label") or "").strip().rstrip("：:")
            time_text = str(value.get("time") or "").strip()
            if label and time_text and (label, time_text) not in rows:
                rows.append((label, time_text))
    if not rows:
        legacy = str(notice.ai_analysis.get("deadline") or "").strip()
        if legacy:
            rows.append(("截止时间", legacy))
    return rows[:8]


def _has_detail_content(notice: Notice) -> bool:
    if notice.body_text.strip() or notice.summary.strip():
        return True
    return any(
        str(item.get("highlights") or "").strip()
        for item in [*notice.attachments, *notice.links]
    )


def _original_label(notice: Notice) -> str:
    if urlparse(notice.url).hostname == "future.whu.edu.cn":
        return "原文（需校园网或武大 VPN）"
    return "原文"


def _compact_lines(notice: Notice, marker: str = "") -> list[str]:
    prefix = f"{marker} " if marker else ""
    lines = [f"{prefix}【{_category(notice)}｜{notice.site_name}】{notice.title}"]
    value = str(notice.ai_analysis.get("value") or "").strip()
    materials = _material_names(notice)
    attachments = _attachment_rows(notice)
    for label, time_text in _deadline_rows(notice):
        lines.append(f"{label}：{time_text}")
    if value:
        lines.append(f"价值：{value}")
    if materials:
        lines.append("材料：")
        lines.extend(f"- {name}" for name in materials)
    if attachments:
        lines.append("附件：")
        for name, url in attachments:
            lines.append(f"- {name}" + (f"：{url}" if url else ""))
    lines.append(f"{_original_label(notice)}：{notice.url}")
    if not _has_detail_content(notice):
        lines.append("正文状态：暂未抓取到正文，请连接校园网或武大 VPN 后查看原文。")
    if notice.ai_analysis.get("schema_version"):
        summary = str(notice.ai_analysis.get("summary") or "").strip()
    else:
        summary = _excerpt(notice, 100)
    if summary:
        lines.append(f"摘要：{summary}")
    return lines


def build_digest(day: str, notices: list[Notice], scan_status: dict[str, str]) -> DailyDigest:
    digest = DailyDigest(day=day, scan_status=scan_status)
    # Exact/small-punctuation title duplicates from different campus sites share
    # one digest entry. Storage still keeps both original notices and URLs.
    preferred: dict[str, tuple[int, Notice]] = {}
    priority = {"filter": 0, "review": 1, "keep": 2}
    for notice in notices:
        key = _dedupe_key(notice)
        rank = priority[_effective_label(notice)]
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
        label = _effective_label(notice)
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
    lines = [digest.title]
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        lines.append("采集失败：" + "、".join(failures))
    if not digest.candidate_count:
        lines.append("今日暂无新增的有效候选信息。")
    for notice in [*digest.keep, *digest.review]:
        lines.append("")
        lines.extend(_compact_lines(notice, "✅"))
    lines.extend(("", "请以官网原文为准。"))
    return "\n".join(lines)


def render_html(digest: DailyDigest) -> str:
    out = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<body style="font:15px/1.65 Arial,sans-serif;max-width:760px;margin:28px auto;color:#222">',
        f'<h1 style="font-size:24px">{html.escape(digest.title)}</h1>',
    ]
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        out.append('<p style="color:#a35b00">采集失败：' + html.escape("、".join(failures)) + "</p>")
    if not digest.candidate_count:
        out.append("<p>今日暂无新增的有效候选信息。</p>")
    for notice in [*digest.keep, *digest.review]:
        compact = _compact_lines(notice)
        out.append('<section style="padding:14px 0;border-top:1px solid #ddd">')
        out.append(f'<h3 style="margin:0 0 4px">{html.escape(compact[0])}</h3>')
        for line in compact[1:]:
            if line.startswith("原文"):
                url = html.escape(notice.url, quote=True)
                out.append(f'<p><a href="{url}">{html.escape(_original_label(notice))}</a></p>')
            else:
                out.append(f"<p>{html.escape(line)}</p>")
        out.append("</section>")
    out.append('<p style="color:#666">请以官网原文为准。</p></body></html>')
    return "".join(out)


def feishu_parts(digest: DailyDigest, max_chars: int = 3500) -> list[str]:
    """Keep webhook text messages comfortably below typical bot size limits."""
    header = digest.title
    failures = [name for name, status in digest.scan_status.items() if status != "ok"]
    if failures:
        header += "\n⚠ 采集失败：" + "、".join(failures)
    entries: list[str] = []
    for notice in [*digest.keep, *digest.review]:
        entries.append("\n".join(_compact_lines(notice, "✅")))
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
