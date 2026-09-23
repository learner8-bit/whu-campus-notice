"""Health diagnostics for the daily collection and delivery pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Notice


@dataclass(frozen=True)
class HealthIssue:
    kind: str
    source: str
    detail: str


def notice_health_issues(notice: Notice) -> list[HealthIssue]:
    """Return actionable parser failures found while enriching one notice."""
    issues: list[HealthIssue] = []
    source = notice.site_name or notice.site_id
    if notice.fetch_error:
        issues.append(HealthIssue("detail", source, f"{notice.title}: {notice.fetch_error}"))
    for item in notice.attachments:
        if item.get("status") == "failed":
            name = item.get("text") or item.get("url") or "unnamed attachment"
            detail = item.get("parse_note") or "parse failed"
            issues.append(HealthIssue("attachment", source, f"{name}: {detail}"))
    for item in notice.links:
        if item.get("kind") == "external" and item.get("status") == "failed":
            name = item.get("text") or item.get("url") or "external link"
            detail = item.get("parse_note") or "follow failed"
            issues.append(HealthIssue("external_link", source, f"{name}: {detail}"))
    return issues


def render_health_alert(day: str, issues: list[HealthIssue], *, max_items: int = 10) -> str:
    """Build a compact Feishu alert without exposing secrets or full page contents."""
    unique: list[HealthIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.kind, issue.source, issue.detail)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    labels = {
        "site": "信息源采集失败",
        "detail": "通知详情解析失败",
        "attachment": "附件解析失败",
        "external_link": "外部链接跟进失败",
        "ai": "DeepSeek 分析异常",
        "delivery": "通知发送异常",
    }
    lines = [f"⚠️ 武大校园信息系统异常｜{day}"]
    for issue in unique[:max_items]:
        lines.append(f"- {labels.get(issue.kind, issue.kind)}｜{issue.source}｜{issue.detail}")
    if len(unique) > max_items:
        lines.append(f"- 另有 {len(unique) - max_items} 项异常未展开")
    lines.append("请到电脑上运行一键测试程序，查看完整错误并处理。")
    return "\n".join(lines)
