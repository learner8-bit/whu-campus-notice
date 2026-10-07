from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .enrichment import enrich_notices
from .io import write_jsonl
from .rules_v1 import RULESET_VERSION, decide
from .sites import WebsiteSourceAdapter
from .storage import NoticeStore, SyncResult
from .wechat.collector import WechatSourceAdapter
from .wechat.models import ProviderResult, WechatAccount


@dataclass
class PipelineResult:
    run_id: int
    sync: SyncResult
    fetched_count: int
    known_before: int
    degraded_sources: list[str] | None = None
    provider_results: list[tuple[WechatAccount, ProviderResult]] | None = None
    initialized_before: bool = False


def run_incremental(
    *,
    site_id: str,
    days: int,
    project_root: Path,
    database: Path,
    new_output: Path | None = None,
) -> PipelineResult:
    with NoticeStore(database) as store:
        initialized_before = store.is_source_initialized(site_id)
        known = store.known_urls(site_id)
        run_id = store.start_run(
            site_id, "incremental" if initialized_before else "bootstrap"
        )
        try:
            degraded_sources: list[str] = []
            provider_results: list[tuple[WechatAccount, ProviderResult]] = []
            if site_id == "wechat":
                collection = WechatSourceAdapter(
                    project_root=project_root,
                    store=store,
                    days=days,
                ).collect({"days": 30 if not initialized_before else days})
                notices = collection.notices
                degraded_sources = collection.degraded_accounts
                provider_results = collection.provider_results
            else:
                notices = WebsiteSourceAdapter(
                    site_id, days=days, project_root=project_root
                ).collect(
                    {
                        "known_urls": list(known),
                        "incremental": initialized_before,
                    }
                ).notices
            # Existing state means adapters returned only unseen notices. On a
            # first bootstrap, enrich only notices published today so that a
            # fresh deployment does not download months of historical files.
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            enrichment_targets = notices if initialized_before else [
                notice for notice in notices if notice.published_at == today
            ]
            enrich_notices(enrichment_targets)
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
            store.mark_source_initialized(site_id)
            if new_output is not None:
                write_jsonl(new_output, sync.new)
            return PipelineResult(
                run_id,
                sync,
                len(notices),
                len(known),
                degraded_sources,
                provider_results,
                initialized_before,
            )
        except Exception as exc:
            empty = SyncResult()
            store.finish_run(run_id, empty, error=f"{type(exc).__name__}: {exc}")
            raise
