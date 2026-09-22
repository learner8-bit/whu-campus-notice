from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules import decide  # noqa: E402


def load_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply draft rules to collected EIS samples")
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "eis" / "notices.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "eis" / "rule_predictions.csv")
    args = parser.parse_args()

    rows = load_rows(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[tuple[str, str]] = Counter()
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "published_at",
                "source_id",
                "source_name",
                "title",
                "rule_label",
                "score",
                "reasons",
                "has_attachment",
                "url",
            ),
        )
        writer.writeheader()
        for row in rows:
            result = decide(row["title"], row.get("summary", ""), row.get("body_text", ""), row["source_id"])
            counts[(row["source_name"], result.label)] += 1
            writer.writerow(
                {
                    "published_at": row["published_at"],
                    "source_id": row["source_id"],
                    "source_name": row["source_name"],
                    "title": row["title"],
                    "rule_label": result.label,
                    "score": result.score,
                    "reasons": "；".join(result.reasons),
                    "has_attachment": bool(row.get("attachments")),
                    "url": row["url"],
                }
            )

    print(f"analyzed {len(rows)} notices; wrote {args.output}")
    for (source, label), count in sorted(counts.items()):
        print(f"{source}\t{label}\t{count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
