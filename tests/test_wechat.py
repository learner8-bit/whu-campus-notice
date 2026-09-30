from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.digest import build_digest, render_text  # noqa: E402
from whu_notice_research.models import Notice  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402
from whu_notice_research.wechat.article import parse_article_html  # noqa: E402
from whu_notice_research.wechat.collector import load_accounts  # noqa: E402
from whu_notice_research.wechat.identity import (  # noqa: E402
    canonicalize_wechat_url,
    parse_article_biz,
    wechat_article_key,
)
from whu_notice_research.wechat.weread import (  # noqa: E402
    WeReadAuthExpired,
    WeReadCredentials,
    WeReadMobileClient,
    WeReadRateLimited,
    encode_web_id,
)
from whu_notice_research.wechat.models import WechatAccount  # noqa: E402
from whu_notice_research.wechat.providers import (  # noqa: E402
    MessageAlbumProvider,
    SogouProvider,
    WeReadProvider,
    WeReadWebProvider,
    _articles_from_weread_web,
)


class WechatTests(unittest.TestCase):
    def _weread_client(self) -> WeReadMobileClient:
        return WeReadMobileClient(WeReadCredentials(vid="123", accessToken="token"))

    def test_requested_wechat_accounts_are_registered(self) -> None:
        accounts = load_accounts(ROOT / "config" / "wechat_accounts.json")
        registered_names = {account.display_name for account in accounts}
        requested_names = {
            "武大青年志愿者",
            "珞珈体育",
            "武汉大学本科生院",
            "武汉大学电子信息学院",
            "武汉大学全心权益",
            "武汉大学社团中心",
            "武汉大学学生会",
        }
        self.assertTrue(requested_names <= registered_names)
        removed_names = {
            "武汉大学",
            "武大通识教育",
            "武汉大学学生资助",
            "武汉大学图书馆",
        }
        self.assertTrue(removed_names.isdisjoint(registered_names))

    def test_every_enabled_wechat_account_has_a_unique_identity(self) -> None:
        accounts = load_accounts(ROOT / "config" / "wechat_accounts.json")
        incomplete = [
            account.display_name
            for account in accounts
            if account.enabled and (not account.biz or not account.book_id)
        ]
        self.assertEqual(incomplete, [])

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

    def test_weread_http_499_enters_rate_limit_cooldown(self) -> None:
        client = self._weread_client()
        client.session.get = Mock(return_value=Mock(status_code=499))
        account = WechatAccount(
            id="whu",
            display_name="武汉大学",
            book_id="MP_WXS_3092373510",
        )
        with self.assertRaises(WeReadRateLimited):
            client.get_articles(account)

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

    def test_cross_channel_event_prefers_more_complete_website_source(self) -> None:
        website = Notice(
            "s", "通知", "2026-09-27", "竞赛通知", "https://eis.whu.edu.cn/1",
            body_text="官网正文包含完整的报名条件、截止时间和申请流程",
            attachments=[{"text": "报名表", "url": "https://eis.whu.edu.cn/a.docx"}],
            site_name="电子信息学院官网",
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
        self.assertIn("来源：电子信息学院官网", rendered)
        self.assertNotIn("EIS青年说", rendered)
        self.assertIn("https://eis.whu.edu.cn/1", rendered)
        self.assertEqual(rendered.count("原文："), 1)

    def test_cross_channel_event_keeps_unique_wechat_source(self) -> None:
        wechat = Notice(
            "w", "公众号文章", "2026-09-27", "独有志愿者招募",
            "https://mp.weixin.qq.com/s/unique",
            body_text="公众号独家发布的招募详情",
            site_name="武大青年志愿者", channel="wechat",
        )
        wechat.ai_analysis = {
            "schema_version": "ai_v3", "actionable": True, "audience_match": True,
            "needs_review": False, "category": "志愿活动", "event_key": "独有志愿者招募",
        }
        rendered = render_text(build_digest("2026-09-27", [wechat], {}))
        self.assertIn("来源：武大青年志愿者", rendered)
        self.assertIn("https://mp.weixin.qq.com/s/unique", rendered)

    def test_cross_channel_event_prefers_more_complete_wechat_source(self) -> None:
        website = Notice(
            "s", "通知", "2026-09-27", "讲座通知", "https://example.edu/short",
            body_text="详情待定", site_name="武汉大学官网",
        )
        wechat = Notice(
            "w", "公众号文章", "2026-09-27", "讲座通知",
            "https://mp.weixin.qq.com/s/detail",
            body_text="公众号公布了讲座时间、地点、主讲人、报名方式和座位限制" * 10,
            site_name="青春珞珈", channel="wechat",
        )
        analysis = {
            "schema_version": "ai_v3", "actionable": True, "audience_match": True,
            "needs_review": False, "category": "讲座", "event_key": "测试讲座",
        }
        website.ai_analysis = dict(analysis)
        wechat.ai_analysis = dict(analysis)
        rendered = render_text(build_digest("2026-09-27", [website, wechat], {}))
        self.assertIn("来源：青春珞珈", rendered)
        self.assertNotIn("来源：武汉大学官网", rendered)
        self.assertIn("https://mp.weixin.qq.com/s/detail", rendered)

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

    def test_missing_album_is_skipped_without_claiming_healthy(self) -> None:
        result = MessageAlbumProvider().sync(
            WechatAccount(id="whu", display_name="武汉大学"), {}
        )
        self.assertFalse(result.attempted)
        self.assertEqual(result.status, "unavailable")

    def test_sogou_runs_at_most_once_per_day(self) -> None:
        today = __import__("datetime").datetime.now().date().isoformat()
        result = SogouProvider().sync(
            WechatAccount(id="whu", display_name="武汉大学"),
            {"queried_on": today, "last_status": "degraded", "last_error": "无结果"},
        )
        self.assertFalse(result.attempted)
        self.assertEqual(result.status, "degraded")

    def test_sogou_rebuilds_split_javascript_redirect(self) -> None:
        source = """
        <script>
        var url = 'https://mp.weixin.qq.com';
        url += '/s?__biz=MzA5MjM3MzUxMA%3D%3D';
        url += '\\x26mid=123\\x26idx=1';
        window.location.replace(url);
        </script>
        """
        resolved = SogouProvider._redirect_from_script(source)
        self.assertEqual(
            resolved,
            "https://mp.weixin.qq.com/s?__biz=MzA5MjM3MzUxMA%3D%3D&mid=123&idx=1",
        )

    def test_sogou_does_not_corrupt_timestamp_query_parameter(self) -> None:
        source = """
        <script>
        var url = 'https://mp.weixin.qq.com';
        url += '/s?src=11&timestamp=1790756560&ver=6997&signature=token';
        </script>
        """
        resolved = SogouProvider._redirect_from_script(source)
        self.assertEqual(
            resolved,
            "https://mp.weixin.qq.com/s?src=11&timestamp=1790756560&ver=6997&signature=token",
        )

    def test_sogou_keeps_only_exact_publisher_rows(self) -> None:
        source = """
        <ul class="news-list">
          <li id="sogou_vr_1_box_0"><div class="txt-box"><h3><a href="/link?url=a">官方通知</a></h3>
          <p class="txt-info">报名摘要</p><div class="s-p"><span class="all-time-y2">武汉大学</span>
          <script>timeConvert('1790524800')</script></div></div></li>
          <li id="sogou_vr_1_box_1"><div class="txt-box"><h3><a href="/link?url=b">同名转载</a></h3>
          <div class="s-p"><span class="all-time-y2">武汉大学生</span></div></div></li>
        </ul>
        """
        rows = SogouProvider._search_rows(
            source,
            "https://weixin.sogou.com/weixin?type=2",
            WechatAccount(id="whu", display_name="武汉大学"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "官方通知")
        self.assertEqual(rows[0]["summary"], "报名摘要")
        self.assertEqual(rows[0]["published_at"], "2026-09-28")

    def test_sogou_old_index_results_are_normal_no_new_state(self) -> None:
        source = """
        <li id="sogou_vr_1_box_0"><div class="txt-box"><h3><a href="/link?url=old">旧通知</a></h3>
        <div class="s-p"><span class="all-time-y2">武汉大学</span>
        <script>timeConvert('1609459200')</script></div></div></li>
        """
        response = Mock(status_code=200, text=source, url="https://weixin.sogou.com/weixin?type=2")
        response.raise_for_status = Mock()
        session = Mock()
        session.headers = {}
        session.get.return_value = response
        with patch(
            "whu_notice_research.wechat.providers.requests.Session",
            return_value=session,
        ):
            result = SogouProvider().sync(
                WechatAccount(id="whu", display_name="武汉大学", biz="MzA5MjM3MzUxMA=="),
                {},
            )
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.articles, [])
        self.assertEqual(session.get.call_count, 1)

    def test_sogou_resolved_article_must_match_official_biz(self) -> None:
        account = WechatAccount(
            id="whu",
            display_name="武汉大学",
            biz="MzA5MjM3MzUxMA==",
        )
        redirect = Mock(
            status_code=200,
            url="https://weixin.sogou.com/link?url=token",
            text=(
                "var url='https://mp.weixin.qq.com';"
                "url+='/s?__biz=MzA5MjM3MzUxMA%3D%3D&mid=1&idx=1';"
            ),
        )
        redirect.raise_for_status = Mock()
        article_page = Mock(
            status_code=200,
            url="https://mp.weixin.qq.com/s?__biz=MzA5MjM3MzUxMA%3D%3D&mid=1&idx=1",
            text="var biz = 'MzA5MjM3MzUxMA==';",
        )
        article_page.raise_for_status = Mock()
        session = Mock()
        session.get.side_effect = [redirect, article_page]
        article = SogouProvider._resolve_article(
            session,
            {
                "title": "官方通知",
                "summary": "摘要",
                "published_at": "2026-09-28",
                "redirect_url": redirect.url,
            },
            referer="https://weixin.sogou.com/weixin?type=2",
            account=account,
        )
        self.assertIsNotNone(article)
        self.assertEqual(article.publisher_id, "whu")
        self.assertEqual(parse_article_biz(article.url), account.biz)

    def test_biz_parser_reads_article_html(self) -> None:
        self.assertEqual(
            parse_article_biz("https://mp.weixin.qq.com/s/token", "var biz='MzA5MjM3MzUxMA==';"),
            "MzA5MjM3MzUxMA==",
        )

    def test_weread_cooldown_does_not_repeat_request(self) -> None:
        tomorrow = (__import__("datetime").datetime.now().date() + __import__("datetime").timedelta(days=1)).isoformat()
        provider = WeReadProvider(
            WeReadCredentials(vid="123", accessToken="token"),
            persist_credentials=lambda value: None,
        )
        result = provider.sync(
            WechatAccount(id="whu", display_name="武汉大学", book_id="MP_WXS_1"),
            {"retry_after": tomorrow, "last_error": "-2041"},
        )
        self.assertFalse(result.attempted)
        self.assertEqual(result.status, "rate_limited")

    def test_weread_web_response_supports_multi_article_push(self) -> None:
        account = WechatAccount(
            id="whu", display_name="武汉大学", book_id="MP_WXS_1"
        )
        payload = {
            "reviews": [
                {
                    "subReviews": [
                        {
                            "review": {
                                "reviewId": "MP_WXS_1_token",
                                "createTime": 1790524800,
                                "mpInfo": {
                                    "title": "竞赛报名通知",
                                    "originalId": "article-token",
                                },
                            }
                        }
                    ]
                }
            ]
        }
        articles = _articles_from_weread_web(payload, account)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "竞赛报名通知")
        self.assertEqual(articles[0].url, "https://mp.weixin.qq.com/s/article-token")
        self.assertEqual(articles[0].provider, "weread_web")

    def test_weread_web_provider_reads_latest_cover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "wr_skey",
                                "value": "saved-session",
                                "domain": ".weread.qq.com",
                                "path": "/",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            provider = WeReadWebProvider(state)
            response = Mock(status_code=200)
            response.json.return_value = {
                "name": "武汉大学本科生院",
                "title": "竞赛报名通知",
                "reviewId": "MP_WXS_3943387748_article~token",
                "digest": "报名摘要",
            }
            response.raise_for_status = Mock()
            article_response = Mock(status_code=200)
            article_response.text = "createTime = '2026-09-30 10:00';"
            article_response.raise_for_status = Mock()
            provider.session.get = Mock(side_effect=[response, article_response])
            result = provider.sync(
                WechatAccount(
                    id="undergraduate_school_wechat",
                    display_name="武汉大学本科生院",
                    book_id="MP_WXS_3943387748",
                ),
                {},
            )
            provider.close()
        self.assertEqual(result.status, "healthy")
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.articles[0].title, "竞赛报名通知")
        self.assertEqual(
            result.articles[0].url,
            "https://mp.weixin.qq.com/s/article_token",
        )
        self.assertEqual(result.articles[0].published_at, "2026-09-30")

    def test_weread_web_id_encoder_matches_known_example(self) -> None:
        self.assertEqual(encode_web_id("43208843"), "c9c321c07293508bc9c79df")


if __name__ == "__main__":
    unittest.main()
