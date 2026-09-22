from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.eis import collect_all, load_sources, write_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect historical EIS notices for rule research")
    parser.add_argument("--days", type=int, default=180, help="lookback window; default: 180")
    parser.add_argument(
        "--sources",
        type=Path,
        default=ROOT / "config" / "eis_sources.json",
        help="source configuration JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "eis" / "notices.jsonl",
        help="output JSONL path",
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be positive")

    sources = load_sources(args.sources)
    notices = collect_all(sources, args.days)
    write_jsonl(args.output, notices)
    counts = Counter(row.source_name for row in notices)
    print(f"wrote {len(notices)} notices to {args.output}")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")
    failures = sum(bool(row.fetch_error) for row in notices)
    print(f"detail fetch failures: {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

