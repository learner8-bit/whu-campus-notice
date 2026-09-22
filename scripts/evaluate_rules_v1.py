from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "undergraduate_school" / "labeled_samples.csv"
OUTPUT = ROOT / "data" / "undergraduate_school" / "rules_v1_evaluation.csv"


def safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def main() -> int:
    with INPUT.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    confusion = Counter((row["manual_label"], row["rule_label"]) for row in rows)

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("manual_label", "rule_label", "count"),
        )
        writer.writeheader()
        for (manual, predicted), count in sorted(confusion.items()):
            writer.writerow({"manual_label": manual, "rule_label": predicted, "count": count})

    print("three-class confusion (manual -> rules_v1):")
    for (manual, predicted), count in sorted(confusion.items()):
        print(f"  {manual:6s} -> {predicted:6s}: {count}")
    print("per-label metrics:")
    for label in ("keep", "review", "filter"):
        tp = confusion[(label, label)]
        predicted_total = sum(count for (manual, predicted), count in confusion.items() if predicted == label)
        actual_total = sum(count for (manual, predicted), count in confusion.items() if manual == label)
        print(
            f"  {label:6s} precision={safe_div(tp, predicted_total):.3f} "
            f"recall={safe_div(tp, actual_total):.3f} support={actual_total}"
        )

    # Production behavior retains both keep and review. This binary view
    # measures whether useful/uncertain information is safely retained.
    tp = sum(1 for row in rows if row["manual_label"] != "filter" and row["rule_label"] != "filter")
    fp = sum(1 for row in rows if row["manual_label"] == "filter" and row["rule_label"] != "filter")
    fn = sum(1 for row in rows if row["manual_label"] != "filter" and row["rule_label"] == "filter")
    tn = sum(1 for row in rows if row["manual_label"] == "filter" and row["rule_label"] == "filter")
    print(
        "retain-vs-filter: "
        f"precision={safe_div(tp, tp + fp):.3f} recall={safe_div(tp, tp + fn):.3f} "
        f"tp={tp} fp={fp} fn={fn} tn={tn}"
    )
    print("mismatches:")
    for row in rows:
        if row["manual_label"] != row["rule_label"]:
            print(f"  {row['manual_label']} <- {row['rule_label']} | {row['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

