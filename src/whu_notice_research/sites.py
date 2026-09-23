from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .adapters import generic_vsb, second_classroom, undergraduate_school
from .eis import Source as EisSource
from .eis import collect_all as collect_eis
from .models import Notice


GENERIC_SITES = {
    "whu_main",
    "student_aid",
    "youth_league",
    "international_office",
    "service_center",
    "information_disclosure",
    "science_technology",
}
SUPPORTED_SITES = {"eis", "undergraduate_school", "second_classroom", *GENERIC_SITES}


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
    if site_id == "second_classroom":
        return second_classroom.collect_all(
            date.today() - timedelta(days=days),
            known_urls=known_urls,
            incremental=incremental,
        )
    if site_id in GENERIC_SITES:
        path = project_root / "config" / "campus_sources.json"
        with path.open("r", encoding="utf-8") as handle:
            group = json.load(handle)[site_id]
        sources = [
            generic_vsb.Source(
                **row,
                site_id=site_id,
                site_name=group["site_name"],
            )
            for row in group["sources"]
        ]
        return generic_vsb.collect_all(
            sources,
            date.today() - timedelta(days=days),
            known_urls=known_urls,
            incremental=incremental,
        )
    raise ValueError(f"unsupported site: {site_id}; choose from {sorted(SUPPORTED_SITES)}")

