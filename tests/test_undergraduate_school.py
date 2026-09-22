from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.adapters.undergraduate_school import (  # noqa: E402
    Source,
    discover_source,
    parse_list_page,
    parse_uc_detail_page,
)
from whu_notice_research.eis import BlockedPageError, fetch_text  # noqa: E402
from unittest.mock import patch
from datetime import date

from scripts.repair_uc_challenge_rows import original_url  # noqa: E402


class UndergraduateSchoolParserTests(unittest.TestCase):
    def test_incremental_scans_past_a_fully_known_page(self) -> None:
        first = "https://uc.whu.edu.cn/info/1517/1.htm"
        later = "https://uc.whu.edu.cn/info/1517/2.htm"
        page_one = (
            '<li><a href="../info/1517/1.htm"><span>旧通知</span><i>2026-09-20</i></a></li>'
            '<span class="p_next"><a href="xstz/2.htm">下一页</a></span>'
        )
        page_two = '<li><a href="/info/1517/2.htm"><span>补发通知</span><i>2026-09-19</i></a></li>'
        responses = [
            (page_one, "https://uc.whu.edu.cn/tzgg/xstz.htm"),
            (page_two, "https://uc.whu.edu.cn/tzgg/xstz/2.htm"),
        ]
        with patch("whu_notice_research.adapters.undergraduate_school.fetch_text", side_effect=responses):
            rows = discover_source(
                Source("student", "学生通知", "https://uc.whu.edu.cn/tzgg/xstz.htm"),
                date(2026, 3, 22),
                known_urls={first},
                incremental=True,
            )
        self.assertEqual([row.url for row in rows], [later])

    def test_verification_url_recovers_original_list_url(self) -> None:
        import base64

        original = "http://uc.whu.edu.cn/2022/show.jsp?wbtreeid=1520&wbnewsid=127201"
        encoded = base64.b64encode(original.encode()).decode()
        challenged = "https://uc.whu.edu.cn/system/resource/visitcode/visitcode.jsp?backUrl=" + encoded
        self.assertEqual(
            original_url(challenged),
            "https://uc.whu.edu.cn/2022/show.jsp?wbtreeid=1520&wbnewsid=127201",
        )

    def test_fetch_rejects_verification_redirect(self) -> None:
        class FakeResponse:
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def geturl(self):
                return "https://uc.whu.edu.cn/system/resource/visitcode/visitcode.jsp"

        with patch("whu_notice_research.eis.urlopen", return_value=FakeResponse()):
            with self.assertRaises(BlockedPageError):
                fetch_text("https://uc.whu.edu.cn/2022/show.jsp?a=1", retries=1)

    def test_list_parser(self) -> None:
        markup = """
        <div class="list_txt"><ul class="am-list">
        <li><a href="../info/1517/1.htm"><span>竞赛报名通知</span><i>2026-09-17</i></a></li>
        </ul></div>
        <span class="p_next p_fun"><a href="xstz/23.htm">下页</a></span>
        """
        rows, next_url = parse_list_page(markup, "https://uc.whu.edu.cn/tzgg/xstz.htm")
        self.assertEqual(rows[0]["title"], "竞赛报名通知")
        self.assertEqual(rows[0]["url"], "https://uc.whu.edu.cn/info/1517/1.htm")
        self.assertEqual(next_url, "https://uc.whu.edu.cn/tzgg/xstz/23.htm")

    def test_detail_parser_uses_metadata_and_common_content(self) -> None:
        markup = """
        <meta name="ColumnName" content="学生通知">
        <meta name="ArticleTitle" content="竞赛报名通知">
        <meta name="PubDate" content="2026-09-17 17:28">
        <meta name="Description" content="面向全校本科生">
        <div class="v_news_content"><p>请在系统中报名。</p>
        <a href="/system/_content/download.jsp?id=1">报名表.docx</a></div>
        """
        row = parse_uc_detail_page(markup, "https://uc.whu.edu.cn/info/1517/1.htm")
        self.assertEqual(row["column"], "学生通知")
        self.assertEqual(row["title"], "竞赛报名通知")
        self.assertEqual(row["published_at"], "2026-09-17")
        self.assertEqual(row["summary"], "面向全校本科生")
        self.assertIn("请在系统中报名", row["body_text"])
        self.assertEqual(row["attachments"][0]["text"], "报名表.docx")


if __name__ == "__main__":
    unittest.main()
