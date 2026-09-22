"""Conservative delivery safety-net around the frozen research rules_v1.

This is intentionally separate from the cross-site evaluation: the recorded
rules_v1 scores stay reproducible, while known false-negative patterns are
not silently omitted from the daily digest.
"""

from __future__ import annotations

from .models import Notice
from .rules_v1 import decide


def delivery_label(notice: Notice) -> str:
    baseline = decide(
        notice.title, notice.summary, notice.body_text, notice.source_id
    ).label
    if baseline != "filter":
        return baseline
    if notice.site_id == "undergraduate_school" and notice.source_id == "uc_student_notice":
        title = notice.title
        if any(term in title for term in ("录取名单", "立项名单", "公共教室开放")):
            return "review"
    return baseline
