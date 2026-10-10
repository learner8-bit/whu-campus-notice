"""Low-frequency repair of failed WeChat bodies; never discovers or sends posts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules_v1 import RULESET_VERSION, decide
from whu_notice_research.storage import NoticeStore, SyncResult
from whu_notice_research.wechat.collector import retry_wechat_content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    with NoticeStore(args.database) as store:
        run_id = store.start_run("wechat", "content-repair")
        try:
            notices = retry_wechat_content(project_root=ROOT, store=store)
            sync = store.sync(
                notices, {n.notice_id: decide(n.title, n.summary, n.body_text, n.source_id) for n in notices},
                ruleset_version=RULESET_VERSION, run_id=run_id,
            )
            store.finish_run(run_id, sync)
        except Exception as exc:
            store.finish_run(run_id, SyncResult(), error=type(exc).__name__)
            raise
        for notice in notices:
            print(f"content repair: source={notice.site_name} chars={len(notice.body_text)} "
                  f"quality={notice.content_quality} failed={bool(notice.fetch_error)}")
        print(f"content repair: attempted={len(notices)} recovered={sum(not n.fetch_error for n in notices)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
