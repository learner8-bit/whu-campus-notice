from __future__ import annotations

import sys
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.digest import build_digest, feishu_parts  # noqa: E402
from whu_notice_research.enrichment import (  # noqa: E402
    Resource,
    _external_http_link,
    enrich_notice,
    extract_docx,
    extract_html,
    extract_xlsx,
    important_highlights,
)
from whu_notice_research.models import Notice  # noqa: E402


def zipped(parts: dict[str, str]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return output.getvalue()


class EnrichmentTests(unittest.TestCase):
    def test_extract_docx_text(self) -> None:
        data = zipped(
            {
                "word/document.xml": """
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body><w:p><w:r><w:t>报名截止：9月30日</w:t></w:r></w:p></w:body>
                </w:document>
                """
            }
        )
        self.assertIn("报名截止：9月30日", extract_docx(data))

    def test_extract_xlsx_shared_and_inline_strings(self) -> None:
        data = zipped(
            {
                "xl/sharedStrings.xml": """
                <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <si><t>本科生</t></si>
                </sst>
                """,
                "xl/worksheets/sheet1.xml": """
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData><row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>申请表</t></is></c></row></sheetData>
                </worksheet>
                """,
            }
        )
        text = extract_xlsx(data)
        self.assertIn("本科生", text)
        self.assertIn("申请表", text)

    def test_html_extraction_ignores_scripts(self) -> None:
        title, text = extract_html(
            "<title>竞赛官网</title><script>secret()</script><p>报名截止：10月1日</p>".encode()
        )
        self.assertEqual(title, "竞赛官网")
        self.assertIn("报名截止", text)
        self.assertNotIn("secret", text)

    def test_highlights_prioritize_conditions(self) -> None:
        result = important_highlights("欢迎参赛。\n报名截止：10月1日。\n申请对象：全日制本科生。")
        self.assertEqual(result, ["报名截止：10月1日。", "申请对象：全日制本科生。"])

    def test_only_other_http_hosts_are_followed(self) -> None:
        notice = "https://uc.whu.edu.cn/info/1.htm"
        self.assertTrue(_external_http_link(notice, "https://contest.example.org/apply"))
        self.assertFalse(_external_http_link(notice, "https://uc.whu.edu.cn/form"))
        self.assertFalse(_external_http_link(notice, "mailto:test@example.org"))

    @patch("whu_notice_research.enrichment.build_opener")
    @patch("whu_notice_research.enrichment._validate_public_url")
    def test_fetch_percent_encodes_unicode_filename(self, validate, build_opener) -> None:
        class Headers:
            def get(self, name: str, default: str = "") -> str:
                return default

            def get_content_type(self) -> str:
                return "application/octet-stream"

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def geturl(self) -> str:
                return "https://example.org/file.docx"

            def read(self, size: int) -> bytes:
                return b""

        opener = build_opener.return_value
        opener.open.return_value = Response()
        from whu_notice_research.enrichment import _fetch_resource

        _fetch_resource("https://example.org/download?filename=报名表.docx", max_bytes=1024)
        requested_url = opener.open.call_args.args[0].full_url
        self.assertIn("%E6%8A%A5%E5%90%8D%E8%A1%A8.docx", requested_url)

    @patch("whu_notice_research.enrichment._fetch_resource")
    def test_notice_gets_attachment_and_external_highlights(self, fetch) -> None:
        docx = zipped(
            {
                "word/document.xml": """
                <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
                  <w:body><w:p><w:r><w:t>报名截止：9月30日。</w:t></w:r></w:p></w:body>
                </w:document>
                """
            }
        )
        fetch.side_effect = (
            Resource(docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "https://uc.whu.edu.cn/a.docx"),
            Resource(
                "<title>竞赛官网</title><p>申请对象：全日制本科生。</p>".encode(),
                "text/html",
                "https://contest.example.org/apply",
            ),
        )
        notice = Notice(
            site_id="undergraduate_school",
            site_name="本科生院",
            source_id="uc_student_notice",
            source_name="学生通知",
            published_at="2026-09-23",
            title="竞赛通知",
            url="https://uc.whu.edu.cn/info/1.htm",
            attachments=[{"text": "报名表.docx", "url": "https://uc.whu.edu.cn/a.docx"}],
            links=[{"text": "竞赛官网", "url": "https://contest.example.org/apply"}],
        )
        enrich_notice(notice)
        self.assertEqual(notice.attachments[0]["status"], "parsed")
        self.assertEqual(notice.links[0]["status"], "parsed")
        self.assertIn("报名截止", notice.summary)
        self.assertIn("全日制本科生", notice.summary)

    def test_feishu_digest_uses_one_original_link_and_compact_fields(self) -> None:
        notice = Notice(
            source_id="uc_student_notice",
            source_name="学生通知",
            published_at="2026-09-23",
            title="关于竞赛报名的通知",
            url="https://uc.whu.edu.cn/info/1.htm",
            site_id="undergraduate_school",
            site_name="武汉大学本科生院",
            attachments=[{
                "text": "报名表.docx",
                "url": "https://uc.whu.edu.cn/download/1",
                "status": "parsed",
                "highlights": "报名截止：9月30日",
            }],
            links=[{
                "text": "竞赛官网",
                "url": "https://contest.example.org/",
                "kind": "external",
                "status": "parsed",
            }],
            ai_analysis={
                "schema_version": "ai_v1",
                "actionable": True,
                "audience_match": True,
                "needs_review": False,
                "category": "竞赛",
                "deadline": "9月30日",
                "value": "综测竞赛板块可能计分",
                "materials": ["报名表"],
                "summary": "完成报名后按要求参赛。",
                "event_key": "2026测试竞赛",
                "policy_basis": "通知原文",
            },
        )
        message = "\n".join(feishu_parts(build_digest("2026-09-23", [notice], {})))
        self.assertIn("【竞赛】关于竞赛报名的通知", message)
        self.assertIn("截止：9月30日", message)
        self.assertIn("材料/附件：报名表、报名表.docx", message)
        self.assertIn("原文：https://uc.whu.edu.cn/info/1.htm", message)
        self.assertTrue(message.endswith("摘要：完成报名后按要求参赛。"))
        self.assertNotIn("https://uc.whu.edu.cn/download/1", message)
        self.assertNotIn("https://contest.example.org/", message)
        self.assertNotIn("可信度", message)
        self.assertNotIn("报名入口", message)


if __name__ == "__main__":
    unittest.main()
