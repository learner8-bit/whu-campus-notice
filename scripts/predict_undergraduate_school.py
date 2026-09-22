"""Record frozen rules_v1 predictions before comparing with human labels."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.io import read_jsonl  # noqa: E402
from whu_notice_research.rules_v1 import RULESET_VERSION, decide  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, default=ROOT / "data" / "undergraduate_school" / "notices.jsonl"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "undergraduate_school" / "rules_v1_predictions.csv",
    )
    args = parser.parse_args()
    rows = read_jsonl(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "published_at", "source_id", "source_name", "title", "ruleset_version",
                "rule_label", "score", "reasons", "has_attachment", "detail_status", "url",
            ),
        )
        writer.writeheader()
        for row in rows:
            result = decide(row.title, row.summary, row.body_text, row.source_id)
            writer.writerow(
                {
                    "published_at": row.published_at,
                    "source_id": row.source_id,
                    "source_name": row.source_name,
                    "title": row.title,
                    "ruleset_version": RULESET_VERSION,
                    "rule_label": result.label,
                    "score": result.score,
                    "reasons": "；".join(result.reasons),
                    "has_attachment": bool(row.attachments),
                    "detail_status": "blocked" if row.fetch_error else "parsed",
                    "url": row.url,
                }
            )
    print(f"wrote {len(rows)} predictions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
