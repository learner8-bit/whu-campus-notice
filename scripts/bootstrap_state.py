from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.io import read_jsonl  # noqa: E402
from whu_notice_research.rules_v1 import RULESET_VERSION, decide  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import existing research JSONL into the state database")
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data" / "state" / "notices.sqlite3"
    )
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    with NoticeStore(args.database) as store:
        for path in args.inputs:
            notices = read_jsonl(path)
            site_id = notices[0].site_id if notices else "unknown"
            run_id = store.start_run(site_id, "import")
            decisions = {
                item.notice_id: decide(item.title, item.summary, item.body_text, item.source_id)
                for item in notices
            }
            result = store.sync(
                notices,
                decisions,
                ruleset_version=RULESET_VERSION,
                run_id=run_id,
            )
            store.finish_run(run_id, result)
            print(
                f"{path}: new={len(result.new)} updated={len(result.updated)} "
                f"unchanged={len(result.unchanged)}"
            )
        print(f"database total={store.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

