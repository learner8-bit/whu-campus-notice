from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .io import write_jsonl
from .rules_v1 import RULESET_VERSION, decide
from .sites import collect_site
from .storage import NoticeStore, SyncResult


@dataclass
class PipelineResult:
    run_id: int
    sync: SyncResult
    fetched_count: int
    known_before: int


def run_incremental(
    *,
    site_id: str,
    days: int,
    project_root: Path,
    database: Path,
    new_output: Path | None = None,
) -> PipelineResult:
    with NoticeStore(database) as store:
        known = store.known_urls(site_id)
        run_id = store.start_run(site_id, "incremental" if known else "bootstrap")
        try:
            notices = collect_site(
                site_id,
                days=days,
                project_root=project_root,
                known_urls=known,
                incremental=bool(known),
            )
            decisions = {
                notice.notice_id: decide(
                    notice.title,
                    notice.summary,
                    notice.body_text,
                    notice.source_id,
                )
                for notice in notices
            }
            sync = store.sync(
                notices,
                decisions,
                ruleset_version=RULESET_VERSION,
                run_id=run_id,
            )
            store.finish_run(run_id, sync)
            if new_output is not None:
                write_jsonl(new_output, sync.new)
            return PipelineResult(run_id, sync, len(notices), len(known))
        except Exception as exc:
            empty = SyncResult()
            store.finish_run(run_id, empty, error=f"{type(exc).__name__}: {exc}")
            raise

