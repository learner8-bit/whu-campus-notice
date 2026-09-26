"""Cloud Run Job wrapper with persistent SQLite state in Cloud Storage."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATABASE = Path("/tmp/whu-notice/notices.sqlite3")
LOCAL_OUTPUT = Path("/tmp/whu-notice/runs")
BASELINE_DATABASE = ROOT / "data" / "state" / "notices.sqlite3"


def required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def acquire_lock(bucket: storage.Bucket, object_name: str) -> storage.Blob | None:
    """Acquire a GCS lease so overlapping schedules cannot double-send."""
    lock = bucket.blob(object_name)
    payload = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        lock.upload_from_string(payload, if_generation_match=0)
        lock.reload()
        return lock
    except PreconditionFailed:
        try:
            lock.reload()
        except NotFound:
            return acquire_lock(bucket, object_name)
        updated = lock.updated
        if updated and datetime.now(timezone.utc) - updated > timedelta(minutes=45):
            try:
                lock.delete(if_generation_match=lock.generation)
            except (NotFound, PreconditionFailed):
                return None
            return acquire_lock(bucket, object_name)
        return None


def download_database(bucket: storage.Bucket, object_name: str) -> None:
    LOCAL_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    blob = bucket.blob(object_name)
    try:
        blob.download_to_filename(LOCAL_DATABASE)
        print(f"state: downloaded gs://{bucket.name}/{object_name}")
    except NotFound:
        shutil.copy2(BASELINE_DATABASE, LOCAL_DATABASE)
        print("state: cloud object absent; copied packaged baseline")


def database_is_valid(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok")
    except sqlite3.Error:
        return False


def upload_database(bucket: storage.Bucket, object_name: str) -> None:
    if not LOCAL_DATABASE.exists() or not database_is_valid(LOCAL_DATABASE):
        raise RuntimeError("refusing to upload a missing or invalid SQLite database")
    bucket.blob(object_name).upload_from_filename(LOCAL_DATABASE)
    print(f"state: uploaded gs://{bucket.name}/{object_name}")


def run_digest() -> int:
    days = os.getenv("LOOKBACK_DAYS", "1").strip() or "1"
    retention_days = os.getenv("RETENTION_DAYS", "90").strip() or "90"
    LOCAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "daily_digest.py"),
        "--send",
        "--channels",
        "feishu",
        "--days",
        days,
        "--retention-days",
        retention_days,
        "--skip-if-complete",
        "--database",
        str(LOCAL_DATABASE),
        "--output-dir",
        str(LOCAL_OUTPUT),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main() -> int:
    bucket_name = required_environment("STATE_BUCKET")
    state_object = os.getenv("STATE_OBJECT", "state/notices.sqlite3").strip()
    lock_object = os.getenv("LOCK_OBJECT", "locks/daily-digest.lock").strip()
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    lock = acquire_lock(bucket, lock_object)
    if lock is None:
        print("lock: another digest execution is active; skipping")
        return 0

    try:
        download_database(bucket, state_object)
        exit_code = run_digest()
        upload_database(bucket, state_object)
        return exit_code
    finally:
        try:
            lock.delete(if_generation_match=lock.generation)
            print("lock: released")
        except (NotFound, PreconditionFailed):
            print("lock: lease was already released or replaced", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
