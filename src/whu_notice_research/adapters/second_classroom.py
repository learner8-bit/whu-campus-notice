"""Public activity feed for Wuhan University's second-classroom platform."""

from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ..eis import USER_AGENT, clean_text
from ..models import Notice


SITE_ID = "second_classroom"
SITE_NAME = "武汉大学第二课堂"
SEARCH_URL = "https://ek.whu.edu.cn/dekt/vuekczx/search"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _display_time(value: str | None) -> str:
    parsed = _parse_time(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else ""


def _fetch_page(page_index: int, page_size: int = 500) -> dict:
    payload = urlencode(
        {
            "limit": page_size,
            "pageindex": page_index,
            "fl": "",
            "zt": "",
            "px": "jjks",
            "rq": "",
            "cyxs": "",
            "bmzt": "",
            "xy": "",
            "mc": "",
            "kssj": "",
            "jssj": "",
        }
    ).encode("utf-8")
    request = Request(
        SEARCH_URL,
        data=payload,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=45) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code") != 0 or not isinstance(result.get("data"), list):
        raise RuntimeError(f"second-classroom API error: {result.get('msg') or result.get('code')}")
    return result


def _notice_from_row(row: dict) -> Notice:
    course_id = str(row["id"])
    url = f"https://ek.whu.edu.cn/dekt/#/courseDetail?id={course_id}"
    registration_start = _parse_time(row.get("bmks"))
    activity_start = _parse_time(row.get("hdks"))
    published_at = registration_start or activity_start or datetime.now(SHANGHAI)
    details = [clean_text(row.get("jj") or "")]
    fields = (
        ("类别", row.get("flmc")),
        ("报名开始", _display_time(row.get("bmks"))),
        ("报名截止", _display_time(row.get("bmjs"))),
        ("活动开始", _display_time(row.get("hdks"))),
        ("活动结束", _display_time(row.get("hdjs"))),
        ("地点", row.get("dd")),
        ("发布单位", row.get("ssxymc")),
        ("面向学院", row.get("kcyxystr") or row.get("kcyxy")),
        ("面向年级", row.get("kcynj")),
        ("第二课堂学时", row.get("xs")),
        ("名额", row.get("zmrs")),
        ("已报名", row.get("bmrs")),
    )
    details.extend(f"{label}：{value}" for label, value in fields if value not in (None, ""))
    return Notice(
        site_id=SITE_ID,
        site_name=SITE_NAME,
        source_id=f"second_classroom_{row.get('flid') or 'activity'}",
        source_name=clean_text(row.get("flmc") or "第二课堂活动"),
        published_at=published_at.date().isoformat(),
        title=clean_text(row.get("mc") or f"第二课堂活动 {course_id}"),
        url=url,
        summary=clean_text(row.get("jj") or ""),
        detail_title=clean_text(row.get("mc") or ""),
        detail_column=clean_text(row.get("flmc") or "第二课堂活动"),
        body_text="\n".join(part for part in details if part),
        fetched_at=datetime.now(SHANGHAI).isoformat(timespec="seconds"),
    )


def collect_all(
    cutoff: date,
    *,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    known_urls = known_urls or set()
    first = _fetch_page(1)
    pages = int(first.get("totalPages") or 1)
    payload_rows = list(first["data"])
    for page_index in range(2, pages + 1):
        payload_rows.extend(_fetch_page(page_index)["data"])

    today = date.today()
    notices: list[Notice] = []
    seen: set[str] = set()
    for row in payload_rows:
        notice = _notice_from_row(row)
        registration_end = _parse_time(row.get("bmjs"))
        activity_end = _parse_time(row.get("hdjs")) or _parse_time(row.get("hdks"))
        still_actionable = bool(
            (registration_end and registration_end.date() >= today)
            or (activity_end and activity_end.date() >= today)
        )
        if notice.published_at < cutoff.isoformat() and not still_actionable:
            continue
        if notice.url in seen or (incremental and notice.url in known_urls):
            continue
        seen.add(notice.url)
        notices.append(notice)
    notices.sort(key=lambda item: (item.published_at, item.title), reverse=True)
    return notices
