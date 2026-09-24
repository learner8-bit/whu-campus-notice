from __future__ import annotations

import json
from io import BytesIO
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.ai_analysis import (  # noqa: E402
    AIConfig,
    analyze_notice,
    load_policy_context,
    normalize_analysis,
)
from whu_notice_research.digest import build_digest, feishu_parts  # noqa: E402
from whu_notice_research.models import Notice  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402


def sample_notice(title: str = "全国大学生电子设计竞赛报名通知") -> Notice:
    return Notice(
        source_id="eis_notice",
        source_name="通知公告",
        published_at="2026-09-23",
        title=title,
        url="https://eis.whu.edu.cn/info/1.htm",
        site_id="eis",
        site_name="武汉大学电子信息学院",
        body_text="现组织本科生报名全国大学生电子设计竞赛。",
    )


class AIAnalysisTests(unittest.TestCase):
    def test_deepseek_defaults_and_user_profile(self) -> None:
        with patch.dict(os.environ, {"AI_API_KEY": "test-key"}, clear=True):
            config = AIConfig.from_environment()
        assert config is not None
        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.model, "deepseek-flash")
        self.assertIn("2025级", config.user_profile)
        self.assertIn("电子信息", config.user_profile)

    def test_value_without_basis_is_removed(self) -> None:
        result = normalize_analysis({
            "actionable": True,
            "audience_match": True,
            "category": "竞赛",
            "value": "保研加10分",
        })
        self.assertEqual(result["value"], "")
        retained = normalize_analysis({
            "actionable": True,
            "audience_match": True,
            "category": "竞赛",
            "value": "按2026届政策参考，国家一等奖10分",
            "policy_basis": "2026届推免文件第5页",
        })
        self.assertIn("10分", retained["value"])

    def test_policy_context_matches_notice_and_includes_scope_warning(self) -> None:
        context = load_policy_context(ROOT, sample_notice())
        self.assertIn("2024年修订", context)
        self.assertIn("第13页", context)
        self.assertIn("2026届", context)
        self.assertIn("只能作为往届参考", context)
        self.assertNotIn("雅思", context)

    def test_ai_filters_audience_mismatch(self) -> None:
        notice = sample_notice("仅限2024级学生申请")
        notice.ai_analysis = {
            "schema_version": "ai_v1",
            "actionable": True,
            "audience_match": False,
            "needs_review": False,
            "category": "本科生事务",
            "event_key": "2024级专项申请",
        }
        digest = build_digest("2026-09-23", [notice], {})
        self.assertEqual(digest.candidate_count, 0)
        self.assertEqual(digest.filtered_count, 1)

    def test_event_key_merges_cross_site_duplicates(self) -> None:
        first = sample_notice()
        second = sample_notice("关于举办全国大学生电子设计竞赛的通知")
        second.url = "https://uc.whu.edu.cn/info/2.htm"
        second.site_id = "undergraduate_school"
        second.site_name = "武汉大学本科生院"
        for notice in (first, second):
            notice.ai_analysis = {
                "schema_version": "ai_v1",
                "actionable": True,
                "audience_match": True,
                "needs_review": False,
                "category": "竞赛",
                "event_key": "2026全国大学生电子设计竞赛",
                "summary": "报名正在进行。",
            }
        digest = build_digest("2026-09-23", [first, second], {})
        self.assertEqual(digest.candidate_count, 1)

    def test_analysis_cache_round_trip(self) -> None:
        notice = sample_notice()
        analysis = normalize_analysis({
            "actionable": True,
            "audience_match": True,
            "category": "竞赛",
            "summary": "报名正在进行。",
        })
        with tempfile.TemporaryDirectory() as directory:
            with NoticeStore(Path(directory) / "state.sqlite3") as store:
                store.save_ai_analysis(
                    notice,
                    analysis,
                    provider="deepseek",
                    model="deepseek-flash",
                    prompt_version="ai_v1",
                )
                cached = store.get_ai_analysis(
                    notice,
                    provider="deepseek",
                    model="deepseek-flash",
                    prompt_version="ai_v1",
                )
        self.assertEqual(cached, analysis)

    @patch("whu_notice_research.ai_analysis.urlopen")
    def test_deepseek_request_uses_json_output(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                content = json.dumps({
                    "actionable": True,
                    "audience_match": True,
                    "needs_review": False,
                    "category": "竞赛",
                    "deadline": "9月30日",
                    "value": "保研加10分",
                    "materials": [],
                    "summary": "报名正在进行。",
                    "event_key": "2026电子设计竞赛",
                    "policy_basis": "不存在的政策第99页",
                }, ensure_ascii=False)
                return json.dumps({
                    "choices": [{"message": {"content": content}}]
                }, ensure_ascii=False).encode("utf-8")

        urlopen_mock.return_value = Response()
        config = AIConfig(
            api_key="secret",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            user_profile="武汉大学电子信息学院2025级本科生",
        )
        result = analyze_notice(
            sample_notice(),
            config,
            policy_context="暂无可用政策。",
        )
        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 2000)
        self.assertEqual(result["deadline"], "9月30日")
        self.assertEqual(result["value"], "")

    @patch("whu_notice_research.ai_analysis.time.sleep")
    @patch("whu_notice_research.ai_analysis.urlopen")
    def test_http_error_reason_is_safe_and_actionable(self, urlopen_mock, _sleep_mock) -> None:
        body = json.dumps({"error": {"message": "Rate limit reached"}}).encode("utf-8")
        urlopen_mock.side_effect = [
            HTTPError("https://api.deepseek.com/chat/completions", 429, "Too Many Requests", None, None),
            HTTPError("https://api.deepseek.com/chat/completions", 429, "Too Many Requests", None, None),
            HTTPError("https://api.deepseek.com/chat/completions", 429, "Too Many Requests", None, BytesIO(body)),
        ]
        config = AIConfig(
            api_key="secret",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            user_profile="test",
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 429: Rate limit reached"):
            analyze_notice(sample_notice(), config, policy_context="none")

    @patch("whu_notice_research.ai_analysis.time.sleep")
    @patch("whu_notice_research.ai_analysis.urlopen")
    def test_empty_json_content_is_retried_with_stronger_prompt(
        self, urlopen_mock, _sleep_mock
    ) -> None:
        class Response:
            def __init__(self, content: str):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                return json.dumps({
                    "choices": [{"message": {"content": self.content}}],
                }, ensure_ascii=False).encode("utf-8")

        valid = json.dumps({
            "actionable": True,
            "audience_match": True,
            "needs_review": False,
            "category": "竞赛",
            "deadline": "",
            "value": "",
            "materials": [],
            "summary": "报名正在进行。",
            "event_key": "2026电子设计竞赛",
            "policy_basis": "",
        }, ensure_ascii=False)
        urlopen_mock.side_effect = [Response(""), Response(valid)]
        config = AIConfig(
            api_key="secret",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            user_profile="test",
        )
        result = analyze_notice(sample_notice(), config, policy_context="none")
        self.assertTrue(result["actionable"])
        self.assertEqual(urlopen_mock.call_count, 2)
        retry_request = urlopen_mock.call_args_list[1].args[0]
        retry_payload = json.loads(retry_request.data.decode("utf-8"))
        self.assertIn("上一尝试为空", retry_payload["messages"][-1]["content"])

    def test_compact_message_has_no_confidence_or_signup_link(self) -> None:
        notice = sample_notice()
        notice.ai_analysis = {
            "schema_version": "ai_v1",
            "actionable": True,
            "audience_match": True,
            "needs_review": False,
            "category": "竞赛",
            "deadline": "9月30日",
            "value": "",
            "materials": [],
            "summary": "报名正在进行。",
            "event_key": "2026电子设计竞赛",
            "policy_basis": "",
        }
        message = "\n".join(feishu_parts(build_digest("2026-09-23", [notice], {})))
        self.assertNotIn("可信度", message)
        self.assertNotIn("报名入口", message)
        self.assertNotIn("对象：", message)
        self.assertEqual(message.count(notice.url), 1)


if __name__ == "__main__":
    unittest.main()
