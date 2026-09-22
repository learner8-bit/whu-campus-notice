"""One-time correction of historical UC rows saved from verification redirects.

The public list URL is recoverable from the verification page's backUrl.
This does not bypass the site's verification or fabricate missing details.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]


def original_url(verification_url: str) -> str | None:
    parsed = urlparse(verification_url)
    if "/system/resource/visitcode/" not in parsed.path:
        return None
    encoded = parse_qs(parsed.query).get("backUrl", [""])[0]
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8").replace("http://", "https://", 1)
    except (ValueError, UnicodeDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", type=Path, default=ROOT / "data" / "undergraduate_school" / "notices.jsonl"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()]
    repaired = 0
    for row in rows:
        url = original_url(row["url"])
        if url is None:
            continue
        row["url"] = url
        row["fetch_error"] = "BlockedPageError: site verification page (detail unavailable)"
        row["detail_title"] = ""
        row["detail_column"] = ""
        row["body_text"] = ""
        row["attachments"] = []
        row["links"] = []
        repaired += 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"rows={len(rows)} verification_rows_repaired={repaired} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
