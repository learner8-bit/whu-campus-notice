"""Run only the WeChat discovery stage; used by pre-digest Cloud schedules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.pipeline import run_incremental  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()
    result = run_incremental(
        site_id="wechat",
        days=args.days,
        project_root=ROOT,
        database=args.database,
    )
    print(
        f"wechat: fetched={result.fetched_count} new={len(result.sync.new)} "
        f"updated={len(result.sync.updated)} degraded={len(result.degraded_sources or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
