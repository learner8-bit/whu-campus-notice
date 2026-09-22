from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules import decide  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_rules_v1_source_is_frozen(self) -> None:
        rules_path = ROOT / "src" / "whu_notice_research" / "rules_v1.py"
        digest = hashlib.sha256(rules_path.read_bytes()).hexdigest()
        self.assertEqual(digest, "21cc5d7165fda7906c1f208423ffcc99b6709fe84c39c6535fb8af2a87780c77")

    def test_actionable_competition_is_kept(self) -> None:
        result = decide("关于组织参加全国大学生电子设计竞赛的通知", "请本科生报名")
        self.assertEqual(result.label, "keep")

    def test_completed_event_is_filtered(self) -> None:
        result = decide("我院创新创业大赛圆满落幕", "活动取得圆满成功", source_id="student_activity")
        self.assertEqual(result.label, "filter")

    def test_result_list_is_sent_to_review(self) -> None:
        result = decide("学生助理拟聘用名单公示", "现予以公示")
        self.assertEqual(result.label, "review")

    def test_student_activity_news_is_filtered_even_if_body_mentions_signup(self) -> None:
        result = decide(
            "学院暑期研学活动顺利开展",
            "同学们此前积极报名参加",
            source_id="student_activity",
        )
        self.assertEqual(result.label, "filter")

    def test_graduate_only_notice_is_filtered(self) -> None:
        result = decide("关于组织研究生参加电子设计竞赛的通知", "请报名", source_id="student_notice")
        self.assertEqual(result.label, "filter")

    def test_undergraduate_recommendation_notice_is_not_mistaken_for_graduate_only(self) -> None:
        result = decide("关于启动2027届推荐免试研究生工作的通知", "本科生提交申请")
        self.assertEqual(result.label, "keep")


if __name__ == "__main__":
    unittest.main()
