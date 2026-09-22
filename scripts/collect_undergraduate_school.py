from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.adapters.undergraduate_school import Source, collect_all  # noqa: E402
from whu_notice_research.io import write_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Wuhan University undergraduate-school notices")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument(
        "--sources",
        type=Path,
        default=ROOT / "config" / "undergraduate_school_sources.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "undergraduate_school" / "notices.jsonl",
    )
    args = parser.parse_args()
    with args.sources.open("r", encoding="utf-8") as handle:
        sources = [Source(**row) for row in json.load(handle)]
    notices = collect_all(sources, date.today() - timedelta(days=args.days))
    write_jsonl(args.output, notices)
    print(f"wrote {len(notices)} notices to {args.output}")
    for name, count in sorted(Counter(row.source_name for row in notices).items()):
        print(f"  {name}: {count}")
    print(f"detail fetch failures: {sum(bool(row.fetch_error) for row in notices)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

