from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from whu_notice_research.digest import build_digest, feishu_parts, render_text
from whu_notice_research.health import notice_health_issues
from whu_notice_research.models import Notice
from whu_notice_research.rules_v1 import decide
from whu_notice_research.storage import NoticeStore
from whu_notice_research.wechat.article import WechatContentBlocked, parse_article_html
from whu_notice_research.wechat.collector import _merge_articles, collect_wechat, retry_wechat_content
from whu_notice_research.wechat.models import ProviderResult, WechatAccount, WechatArticle

spec = importlib.util.spec_from_file_location("daily_digest_content_test", ROOT / "scripts/daily_digest.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)


class ContentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        self.account = WechatAccount(id="test", display_name="测试公众号", enabled=True, verified=True)
        self.notice = Notice(
            "test", "公众号文章", "", "本科生竞赛报名", "https://mp.weixin.qq.com/s/test",
            site_id="wechat", site_name="测试公众号", channel="wechat", publisher_id="test",
            wechat_article_key="wechat:test", content_quality="metadata", fetch_error="要求人工验证",
        )

    def save(self, store, notice=None):
        notice = notice or self.notice
        run = store.start_run("wechat", "incremental")
        sync = store.sync([notice], {notice.notice_id: decide(notice.title)}, ruleset_version="v", run_id=run)
        store.finish_run(run, sync)

    def test_script_verification_strings_do_not_hide_real_body(self):
        article = parse_article_html(
            '<script>var error="当前环境异常";</script><div id="js_content">正常报名内容</div>',
            self.notice.url, publisher_id="test", publisher_name="测试", provider="weread",
        )
        self.assertEqual(article.body_text, "正常报名内容")
        with self.assertRaises(WechatContentBlocked):
            parse_article_html('<p>当前环境异常，请完成验证后即可继续访问</p>', self.notice.url,
                               publisher_id="test", publisher_name="测试", provider="weread")

    def test_pending_content_survives_cursor_and_has_daily_and_total_budget(self):
        with tempfile.TemporaryDirectory() as tmp, NoticeStore(Path(tmp) / "state.sqlite3") as store:
            self.save(store)
            store.save_cursor("test", "weread", {"last_keys": ["wechat:test"]})
            self.assertEqual(len(store.pending_wechat_content(self.day)), 1)
            base = datetime.fromisoformat(self.day).date()
            for offset in range(3):
                day = (base + timedelta(days=offset)).isoformat()
                self.assertTrue(store.wechat_content_attempt_allowed(self.notice.notice_id, day))
                store.record_wechat_content_attempt(self.notice, day)
                self.assertEqual(store.pending_wechat_content(day), [])
            fourth = (base + timedelta(days=3)).isoformat()
            self.assertFalse(store.wechat_content_attempt_allowed(self.notice.notice_id, fourth))
            self.assertEqual(store.pending_wechat_content(fourth), [])

    def test_retry_restores_body_without_rediscovering_articles(self):
        full = WechatArticle("test", "测试公众号", self.notice.title, self.notice.url,
                             body_text="报名条件和活动时间" * 20, content_quality="full_text",
                             article_key="wechat:test", published_at=self.day)
        with tempfile.TemporaryDirectory() as tmp, NoticeStore(Path(tmp) / "state.sqlite3") as store:
            self.save(store)
            with patch("whu_notice_research.wechat.collector.load_accounts", return_value=[self.account]), patch(
                "whu_notice_research.wechat.collector.fetch_article", return_value=full
            ) as fetch:
                notices = retry_wechat_content(project_root=ROOT, store=store)
                self.assertEqual(len(notices), 1)
                self.save(store, notices[0])
                self.assertEqual(notices[0].fetch_error, "")
                self.assertEqual(store.content_recovered_on(self.day)[0].body_text, full.body_text)
                self.assertEqual(store.published_on(self.day)[0].published_at, self.day)
                self.assertEqual(retry_wechat_content(project_root=ROOT, store=store), [])
                fetch.assert_called_once()

    def test_duplicate_provider_does_not_repeat_failed_fetch_same_day(self):
        article = WechatArticle("test", "测试公众号", self.notice.title, self.notice.url,
                                article_key="wechat:test", provider="weread")
        provider = SimpleNamespace(sync=lambda a, c: ProviderResult("weread", articles=[article]))
        account = replace(self.account, providers=("weread",))
        with tempfile.TemporaryDirectory() as tmp, NoticeStore(Path(tmp) / "state.sqlite3") as store:
            self.save(store)
            store.record_wechat_content_attempt(self.notice, self.day)
            with patch("whu_notice_research.wechat.collector.load_accounts", return_value=[account]), patch(
                "whu_notice_research.wechat.collector._provider_map", return_value={"weread": provider}
            ), patch("whu_notice_research.wechat.collector.fetch_article") as fetch:
                result = collect_wechat(project_root=ROOT, store=store)
                fetch.assert_not_called()
                self.assertEqual(result.notices[0].fetch_error, self.notice.fetch_error)

    def test_merge_keeps_richer_alternate_content(self):
        metadata = WechatArticle("test", "测试", "报名", self.notice.url,
                                 article_key="wechat:test", provider="weread_web")
        full = replace(metadata, provider="sogou", body_text="正文" * 100, summary="摘要",
                       content_quality="full_text", attachments=[{"text": "报名表", "url": "https://example.com/a.pdf"}])
        merged = _merge_articles([full, metadata])[0]
        self.assertEqual(merged.content_quality, "full_text")
        self.assertEqual(merged.body_text, full.body_text)
        self.assertEqual(merged.attachments, full.attachments)

    def test_missing_body_is_not_silent_and_psychology_exclusion_remains(self):
        self.notice.ai_analysis = dict(schema_version="ai_v4", actionable=True, audience_match=True,
                                       needs_review=True, category="竞赛")
        digest = build_digest(self.day, [self.notice], {})
        self.assertEqual(digest.candidate_count, 1)
        text = render_text(digest)
        self.assertIn("在微信中查看原文", text)
        self.assertNotIn("VPN", text)
        self.assertIn("结果可能不完整", feishu_parts(digest)[0])
        self.notice.title = "心理成长演讲大赛"
        self.notice.ai_analysis["actionable"] = False
        digest = build_digest(self.day, [self.notice], {})
        self.assertEqual(digest.candidate_count, 0)
        self.assertIn("结果可能不完整", feishu_parts(digest)[0])
        self.notice.fetch_error = ""
        self.assertEqual(len(notice_health_issues(self.notice)), 1)

    def test_send_from_frozen_state_reports_failure_and_retries_only_alert(self):
        stats = SimpleNamespace(analyzed=0, cached=1, failed=0, disabled=False, failures=[])
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            with NoticeStore(db) as store:
                self.save(store)
                store.freeze_digest(self.day, [self.notice])
            argv = ["daily_digest.py", "--no-scan", "--use-freeze", "--send", "--skip-if-complete",
                    "--database", str(db), "--output-dir", str(Path(tmp)/"out"), "--digest-day", self.day]
            with patch.object(sys, "argv", argv), patch.object(daily, "load_env_file"), patch.object(
                daily.AIConfig, "from_environment", return_value=SimpleNamespace(model="test")
            ), patch.object(daily.DeliveryConfig, "from_environment", return_value=object()), patch.object(
                daily, "attach_ai_analyses", return_value=stats
            ), patch.object(daily, "send_feishu", side_effect=[None, RuntimeError("temporary failure")]) as send:
                self.assertEqual(daily.main(), 1)
                self.assertIn("要求人工验证", send.call_args_list[1].args[1])
            with NoticeStore(db) as store:
                self.assertFalse(store.delivery_complete(self.day, "feishu"))
            with patch.object(sys, "argv", argv), patch.object(daily, "load_env_file"), patch.object(
                daily.AIConfig, "from_environment", return_value=SimpleNamespace(model="test")
            ), patch.object(daily.DeliveryConfig, "from_environment", return_value=object()), patch.object(
                daily, "attach_ai_analyses", return_value=stats
            ), patch.object(daily, "send_feishu") as send:
                self.assertEqual(daily.main(), 0)
                send.assert_called_once()
                self.assertIn("要求人工验证", send.call_args.args[1])
            with NoticeStore(db) as store:
                self.assertTrue(store.delivery_complete(self.day, "feishu"))


if __name__ == "__main__":
    unittest.main()
