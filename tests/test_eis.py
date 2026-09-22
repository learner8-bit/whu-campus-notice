from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.eis import parse_detail_page, parse_list_page  # noqa: E402


class EisParserTests(unittest.TestCase):
    def test_list_parser_extracts_notice_and_next_page(self) -> None:
        markup = """
        <li class="clearfix" id="line_u13_0">
          <p class="month">2026.09.22</p>
          <h3 class="title"><a href="../info/1411/1.htm" title="奖学金通知">奖学金通知</a></h3>
          <p class="des"><a href="../info/1411/1.htm">请同学们申请。</a></p>
        </li>
        <span class="p_next p_fun"><a href="xgtz/29.htm">下页</a></span>
        """
        rows, next_url = parse_list_page(markup, "http://eis.whu.edu.cn/xsgz/xgtz.htm")
        self.assertEqual(rows[0]["date"], "2026.09.22")
        self.assertEqual(rows[0]["title"], "奖学金通知")
        self.assertEqual(rows[0]["summary"], "请同学们申请。")
        self.assertEqual(rows[0]["url"], "http://eis.whu.edu.cn/info/1411/1.htm")
        self.assertEqual(next_url, "http://eis.whu.edu.cn/xsgz/xgtz/29.htm")

    def test_detail_parser_extracts_content_and_attachment(self) -> None:
        markup = """
        <h3 class="chyg-column-h3">学工通知</h3>
        <h3 class="title">奖学金通知</h3>
        <div class="time">2026-09-22</div>
        <div class="v_news_content"><p>请在周五前提交。</p>
        <a href="/system/_content/download.jsp?id=1">申请表.docx</a></div>
        """
        row = parse_detail_page(markup, "http://eis.whu.edu.cn/info/1411/1.htm")
        self.assertEqual(row["title"], "奖学金通知")
        self.assertEqual(row["column"], "学工通知")
        self.assertEqual(row["published_at"], "2026-09-22")
        self.assertIn("请在周五前提交", row["body_text"])
        self.assertEqual(row["attachments"][0]["text"], "申请表.docx")

    def test_attachment_after_content_is_still_extracted(self) -> None:
        markup = """
        <h3 class="title">奖学金通知</h3>
        <div class="v_news_content"><p>正文</p></div>
        <div class="attachment"><a href="/system/_content/download.jsp?id=2">附件.docx</a></div>
        """
        row = parse_detail_page(markup, "http://eis.whu.edu.cn/info/1411/2.htm")
        self.assertEqual(row["attachments"][0]["text"], "附件.docx")

    def test_duplicate_attachment_links_are_deduplicated(self) -> None:
        markup = """
        <h3 class="title">通知</h3><div class="v_news_content"><p>正文</p></div>
        <a href="/system/_content/download.jsp?id=2">附件.docx</a>
        <a href="/system/_content/download.jsp?id=2">附件.docx</a>
        """
        row = parse_detail_page(markup, "http://eis.whu.edu.cn/info/1411/2.htm")
        self.assertEqual(len(row["attachments"]), 1)

    def test_duplicate_attachment_names_across_backends_are_deduplicated(self) -> None:
        markup = """
        <h3 class="title">通知</h3><div class="v_news_content"><p>正文</p></div>
        <a href="/index/downLoad?filepath=a.docx">申请表.docx</a>
        <a href="/system/_content/download.jsp?id=3">申请表.docx</a>
        """
        row = parse_detail_page(markup, "http://eis.whu.edu.cn/info/1411/2.htm")
        self.assertEqual(len(row["attachments"]), 1)

    def test_style_script_text_is_not_in_body_and_embedded_pdf_is_attachment(self) -> None:
        markup = """
        <div class="v_news_content"><style>p { color: red; }</style><p>正文内容</p>
        <script>showVsbpdfIframe('/__local/report.pdf','100%');</script></div>
        """
        row = parse_detail_page(markup, "https://uc.whu.edu.cn/info/1.htm")
        self.assertEqual(row["body_text"], "正文内容")
        self.assertEqual(row["attachments"][0]["text"], "report.pdf")


if __name__ == "__main__":
    unittest.main()
