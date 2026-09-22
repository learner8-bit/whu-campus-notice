"""Inspect notices first discovered on a China-local calendar day."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.storage import NoticeStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--site")
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data" / "state" / "notices.sqlite3"
    )
    args = parser.parse_args()
    with NoticeStore(args.database) as store:
        rows = store.first_seen_on(args.day, args.site)
    print(f"first_seen_day={args.day} site={args.site or 'all'} count={len(rows)}")
    for row in rows:
        print(f"{row.published_at} [{row.site_name}/{row.source_name}] {row.title} {row.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
