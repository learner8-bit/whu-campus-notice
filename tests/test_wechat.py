from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.digest import build_digest, render_text  # noqa: E402
from whu_notice_research.models import Notice  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402
from whu_notice_research.wechat.article import parse_article_html  # noqa: E402
from whu_notice_research.wechat.identity import (  # noqa: E402
    canonicalize_wechat_url,
    wechat_article_key,
)
from whu_notice_research.wechat.weread import (  # noqa: E402
    WeReadAuthExpired,
    WeReadCredentials,
    WeReadMobileClient,
)


class WechatTests(unittest.TestCase):
    def _weread_client(self) -> WeReadMobileClient:
        return WeReadMobileClient(WeReadCredentials(vid="123", accessToken="token"))

    def test_long_link_identity_ignores_share_tracking(self) -> None:
        first = "https://mp.weixin.qq.com/s?__biz=abc%3D%3D&mid=123&idx=2&scene=1"
        second = "https://mp.weixin.qq.com/s?mid=123&idx=2&__biz=abc%3D%3D&from=timeline"
        self.assertEqual(
            wechat_article_key(first),
            wechat_article_key(second),
        )
        self.assertNotIn("scene", canonicalize_wechat_url(first))

    def test_short_link_has_stable_url_key(self) -> None:
        url = "https://mp.weixin.qq.com/s/A_B-c123?scene=2"
        self.assertEqual(wechat_article_key(url), wechat_article_key(url + "&from=timeline"))

    def test_weread_book_info_returns_public_account_metadata(self) -> None:
        client = self._weread_client()
        response = Mock(status_code=200)
        response.json.return_value = {"bookId": "MP_WXS_1", "title": "武汉大学"}
        client.session.get = Mock(return_value=response)

        payload = client.get_book_info("MP_WXS_1")

        self.assertEqual(payload["title"], "武汉大学")
        response.raise_for_status.assert_called_once()

    def test_weread_book_info_rejects_expired_session(self) -> None:
        client = self._weread_client()
        client.session.get = Mock(return_value=Mock(status_code=403))

        with self.assertRaises(WeReadAuthExpired):
            client.get_book_info("MP_WXS_1")

    def test_article_parser_extracts_text_links_files_and_images(self) -> None:
        source = """
        <html><h1 id="activity-name">竞赛报名通知</h1><em id="publish_time">2026年9月27日</em>
        <div id="js_content"><p>请于十月一日前报名。</p>
        <a href="https://example.edu/form.docx">报名表</a>
        <a href="https://example.edu/signup">报名页面</a>
        <img data-src="https://mmbiz.qpic.cn/poster.jpg"></div></html>
        """
        article = parse_article_html(
            source,
            "https://mp.weixin.qq.com/s/example",
            publisher_id="eis",
            publisher_name="EIS青年说",
            provider="weread",
        )
        self.assertEqual(article.published_at, "2026-09-27")
        self.assertIn("十月一日前报名", article.body_text)
        self.assertEqual(article.attachments[0]["text"], "报名表")
        self.assertEqual(article.links[0]["text"], "报名页面")
        self.assertEqual(len(article.images), 1)

    def test_provider_health_escalates_on_third_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with NoticeStore(Path(directory) / "state.sqlite3") as store:
                self.assertEqual(store.record_provider_health("eis", "weread", "unavailable"), 1)
                self.assertEqual(store.record_provider_health("eis", "weread", "unavailable"), 2)
                self.assertEqual(store.record_provider_health("eis", "weread", "unavailable"), 3)
                self.assertEqual(store.record_provider_health("eis", "weread", "healthy"), 0)

    def test_freeze_round_trip(self) -> None:
        notice = Notice("s", "通知", "2026-09-27", "报名", "https://example.edu/1")
        with tempfile.TemporaryDirectory() as directory:
            with NoticeStore(Path(directory) / "state.sqlite3") as store:
                run_id = store.start_run("test", "incremental")
                from whu_notice_research.rules_v1 import decide
                store.sync([notice], {notice.notice_id: decide(notice.title)}, ruleset_version="v", run_id=run_id)
                store.freeze_digest("2026-09-27", [notice])
                self.assertEqual(store.frozen_digest("2026-09-27")[0].title, "报名")

    def test_cross_channel_event_keeps_one_original_and_merges_sources(self) -> None:
        website = Notice(
            "s", "通知", "2026-09-27", "竞赛通知", "https://eis.whu.edu.cn/1",
            body_text="官网正文", site_name="电子信息学院官网",
        )
        wechat = Notice(
            "w", "公众号文章", "2026-09-27", "竞赛通知", "https://mp.weixin.qq.com/s/1",
            body_text="公众号正文", site_name="EIS青年说", channel="wechat",
        )
        analysis = {
            "schema_version": "ai_v3", "actionable": True, "audience_match": True,
            "needs_review": False, "category": "竞赛", "event_key": "2026竞赛",
        }
        website.ai_analysis = dict(analysis)
        wechat.ai_analysis = dict(analysis)
        digest = build_digest("2026-09-27", [website, wechat], {})
        rendered = render_text(digest)
        self.assertEqual(digest.candidate_count, 1)
        self.assertIn("电子信息学院官网", rendered)
        self.assertIn("EIS青年说", rendered)
        self.assertEqual(rendered.count("原文："), 1)

    def test_shadow_account_is_stored_but_not_delivered(self) -> None:
        notice = Notice(
            "w", "公众号文章", "2026-09-27", "竞赛报名通知",
            "https://mp.weixin.qq.com/s/shadow",
            site_name="武汉大学", channel="wechat", delivery_not_before="2026-10-04",
        )
        notice.ai_analysis = {
            "schema_version": "ai_v3", "actionable": True, "audience_match": True,
            "needs_review": False, "category": "竞赛", "event_key": "影子测试",
        }
        digest = build_digest("2026-09-27", [notice], {})
        self.assertEqual(digest.candidate_count, 0)


if __name__ == "__main__":
    unittest.main()
