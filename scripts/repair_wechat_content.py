"""Low-frequency repair of failed WeChat bodies; never discovers or sends posts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules_v1 import RULESET_VERSION, decide
from whu_notice_research.storage import NoticeStore, SyncResult
from whu_notice_research.wechat.collector import retry_wechat_content
from whu_notice_research.models import Notice
from whu_notice_research.enrichment import enrich_notices


def report(store):
    """Print aggregates only, never export the database or private state."""
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    cutoff = (datetime.fromisoformat(day) - timedelta(days=7)).date().isoformat()
    sources, errors, groups = Counter(), Counter(), Counter()
    for row in store.connection.execute(
        "SELECT payload_json,first_seen_at FROM notices WHERE site_id='wechat' "
        "AND substr(first_seen_at,1,10)>=?", (cutoff,),
    ):
        n = Notice.from_dict(json.loads(row["payload_json"]))
        if not n.fetch_error and n.content_quality not in {"metadata", "partial"}:
            continue
        sources[n.site_name] += 1
        groups["today" if n.published_at[:10] == day or row["first_seen_at"][:10] == day else "history"] += 1
        reason = next((word for word in ("授权", "限频", "验证", "缺少", "Timeout", "正文不完整") if word in n.fetch_error), "other")
        errors[reason] += 1
    print("content audit: " + json.dumps({"incomplete": sum(sources.values()), "groups": groups,
          "sources": sources, "reasons": errors, "eligible": len(store.pending_wechat_content(day))}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    with NoticeStore(args.database) as store:
        report(store)
        if args.report_only:
            return 0
        # Operator-only override after replacing the private session with a
        # verified valid one. Never used by scheduled discovery or retries.
        if os.getenv("WECHAT_CONTENT_AUTH_REFRESH", "") == "1":
            day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            cutoff = (datetime.fromisoformat(day) - timedelta(days=7)).date().isoformat()
            with store.connection:
                result = store.connection.execute(
                    "UPDATE wechat_content_attempts SET reader_revision='' WHERE notice_id IN "
                    "(SELECT a.notice_id FROM wechat_content_attempts a JOIN notices n "
                    "ON a.notice_id=n.notice_id WHERE substr(n.first_seen_at,1,10)>=? "
                    "AND a.last_error LIKE '%授权%' ORDER BY n.first_seen_at DESC LIMIT 20)",
                    (cutoff,),
                )
            print(f"content repair: fresh session released={result.rowcount}", flush=True)
        run_id = store.start_run("wechat", "content-repair")
        sync = SyncResult()
        def save_one(notice):
            if not notice.fetch_error:
                enrich_notices([notice])
            result = store.sync(
                [notice], {notice.notice_id: decide(notice.title, notice.summary, notice.body_text, notice.source_id)},
                ruleset_version=RULESET_VERSION, run_id=run_id,
            )
            sync.new.extend(result.new)
            sync.updated.extend(result.updated)
            sync.unchanged.extend(result.unchanged)
            print(f"content repair: source={notice.site_name} chars={len(notice.body_text)} "
                  f"quality={notice.content_quality} failed={bool(notice.fetch_error)} "
                  f"content_provider={notice.content_provider} error={notice.fetch_error}", flush=True)
        try:
            notices = retry_wechat_content(project_root=ROOT, store=store, on_notice=save_one)
            store.finish_run(run_id, sync)
        except Exception as exc:
            store.finish_run(run_id, SyncResult(), error=type(exc).__name__)
            raise
        print(f"content repair: attempted={len(notices)} recovered={sum(not n.fetch_error for n in notices)}")
        report(store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
