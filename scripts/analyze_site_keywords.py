from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze site-specific keyword profiles")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "state" / "notices.sqlite3",
    )
    parser.add_argument(
        "--profiles",
        type=Path,
        default=ROOT / "config" / "site_keyword_profiles.json",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def unique_words(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(word for group in groups for word in group))


def effective_keywords(site: dict, source_id: str, kind: str) -> list[str]:
    source = site.get("sources", {}).get(source_id, {})
    return unique_words(site.get(kind, []), source.get(kind, []))


def matches(text: str, words: list[str]) -> list[str]:
    return [word for word in words if word in text]


def render(database: Path, profiles_path: Path) -> str:
    profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT site_id, source_id, title, rule_label, payload_json FROM notices "
        "ORDER BY site_id, published_at DESC, title"
    ).fetchall()
    connection.close()

    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["site_id"])].append(row)

    lines = [
        "# 分站关键词命中分析",
        "",
        f"词库版本：`{profiles['version']}`；状态：`{profiles['status']}`。",
        "此报告只统计候选词在现有样本中的命中情况，不改变线上筛选结果。",
        "",
        "| 站点 | 样本 | rules_v1 保留/复核/过滤 | 详情受阻 | 分站正向命中 | 复核词命中 | 降权词命中 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    details: list[str] = []
    for site_id, site in profiles["sites"].items():
        site_rows = grouped.get(site_id, [])
        labels = Counter(str(row["rule_label"]) for row in site_rows)
        kinds = ("positive_keywords", "review_keywords", "negative_keywords")
        hit_counters = {kind: Counter() for kind in kinds}
        hit_titles = {kind: [] for kind in kinds}
        blocked = 0
        unmatched: list[str] = []
        for row in site_rows:
            payload = json.loads(row["payload_json"])
            if payload.get("fetch_error"):
                blocked += 1
            text = " ".join((str(row["title"]), payload.get("summary", "")))
            row_hit = False
            for kind in kinds:
                found = matches(text, effective_keywords(site, str(row["source_id"]), kind))
                if found:
                    row_hit = True
                    hit_counters[kind].update(found)
                    hit_titles[kind].append(str(row["title"]))
            if not row_hit:
                unmatched.append(str(row["title"]))
        counts = {kind: len(hit_titles[kind]) for kind in kinds}
        label_text = f"{labels['keep']}/{labels['review']}/{labels['filter']}"
        lines.append(
            f"| `{site_id}` | {len(site_rows)} | {label_text} | {blocked} | "
            f"{counts['positive_keywords']} | {counts['review_keywords']} | "
            f"{counts['negative_keywords']} |"
        )

        details.extend(("", f"## {site['site_name']} (`{site_id}`)", "", site["pattern"], ""))
        for heading, kind in (
            ("高频正向命中", "positive_keywords"),
            ("高频复核命中", "review_keywords"),
            ("高频降权命中", "negative_keywords"),
        ):
            top = "、".join(
                f"{word}×{count}" for word, count in hit_counters[kind].most_common(12)
            )
            details.append(f"- {heading}：{top or '当前样本无命中'}")
        details.append(f"- 尚未命中任何分站词：{len(unmatched)} 条")
        for title in unmatched[:5]:
            details.append(f"  - {title}")

    lines.extend(details)
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    report = render(args.database, args.profiles)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
