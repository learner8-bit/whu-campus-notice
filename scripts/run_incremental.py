from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.pipeline import run_incremental  # noqa: E402
from whu_notice_research.sites import SUPPORTED_SITES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally collect and deduplicate notices")
    parser.add_argument("--site", choices=sorted(SUPPORTED_SITES), required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "state" / "notices.sqlite3",
    )
    parser.add_argument("--new-output", type=Path)
    args = parser.parse_args()
    output = args.new_output or ROOT / "data" / "runs" / f"{args.site}_new.jsonl"
    result = run_incremental(
        site_id=args.site,
        days=args.days,
        project_root=ROOT,
        database=args.database,
        new_output=output,
    )
    print(
        f"run={result.run_id} known_before={result.known_before} fetched={result.fetched_count} "
        f"new={len(result.sync.new)} updated={len(result.sync.updated)} "
        f"unchanged={len(result.sync.unchanged)} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
