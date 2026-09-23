from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.sites import SUPPORTED_SITES  # noqa: E402


class SiteKeywordProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = ROOT / "config" / "site_keyword_profiles.json"
        cls.data = json.loads(path.read_text(encoding="utf-8"))

    def test_every_supported_site_has_a_profile(self) -> None:
        self.assertEqual(set(self.data["sites"]), set(SUPPORTED_SITES))

    def test_profiles_are_explicitly_research_only(self) -> None:
        self.assertEqual(self.data["status"], "research_only")
        self.assertIn("尚未接入线上判定", self.data["scope"])

    def test_each_profile_has_three_keyword_groups(self) -> None:
        for site_id, profile in self.data["sites"].items():
            with self.subTest(site_id=site_id):
                self.assertTrue(profile["positive_keywords"])
                self.assertTrue(profile["review_keywords"])
                self.assertTrue(profile["negative_keywords"])
                self.assertIn("pattern", profile)


if __name__ == "__main__":
    unittest.main()
