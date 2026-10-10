from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import Notice
from .rules_v1 import RuleDecision


SHANGHAI = ZoneInfo("Asia/Shanghai")
CONTENT_READER_REVISION = "weread-reader-cover-session-v2"


def now_shanghai() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


@dataclass
class SyncResult:
    new: list[Notice] = field(default_factory=list)
    updated: list[Notice] = field(default_factory=list)
    unchanged: list[Notice] = field(default_factory=list)


class NoticeStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "NoticeStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                unchanged_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS notices (
                notice_id TEXT PRIMARY KEY,
                canonical_url TEXT NOT NULL UNIQUE,
                site_id TEXT NOT NULL,
                site_name TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                published_at TEXT NOT NULL,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ruleset_version TEXT NOT NULL,
                rule_label TEXT NOT NULL,
                rule_score INTEGER NOT NULL,
                first_seen_at TEXT NOT NULL,
                first_run_id INTEGER,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1,
                last_run_id INTEGER,
                FOREIGN KEY(last_run_id) REFERENCES runs(run_id),
                FOREIGN KEY(first_run_id) REFERENCES runs(run_id)
            );
            CREATE INDEX IF NOT EXISTS idx_notices_site_published
                ON notices(site_id, published_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notices_first_seen
                ON notices(first_seen_at);
            CREATE TABLE IF NOT EXISTS deliveries (
                day TEXT NOT NULL,
                channel TEXT NOT NULL,
                digest_hash TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(day, channel, digest_hash)
            );
            CREATE TABLE IF NOT EXISTS ai_analyses (
                notice_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                PRIMARY KEY(notice_id, content_hash, provider, model, prompt_version)
            );
            CREATE TABLE IF NOT EXISTS source_cursors (
                source_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                cursor_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_id, provider)
            );
            CREATE TABLE IF NOT EXISTS source_state (
                site_id TEXT PRIMARY KEY,
                initialized_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_health (
                publisher_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(publisher_id, provider)
            );
            CREATE TABLE IF NOT EXISTS ocr_usage (
                month TEXT PRIMARY KEY,
                cloud_images INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS digest_freezes (
                day TEXT PRIMARY KEY,
                notice_ids_json TEXT NOT NULL,
                frozen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wechat_content_attempts (
                notice_id TEXT PRIMARY KEY,
                last_attempt_day TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                recovered_on TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {
            str(row["name"]) for row in self.connection.execute("PRAGMA table_info(notices)")
        }
        if "first_run_id" not in columns:
            self.connection.execute("ALTER TABLE notices ADD COLUMN first_run_id INTEGER")
            self.connection.execute(
                "UPDATE notices SET first_run_id=last_run_id WHERE first_run_id IS NULL"
            )
        attempt_columns = {
            str(row["name"]) for row in self.connection.execute("PRAGMA table_info(wechat_content_attempts)")
        }
        if "reader_revision" not in attempt_columns:
            self.connection.execute(
                "ALTER TABLE wechat_content_attempts ADD COLUMN reader_revision TEXT NOT NULL DEFAULT ''"
            )
        # Upgrade existing databases without treating all established sources as
        # new again. A successful historical bootstrap/import is sufficient to
        # prove that the one-time baseline has already completed, even if that
        # run found zero notices.
        self.connection.execute(
            "INSERT OR IGNORE INTO source_state(site_id, initialized_at, updated_at) "
            "SELECT site_id, MIN(COALESCE(finished_at, started_at)), "
            "MIN(COALESCE(finished_at, started_at)) FROM runs "
            "WHERE status='success' AND mode IN ('bootstrap', 'import') "
            "GROUP BY site_id"
        )
        self.connection.commit()

    def start_run(self, site_id: str, mode: str) -> int:
        now = now_shanghai()
        cursor = self.connection.execute(
            "INSERT INTO runs(site_id, mode, started_at, status) VALUES (?, ?, ?, 'running')",
            (site_id, mode, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, result: SyncResult, *, error: str = "") -> None:
        now = now_shanghai()
        self.connection.execute(
            """
            UPDATE runs
               SET finished_at=?, status=?, discovered_count=?, new_count=?,
                   updated_count=?, unchanged_count=?, error=?
             WHERE run_id=?
            """,
            (
                now,
                "failed" if error else "success",
                len(result.new) + len(result.updated) + len(result.unchanged),
                len(result.new),
                len(result.updated),
                len(result.unchanged),
                error,
                run_id,
            ),
        )
        self.connection.commit()

    def known_urls(self, site_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT canonical_url, payload_json FROM notices WHERE site_id=?",
            (site_id,),
        )
        return {
            str(row["canonical_url"])
            for row in rows
            if not json.loads(row["payload_json"]).get("fetch_error")
        }

    def is_source_initialized(self, site_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM source_state WHERE site_id=?", (site_id,)
        ).fetchone()
        return row is not None

    def mark_source_initialized(self, site_id: str) -> None:
        now = now_shanghai()
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_state(site_id, initialized_at, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(site_id) DO UPDATE SET "
                "updated_at=excluded.updated_at",
                (site_id, now, now),
            )

    def get_cursor(self, source_id: str, provider: str) -> dict:
        row = self.connection.execute(
            "SELECT cursor_json FROM source_cursors WHERE source_id=? AND provider=?",
            (source_id, provider),
        ).fetchone()
        return json.loads(row["cursor_json"]) if row else {}

    def save_cursor(self, source_id: str, provider: str, cursor: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_cursors(source_id, provider, cursor_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(source_id, provider) DO UPDATE SET "
                "cursor_json=excluded.cursor_json, updated_at=excluded.updated_at",
                (source_id, provider, json.dumps(cursor, ensure_ascii=False), now_shanghai()),
            )

    def record_provider_health(
        self,
        publisher_id: str,
        provider: str,
        status: str,
        *,
        error: str = "",
    ) -> int:
        row = self.connection.execute(
            "SELECT consecutive_failures, last_success_at FROM provider_health "
            "WHERE publisher_id=? AND provider=?",
            (publisher_id, provider),
        ).fetchone()
        failures = 0 if status == "healthy" else int(row["consecutive_failures"] if row else 0) + 1
        last_success = now_shanghai() if status == "healthy" else str(row["last_success_at"] if row else "")
        with self.connection:
            self.connection.execute(
                "INSERT INTO provider_health(publisher_id, provider, status, "
                "consecutive_failures, last_success_at, last_error, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(publisher_id, provider) "
                "DO UPDATE SET status=excluded.status, "
                "consecutive_failures=excluded.consecutive_failures, "
                "last_success_at=excluded.last_success_at, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (
                    publisher_id,
                    provider,
                    status,
                    failures,
                    last_success,
                    error[:500],
                    now_shanghai(),
                ),
            )
        return failures

    def provider_health_rows(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM provider_health ORDER BY publisher_id, provider"
        )
        return [dict(row) for row in rows]

    def freeze_digest(self, day: str, notices: Iterable[Notice]) -> None:
        ids = list(dict.fromkeys(notice.notice_id for notice in notices))
        with self.connection:
            self.connection.execute(
                "INSERT INTO digest_freezes(day, notice_ids_json, frozen_at) VALUES (?, ?, ?) "
                "ON CONFLICT(day) DO UPDATE SET notice_ids_json=excluded.notice_ids_json, "
                "frozen_at=excluded.frozen_at",
                (day, json.dumps(ids), now_shanghai()),
            )

    def frozen_digest(self, day: str) -> list[Notice] | None:
        row = self.connection.execute(
            "SELECT notice_ids_json FROM digest_freezes WHERE day=?", (day,)
        ).fetchone()
        if row is None:
            return None
        ids = json.loads(row["notice_ids_json"])
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"SELECT payload_json FROM notices WHERE notice_id IN ({placeholders})", ids
        )
        return [Notice.from_dict(json.loads(item["payload_json"])) for item in rows]

    def reserve_cloud_ocr(self, *, limit: int = 900, month: str | None = None) -> bool:
        current_month = month or datetime.now(SHANGHAI).strftime("%Y-%m")
        row = self.connection.execute(
            "SELECT cloud_images FROM ocr_usage WHERE month=?", (current_month,)
        ).fetchone()
        used = int(row["cloud_images"] if row else 0)
        if used >= limit:
            return False
        with self.connection:
            self.connection.execute(
                "INSERT INTO ocr_usage(month, cloud_images, updated_at) VALUES (?, 1, ?) "
                "ON CONFLICT(month) DO UPDATE SET cloud_images=cloud_images+1, "
                "updated_at=excluded.updated_at",
                (current_month, now_shanghai()),
            )
        return True

    def pending_wechat_content(self, day: str, *, limit: int = 20) -> list[Notice]:
        """Target only incomplete recent records, independently of discovery cursors.

        One attempt per article/day, at most three per reader revision, within
        seven days of discovery. A new reader gets one bounded upgrade attempt.
        """
        cutoff = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
        rows = self.connection.execute(
            "SELECT n.payload_json FROM notices n LEFT JOIN wechat_content_attempts a "
            "ON a.notice_id=n.notice_id WHERE n.site_id='wechat' "
            "AND substr(n.first_seen_at,1,10)>=? "
            "AND (a.notice_id IS NULL OR a.reader_revision<>? "
            "OR (a.last_attempt_day<? AND a.attempt_count<3)) "
            "ORDER BY n.first_seen_at DESC", (cutoff, CONTENT_READER_REVISION, day),
        )
        pending = []
        for row in rows:
            notice = Notice.from_dict(json.loads(row["payload_json"]))
            if notice.fetch_error or notice.content_quality in {"metadata", "partial"}:
                pending.append(notice)
                if len(pending) >= limit:
                    break
        return pending

    def notice_by_id(self, notice_id: str) -> Notice | None:
        row = self.connection.execute(
            "SELECT payload_json FROM notices WHERE notice_id=?", (notice_id,),
        ).fetchone()
        return Notice.from_dict(json.loads(row["payload_json"])) if row else None

    def wechat_content_attempt_allowed(self, notice_id: str, day: str) -> bool:
        row = self.connection.execute(
            "SELECT last_attempt_day, attempt_count, reader_revision FROM wechat_content_attempts WHERE notice_id=?",
            (notice_id,),
        ).fetchone()
        return row is None or row["reader_revision"] != CONTENT_READER_REVISION or (
            row["last_attempt_day"] < day and row["attempt_count"] < 3
        )

    def record_wechat_content_attempt(self, notice: Notice, day: str, *, recovered: bool = False) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO wechat_content_attempts "
                "(notice_id,last_attempt_day,attempt_count,last_error,recovered_on,reader_revision) "
                "VALUES (?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(notice_id) DO UPDATE SET last_attempt_day=excluded.last_attempt_day, "
                "attempt_count=CASE WHEN reader_revision=excluded.reader_revision "
                "THEN attempt_count+1 ELSE 1 END, last_error=excluded.last_error, "
                "recovered_on=excluded.recovered_on, reader_revision=excluded.reader_revision",
                (notice.notice_id, day, notice.fetch_error, day if recovered else "", CONTENT_READER_REVISION),
            )

    def content_recovered_on(self, day: str) -> list[Notice]:
        rows = self.connection.execute(
            "SELECT n.payload_json FROM notices n JOIN wechat_content_attempts a "
            "ON a.notice_id=n.notice_id WHERE a.recovered_on=? AND a.last_error=''", (day,),
        )
        return [Notice.from_dict(json.loads(row["payload_json"])) for row in rows]

    def pending_recovered_content(self, day: str) -> list[Notice]:
        """Carry repaired articles over until a completed digest includes them.

        A repair after 22:00 must not disappear at midnight. Selection remains
        bounded to seven days; normal AI relevance/deadline filtering applies.
        """
        cutoff = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
        delivered: dict[str, str] = {}
        for row in self.connection.execute(
            "SELECT f.notice_ids_json,f.frozen_at FROM digest_freezes f JOIN deliveries d "
            "ON d.day=f.day WHERE f.day>=? AND f.day<=? AND d.channel='feishu-complete' "
            "AND d.digest_hash='complete'", (cutoff, day),
        ):
            for notice_id in json.loads(row["notice_ids_json"]):
                delivered[notice_id] = max(delivered.get(notice_id, ""), row["frozen_at"])
        rows = self.connection.execute(
            "SELECT n.payload_json FROM notices n JOIN wechat_content_attempts a "
            "ON a.notice_id=n.notice_id WHERE a.recovered_on>=? AND a.recovered_on<=? "
            "AND a.last_error=''", (cutoff, day),
        )
        return [n for row in rows if (
            n := Notice.from_dict(json.loads(row["payload_json"]))
        ).notice_id not in delivered or delivered[n.notice_id] < n.fetched_at]

    def content_failed_on(self, day: str) -> list[Notice]:
        rows = self.connection.execute(
            "SELECT n.payload_json FROM notices n JOIN wechat_content_attempts a "
            "ON a.notice_id=n.notice_id WHERE a.last_attempt_day=? AND a.last_error<>''", (day,),
        )
        return [Notice.from_dict(json.loads(row["payload_json"])) for row in rows]

    def sync(
        self,
        notices: Iterable[Notice],
        decisions: dict[str, RuleDecision],
        *,
        ruleset_version: str,
        run_id: int,
    ) -> SyncResult:
        result = SyncResult()
        now = now_shanghai()
        with self.connection:
            for notice in notices:
                existing = self.connection.execute(
                    "SELECT content_hash FROM notices WHERE notice_id=?",
                    (notice.notice_id,),
                ).fetchone()
                decision = decisions[notice.notice_id]
                payload = json.dumps(notice.to_dict(), ensure_ascii=False, sort_keys=True)
                if existing is None:
                    self.connection.execute(
                        """
                        INSERT INTO notices(
                            notice_id, canonical_url, site_id, site_name, source_id, source_name,
                            published_at, title, content_hash, payload_json, ruleset_version,
                            rule_label, rule_score, first_seen_at, first_run_id,
                            last_seen_at, seen_count, last_run_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            notice.notice_id,
                            notice.canonical_url or notice.url,
                            notice.site_id,
                            notice.site_name,
                            notice.source_id,
                            notice.source_name,
                            notice.published_at,
                            notice.title,
                            notice.content_hash,
                            payload,
                            ruleset_version,
                            decision.label,
                            decision.score,
                            now,
                            run_id,
                            now,
                            run_id,
                        ),
                    )
                    result.new.append(notice)
                else:
                    changed = existing["content_hash"] != notice.content_hash
                    self.connection.execute(
                        """
                        UPDATE notices
                           SET published_at=?, title=?, content_hash=?, payload_json=?, ruleset_version=?, rule_label=?,
                               rule_score=?, last_seen_at=?, seen_count=seen_count+1, last_run_id=?
                         WHERE notice_id=?
                        """,
                        (
                            notice.published_at,
                            notice.title,
                            notice.content_hash,
                            payload,
                            ruleset_version,
                            decision.label,
                            decision.score,
                            now,
                            run_id,
                            notice.notice_id,
                        ),
                    )
                    (result.updated if changed else result.unchanged).append(notice)
        return result

    def count(self, site_id: str | None = None) -> int:
        if site_id is None:
            row = self.connection.execute("SELECT COUNT(*) AS value FROM notices").fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS value FROM notices WHERE site_id=?", (site_id,)
            ).fetchone()
        return int(row["value"])

    def first_seen_on(self, day: str, site_id: str | None = None) -> list[Notice]:
        """Live newly discovered records by China-local day, excluding baseline imports."""
        eligibility = (
            "(r.mode='incremental' OR (r.mode='bootstrap' AND EXISTS ("
            "SELECT 1 FROM runs prior WHERE prior.site_id=r.site_id "
            "AND prior.status='success' AND prior.run_id<r.run_id)))"
        )
        if site_id is None:
            rows = self.connection.execute(
                "SELECT n.payload_json FROM notices n JOIN runs r ON r.run_id=n.first_run_id "
                f"WHERE substr(n.first_seen_at, 1, 10)=? AND {eligibility} "
                "ORDER BY n.published_at DESC, n.title",
                (day,),
            )
        else:
            rows = self.connection.execute(
                "SELECT n.payload_json FROM notices n JOIN runs r ON r.run_id=n.first_run_id "
                f"WHERE substr(n.first_seen_at, 1, 10)=? AND {eligibility} "
                "AND n.site_id=? ORDER BY n.published_at DESC, n.title",
                (day, site_id),
            )
        return [Notice.from_dict(json.loads(row["payload_json"])) for row in rows]

    def was_delivered(self, day: str, channel: str, digest_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM deliveries WHERE day=? AND channel=? AND digest_hash=?",
            (day, channel, digest_hash),
        ).fetchone()
        return row is not None

    def delivery_complete(self, day: str, channel: str) -> bool:
        return self.was_delivered(day, f"{channel}-complete", "complete")

    def mark_delivery_complete(self, day: str, channel: str) -> None:
        self.record_delivery(day, f"{channel}-complete", "complete")

    def record_delivery(self, day: str, channel: str, digest_hash: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO deliveries(day, channel, digest_hash, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (day, channel, digest_hash, now_shanghai()),
            )

    def prune_history(
        self,
        retention_days: int = 90,
        *,
        today: date | None = None,
    ) -> dict[str, int]:
        """Remove state older than the configured retention window."""
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        cutoff = ((today or datetime.now(SHANGHAI).date()) - timedelta(
            days=retention_days
        )).isoformat()
        with self.connection:
            analyses = self.connection.execute(
                "DELETE FROM ai_analyses WHERE notice_id IN ("
                "SELECT notice_id FROM notices WHERE substr(first_seen_at, 1, 10) < ?"
                ")",
                (cutoff,),
            ).rowcount
            notices = self.connection.execute(
                "DELETE FROM notices WHERE substr(first_seen_at, 1, 10) < ?",
                (cutoff,),
            ).rowcount
            deliveries = self.connection.execute(
                "DELETE FROM deliveries WHERE day < ?",
                (cutoff,),
            ).rowcount
            runs = self.connection.execute(
                "DELETE FROM runs WHERE substr(started_at, 1, 10) < ? "
                "AND NOT EXISTS (SELECT 1 FROM notices "
                "WHERE first_run_id=runs.run_id OR last_run_id=runs.run_id)",
                (cutoff,),
            ).rowcount
            old_ocr = self.connection.execute(
                "DELETE FROM ocr_usage WHERE month < ?", (cutoff[:7],)
            ).rowcount
            freezes = self.connection.execute(
                "DELETE FROM digest_freezes WHERE day < ?", (cutoff,)
            ).rowcount
            content_attempts = self.connection.execute(
                "DELETE FROM wechat_content_attempts WHERE notice_id NOT IN "
                "(SELECT notice_id FROM notices)"
            ).rowcount
        return {
            "notices": notices,
            "analyses": analyses,
            "deliveries": deliveries,
            "runs": runs,
            "ocr_months": old_ocr,
            "digest_freezes": freezes,
            "content_attempts": content_attempts,
        }

    def latest_notices(self, limit: int) -> list[Notice]:
        rows = self.connection.execute(
            "SELECT payload_json FROM notices ORDER BY published_at DESC, title LIMIT ?",
            (limit,),
        )
        return [Notice.from_dict(json.loads(row["payload_json"])) for row in rows]

    def get_ai_analysis(
        self,
        notice: Notice,
        *,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> dict | None:
        row = self.connection.execute(
            "SELECT analysis_json FROM ai_analyses "
            "WHERE notice_id=? AND content_hash=? AND provider=? AND model=? "
            "AND prompt_version=?",
            (notice.notice_id, notice.content_hash, provider, model, prompt_version),
        ).fetchone()
        return json.loads(row["analysis_json"]) if row else None

    def save_ai_analysis(
        self,
        notice: Notice,
        analysis: dict,
        *,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO ai_analyses("
                "notice_id, content_hash, provider, model, prompt_version, "
                "analysis_json, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    notice.notice_id,
                    notice.content_hash,
                    provider,
                    model,
                    prompt_version,
                    json.dumps(analysis, ensure_ascii=False, sort_keys=True),
                    now_shanghai(),
                ),
            )

    def published_on(self, day: str) -> list[Notice]:
        """Include same-day announcements even when historical data was pre-imported."""
        rows = self.connection.execute(
            "SELECT payload_json FROM notices WHERE published_at=? ORDER BY title", (day,)
        )
        return [Notice.from_dict(json.loads(row["payload_json"])) for row in rows]
