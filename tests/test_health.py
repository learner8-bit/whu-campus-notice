from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.health import HealthIssue, notice_health_issues, render_health_alert
from whu_notice_research.models import Notice


class HealthTests(unittest.TestCase):
    def test_notice_failures_are_reported(self) -> None:
        notice = Notice(
            source_id="notice",
            source_name="通知",
            site_id="example",
            site_name="示例网站",
            published_at="2026-09-23",
            title="测试通知",
            url="https://example.com/1",
            fetch_error="HTTPError: 403",
            attachments=[{"text": "报名表.pdf", "status": "failed", "parse_note": "timeout"}],
            links=[{"text": "报名页", "kind": "external", "status": "failed"}],
        )
        issues = notice_health_issues(notice)
        self.assertEqual([item.kind for item in issues], ["detail", "attachment", "external_link"])

    def test_alert_is_deduplicated_and_actionable(self) -> None:
        issue = HealthIssue("site", "本科生院", "TimeoutError")
        message = render_health_alert("2026-09-23", [issue, issue])
        self.assertEqual(message.count("TimeoutError"), 1)
        self.assertIn("请到电脑上运行一键测试程序", message)


if __name__ == "__main__":
    unittest.main()
