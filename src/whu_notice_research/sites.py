from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .adapters import undergraduate_school
from .eis import Source as EisSource
from .eis import collect_all as collect_eis
from .models import Notice


SUPPORTED_SITES = {"eis", "undergraduate_school"}


def collect_site(
    site_id: str,
    *,
    days: int,
    project_root: Path,
    known_urls: set[str] | None = None,
    incremental: bool = False,
) -> list[Notice]:
    if site_id == "eis":
        path = project_root / "config" / "eis_sources.json"
        with path.open("r", encoding="utf-8") as handle:
            sources = [EisSource(**row) for row in json.load(handle)]
        return collect_eis(
            sources,
            days,
            known_urls=known_urls,
            incremental=incremental,
        )
    if site_id == "undergraduate_school":
        path = project_root / "config" / "undergraduate_school_sources.json"
        with path.open("r", encoding="utf-8") as handle:
            sources = [undergraduate_school.Source(**row) for row in json.load(handle)]
        return undergraduate_school.collect_all(
            sources,
            date.today() - timedelta(days=days),
            known_urls=known_urls,
            incremental=incremental,
        )
    raise ValueError(f"unsupported site: {site_id}; choose from {sorted(SUPPORTED_SITES)}")

