from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.models import Notice  # noqa: E402
from whu_notice_research.rules_v1 import RULESET_VERSION, decide  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402


class StorageTests(unittest.TestCase):
    def test_new_unchanged_and_updated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            notice = Notice(
                site_id="test",
                site_name="测试站点",
                source_id="notices",
                source_name="通知",
                published_at="2026-09-22",
                title="竞赛报名通知",
                url="https://example.edu/info/1.htm",
            )
            with NoticeStore(path) as store:
                run1 = store.start_run("test", "bootstrap")
                result1 = store.sync(
                    [notice],
                    {notice.notice_id: decide(notice.title)},
                    ruleset_version=RULESET_VERSION,
                    run_id=run1,
                )
                store.finish_run(run1, result1)
                self.assertEqual(len(result1.new), 1)

                run2 = store.start_run("test", "incremental")
                result2 = store.sync(
                    [notice],
                    {notice.notice_id: decide(notice.title)},
                    ruleset_version=RULESET_VERSION,
                    run_id=run2,
                )
                store.finish_run(run2, result2)
                self.assertEqual(len(result2.unchanged), 1)

                notice.body_text = "正文已更新"
                run3 = store.start_run("test", "refresh")
                result3 = store.sync(
                    [notice],
                    {notice.notice_id: decide(notice.title)},
                    ruleset_version=RULESET_VERSION,
                    run_id=run3,
                )
                store.finish_run(run3, result3)
                self.assertEqual(len(result3.updated), 1)
                self.assertEqual(store.count("test"), 1)

    def test_failed_detail_is_eligible_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            notice = Notice(
                site_id="test",
                site_name="测试站点",
                source_id="notices",
                source_name="通知",
                published_at="2026-09-22",
                title="本科生报名通知",
                url="https://example.edu/info/2.htm",
                fetch_error="BlockedPageError: verification page",
            )
            with NoticeStore(Path(directory) / "state.sqlite3") as store:
                run_id = store.start_run("test", "bootstrap")
                result = store.sync(
                    [notice],
                    {notice.notice_id: decide(notice.title)},
                    ruleset_version=RULESET_VERSION,
                    run_id=run_id,
                )
                store.finish_run(run_id, result)
                self.assertNotIn(notice.url, store.known_urls("test"))
                notice.fetch_error = ""
                notice.body_text = "报名方式详见正文。"
                retry_id = store.start_run("test", "incremental")
                retry = store.sync(
                    [notice],
                    {notice.notice_id: decide(notice.title)},
                    ruleset_version=RULESET_VERSION,
                    run_id=retry_id,
                )
                store.finish_run(retry_id, retry)
                self.assertEqual(len(retry.updated), 1)
                self.assertIn(notice.url, store.known_urls("test"))

    def test_today_query_excludes_baseline_but_keeps_live_new(self) -> None:
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with tempfile.TemporaryDirectory() as directory, NoticeStore(
            Path(directory) / "state.sqlite3"
        ) as store:
            baseline = Notice("s", "通知", day, "旧", "https://example.edu/1", site_id="test")
            first_run = store.start_run("test", "import")
            first = store.sync(
                [baseline], {baseline.notice_id: decide(baseline.title)},
                ruleset_version=RULESET_VERSION, run_id=first_run,
            )
            store.finish_run(first_run, first)
            self.assertEqual(store.first_seen_on(day, "test"), [])
            fresh = Notice("s", "通知", day, "新", "https://example.edu/2", site_id="test")
            live_run = store.start_run("test", "incremental")
            second = store.sync(
                [fresh], {fresh.notice_id: decide(fresh.title)},
                ruleset_version=RULESET_VERSION, run_id=live_run,
            )
            store.finish_run(live_run, second)
            self.assertEqual([row.title for row in store.first_seen_on(day, "test")], ["新"])


if __name__ == "__main__":
    unittest.main()
