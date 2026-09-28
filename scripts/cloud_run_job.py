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
LOCAL_WECHAT_CREDENTIALS = Path("/tmp/whu-notice/private/wechat_credentials.json")
LOCAL_WECHAT_WEB_STATE = Path("/tmp/whu-notice/private/weread_web_state.json")
BASELINE_DATABASE = ROOT / "data" / "state" / "notices.sqlite3"
BEIJING_TIME = timezone(timedelta(hours=8))


def past_daily_send_cutoff(now: datetime | None = None) -> bool:
    """Return true after the 23:30 Beijing-time delivery cutoff."""
    local_now = now or datetime.now(BEIJING_TIME)
    cutoff = local_now.replace(hour=23, minute=30, second=0, microsecond=0)
    return local_now > cutoff


def required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def download_database(bucket: storage.Bucket, object_name: str) -> int:
    LOCAL_DATABASE.parent.mkdir(parents=True, exist_ok=True)
    blob = bucket.blob(object_name)
    try:
        blob.download_to_filename(LOCAL_DATABASE)
        blob.reload()
        print(f"state: downloaded gs://{bucket.name}/{object_name}")
        return int(blob.generation or 0)
    except NotFound:
        shutil.copy2(BASELINE_DATABASE, LOCAL_DATABASE)
        print("state: cloud object absent; copied packaged baseline")
        return 0


def database_is_valid(path: Path) -> bool:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok")
    except sqlite3.Error:
        return False


def upload_database(bucket: storage.Bucket, object_name: str, generation: int) -> None:
    if not LOCAL_DATABASE.exists() or not database_is_valid(LOCAL_DATABASE):
        raise RuntimeError("refusing to upload a missing or invalid SQLite database")
    bucket.blob(object_name).upload_from_filename(
        LOCAL_DATABASE, if_generation_match=generation
    )
    print(f"state: uploaded gs://{bucket.name}/{object_name}")


def run_digest() -> int:
    days = os.getenv("LOOKBACK_DAYS", "1").strip() or "1"
    retention_days = os.getenv("RETENTION_DAYS", "90").strip() or "90"
    digest_day = os.getenv("DIGEST_DAY", "").strip()
    test_mode = environment_flag("TEST_MODE")
    os.environ["WECHAT_CREDENTIALS_FILE"] = str(LOCAL_WECHAT_CREDENTIALS)
    os.environ["WECHAT_WEB_STATE_FILE"] = str(LOCAL_WECHAT_WEB_STATE)
    LOCAL_OUTPUT.mkdir(parents=True, exist_ok=True)
    phase = os.getenv("RUN_PHASE", "send").strip().lower()
    if phase == "wechat-sync":
        command = [
            sys.executable,
            str(ROOT / "scripts" / "wechat_sync.py"),
            "--days",
            days,
            "--database",
            str(LOCAL_DATABASE),
        ]
    else:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "daily_digest.py"),
            "--channels",
            "feishu",
            "--days",
            days,
            "--retention-days",
            retention_days,
            "--database",
            str(LOCAL_DATABASE),
            "--output-dir",
            str(LOCAL_OUTPUT),
        ]
        if phase == "prepare":
            pass
        elif phase == "freeze":
            command.extend(("--no-scan", "--freeze"))
        elif phase == "send":
            command.extend(("--send", "--skip-if-complete", "--no-scan", "--use-freeze"))
        else:
            raise RuntimeError(f"unsupported RUN_PHASE: {phase}")
    if test_mode and phase == "send":
        command.append("--force-send")
    if digest_day:
        command.extend(("--digest-day", digest_day))
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def download_optional_private(
    bucket: storage.Bucket, object_name: str, target: Path
) -> tuple[int, bytes]:
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = bucket.blob(object_name)
    try:
        blob.download_to_filename(target)
        blob.reload()
        return int(blob.generation or 0), target.read_bytes()
    except NotFound:
        return 0, b""


def upload_private_if_changed(
    bucket: storage.Bucket,
    object_name: str,
    target: Path,
    generation: int,
    original: bytes,
) -> None:
    if not target.exists() or target.read_bytes() == original:
        return
    bucket.blob(object_name).upload_from_filename(
        target, if_generation_match=generation
    )
    print(f"credentials: updated gs://{bucket.name}/{object_name}")


def main() -> int:
    test_mode = environment_flag("TEST_MODE")
    if past_daily_send_cutoff() and not test_mode:
        print("send window: past 23:30 Asia/Shanghai; skipping")
        return 0

    bucket_name = required_environment("STATE_BUCKET")
    state_object = os.getenv("STATE_OBJECT", "state/notices.sqlite3").strip()
    credential_object = os.getenv(
        "WECHAT_CREDENTIAL_OBJECT", "private/wechat_credentials.json"
    ).strip()
    web_state_object = os.getenv(
        "WECHAT_WEB_STATE_OBJECT", "private/weread_web_state.json"
    ).strip()
    lock_object = os.getenv("LOCK_OBJECT", "locks/daily-digest.lock").strip()
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    lock = acquire_lock(bucket, lock_object)
    if lock is None:
        print("lock: another digest execution is active; skipping")
        return 0

    try:
        state_generation = download_database(bucket, state_object)
        credential_generation, original_credentials = download_optional_private(
            bucket, credential_object, LOCAL_WECHAT_CREDENTIALS
        )
        web_state_generation, original_web_state = download_optional_private(
            bucket, web_state_object, LOCAL_WECHAT_WEB_STATE
        )
        exit_code = run_digest()
        if test_mode:
            print("test mode: production state was not uploaded")
        else:
            upload_database(bucket, state_object, state_generation)
            upload_private_if_changed(
                bucket,
                credential_object,
                LOCAL_WECHAT_CREDENTIALS,
                credential_generation,
                original_credentials,
            )
            upload_private_if_changed(
                bucket,
                web_state_object,
                LOCAL_WECHAT_WEB_STATE,
                web_state_generation,
                original_web_state,
            )
        return exit_code
    finally:
        try:
            lock.delete(if_generation_match=lock.generation)
            print("lock: released")
        except (NotFound, PreconditionFailed):
            print("lock: lease was already released or replaced", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
