from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.pipeline import run_incremental  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402


class PipelineTests(unittest.TestCase):
    def test_successful_empty_bootstrap_makes_next_wechat_run_incremental(self) -> None:
        collection = SimpleNamespace(
            notices=[], degraded_accounts=[], provider_results=[]
        )
        adapter = SimpleNamespace(collect=lambda cursor: collection)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            with patch(
                "whu_notice_research.pipeline.WechatSourceAdapter",
                return_value=adapter,
            ), patch("whu_notice_research.pipeline.enrich_notices"):
                first = run_incremental(
                    site_id="wechat",
                    days=1,
                    project_root=ROOT,
                    database=database,
                )
                second = run_incremental(
                    site_id="wechat",
                    days=1,
                    project_root=ROOT,
                    database=database,
                )

            self.assertFalse(first.initialized_before)
            self.assertTrue(second.initialized_before)
            with NoticeStore(database) as store:
                modes = [
                    row["mode"]
                    for row in store.connection.execute(
                        "SELECT mode FROM runs WHERE site_id='wechat' ORDER BY run_id"
                    )
                ]
            self.assertEqual(modes, ["bootstrap", "incremental"])


if __name__ == "__main__":
    unittest.main()
