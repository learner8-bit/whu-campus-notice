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
                            notice.url,
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
                           SET content_hash=?, payload_json=?, ruleset_version=?, rule_label=?,
                               rule_score=?, last_seen_at=?, seen_count=seen_count+1, last_run_id=?
                         WHERE notice_id=?
                        """,
                        (
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
        if site_id is None:
            rows = self.connection.execute(
                "SELECT n.payload_json FROM notices n JOIN runs r ON r.run_id=n.first_run_id "
                "WHERE substr(n.first_seen_at, 1, 10)=? AND r.mode='incremental' "
                "ORDER BY n.published_at DESC, n.title",
                (day,),
            )
        else:
            rows = self.connection.execute(
                "SELECT n.payload_json FROM notices n JOIN runs r ON r.run_id=n.first_run_id "
                "WHERE substr(n.first_seen_at, 1, 10)=? AND r.mode='incremental' "
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
        return {
            "notices": notices,
            "analyses": analyses,
            "deliveries": deliveries,
            "runs": runs,
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
