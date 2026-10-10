from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.digest import DailyDigest, _deadline_subject, build_digest, feishu_parts
from whu_notice_research.models import Notice
from whu_notice_research.rules_v1 import decide
from whu_notice_research.storage import NoticeStore
from whu_notice_research.wechat.article import WechatContentBlocked
from whu_notice_research.wechat.collector import _read_content
from whu_notice_research.wechat.models import WechatAccount, WechatArticle
from whu_notice_research.wechat.providers import WeReadWebProvider


class BodyRepairTests(unittest.TestCase):
    def test_deadline_keeps_balanced_school_alias(self):
        self.assertEqual(_deadline_subject("巴黎楠泰尔大学（巴黎十大）申请截止"), "巴黎楠泰尔大学（巴黎十大）")
        self.assertEqual(_deadline_subject("荷兰拉德堡德大学(奈梅亨大学)申请截止"), "荷兰拉德堡德大学(奈梅亨大学)")

    def test_no_vpn_claim_for_public_site_failure(self):
        n = Notice("future", "通知", "2026-10-10", "志愿服务招募", "https://future.whu.edu.cn/content.jsp",
                   fetch_error="HTTPError: 403", summary="列表摘要", ai_analysis={
                       "schema_version": "test", "actionable": True, "audience_match": True, "category": "志愿活动"})
        text = feishu_parts(build_digest("2026-10-10", [n], {}))[0]
        self.assertIn("暂未抓取到正文", text)
        self.assertNotIn("VPN", text)

    def test_warning_distinguishes_history(self):
        d = DailyDigest("2026-10-10", incomplete_wechat_count=4, pending_wechat_backlog_count=16)
        text = feishu_parts(d)[0]
        self.assertIn("今日候选中 4 篇", text)
        self.assertIn("16 篇历史文章", text)
        self.assertNotIn("20 篇公众号", text)

    def test_new_reader_gets_only_one_upgrade_attempt(self):
        from whu_notice_research.storage import now_shanghai
        day = now_shanghai()[:10]
        n = Notice("test", "公众号", day, "竞赛报名", "https://mp.weixin.qq.com/s/test",
                   site_id="wechat", channel="wechat", content_quality="metadata", fetch_error="验证")
        with tempfile.TemporaryDirectory() as tmp, NoticeStore(Path(tmp)/"test.db") as store:
            run = store.start_run("wechat", "test")
            store.sync([n], {n.notice_id: decide(n.title)}, ruleset_version="v", run_id=run)
            store.connection.execute(
                "INSERT INTO wechat_content_attempts VALUES (?,?,3,?,'','')", (n.notice_id, day, n.fetch_error))
            store.connection.commit()
            self.assertEqual(len(store.pending_wechat_content(day)), 1)
            store.record_wechat_content_attempt(n, day)
            self.assertFalse(store.wechat_content_attempt_allowed(n.notice_id, day))
            self.assertEqual(store.pending_wechat_content(day), [])

    def test_reader_body_prevents_public_challenge_request(self):
        article = WechatArticle("test", "测试", "竞赛报名", "https://mp.weixin.qq.com/s/test",
                                raw={"review_id": "MP_WXS_1_test"})
        full = replace(article, body_text="报名须知"*30, content_quality="full_text",
                       raw={"content_provider": "weread_reader"})
        reader = Mock()
        reader.read_article.return_value = full
        with patch("whu_notice_research.wechat.collector.fetch_article") as fetch:
            n = _read_content(article, WechatAccount(id="test", display_name="测试"), Mock(), reader=reader)
        self.assertEqual(n.fetch_error, "")
        self.assertEqual(n.body_text, full.body_text)
        fetch.assert_not_called()

    def test_late_repair_carries_over_but_completed_next_digest_does_not(self):
        n = Notice("test", "公众号", "2026-10-10", "竞赛报名", "https://mp.weixin.qq.com/s/test",
                   site_id="wechat", channel="wechat", content_quality="full_text", body_text="报名条件"*30,
                   fetched_at="2026-10-10T22:25:00+08:00")
        with tempfile.TemporaryDirectory() as tmp, NoticeStore(Path(tmp)/"test.db") as store:
            run = store.start_run("wechat", "test")
            store.sync([n], {n.notice_id: decide(n.title)}, ruleset_version="v", run_id=run)
            store.record_wechat_content_attempt(n, "2026-10-10", recovered=True)
            store.freeze_digest("2026-10-10", [n])
            store.connection.execute("UPDATE digest_freezes SET frozen_at='2026-10-10T21:55:00+08:00'")
            store.mark_delivery_complete("2026-10-10", "feishu")
            self.assertEqual(len(store.pending_recovered_content("2026-10-11")), 1)
            store.freeze_digest("2026-10-11", [n])
            store.connection.execute("UPDATE digest_freezes SET frozen_at='2026-10-11T21:55:00+08:00' WHERE day='2026-10-11'")
            store.mark_delivery_complete("2026-10-11", "feishu")
            self.assertEqual(store.pending_recovered_content("2026-10-12"), [])

    def test_reader_auth_failure_circuit_breaker(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/"state.json"
            state.write_text(json.dumps({"cookies": []}), encoding="utf-8")
            p = WeReadWebProvider(state)
            p.session_warmed = True
            p.session.get = Mock(return_value=Mock(status_code=403))
            a = WechatArticle("test", "测试", "报名", "https://mp.weixin.qq.com/s/test",
                              raw={"review_id": "MP_WXS_1_test"})
            for _ in range(2):
                with self.assertRaises(WechatContentBlocked):
                    p.read_article(a)
            p.session.get.assert_called_once()
            p.close()

    def test_cold_reader_loads_cover_before_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/"state.json"
            state.write_text(json.dumps({"cookies": []}), encoding="utf-8")
            p = WeReadWebProvider(state)
            cover = Mock(status_code=200)
            cover.json.return_value = {"title": "报名通知"}
            content = Mock(status_code=200, text='<div id="js_content">'+"报名须知"*30+'</div>')
            p.session.get = Mock(side_effect=[cover, content])
            a = WechatArticle("test", "测试", "报名", "https://mp.weixin.qq.com/s/test",
                              raw={"review_id": "MP_WXS_1_test"})
            result = p.read_article(a)
            self.assertEqual(result.content_quality, "full_text")
            self.assertIn("/api/mp/cover", p.session.get.call_args_list[0].args[0])
            self.assertIn("/web/mp/content", p.session.get.call_args_list[1].args[0])
            p.close()

    def test_server_cookie_refresh_is_persisted_only_to_private_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)/"state.json"
            state.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
            p = WeReadWebProvider(state)
            p.session.cookies.set("wr_skey", "test-renewed", domain="weread.qq.com", path="/")
            p.close()
            rows = json.loads(state.read_text(encoding="utf-8"))["cookies"]
            self.assertEqual(rows[0]["value"], "test-renewed")


if __name__ == "__main__":
    unittest.main()
