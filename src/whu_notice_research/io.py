from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import Notice


def read_jsonl(path: Path) -> list[Notice]:
    with path.open("r", encoding="utf-8") as handle:
        return [Notice.from_dict(json.loads(line)) for line in handle if line.strip()]


def write_jsonl(path: Path, notices: Iterable[Notice]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for notice in notices:
            handle.write(json.dumps(notice.to_dict(), ensure_ascii=False) + "\n")

