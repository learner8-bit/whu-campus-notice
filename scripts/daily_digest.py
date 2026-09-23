"""Collect configured sites, build today's digest, optionally send selected channels."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.ai_analysis import AIConfig, attach_ai_analyses  # noqa: E402
from whu_notice_research.digest import (  # noqa: E402
    build_digest,
    content_hash,
    feishu_parts,
    render_html,
    render_text,
)
from whu_notice_research.health import (  # noqa: E402
    HealthIssue,
    ai_health_issues,
    notice_health_issues,
    render_health_alert,
)
from whu_notice_research.notify import (  # noqa: E402
    DeliveryConfig,
    load_env_file,
    send_email,
    send_feishu,
)
from whu_notice_research.pipeline import run_incremental  # noqa: E402
from whu_notice_research.sites import SUPPORTED_SITES  # noqa: E402
from whu_notice_research.storage import NoticeStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily WHU notice digest")
    parser.add_argument("--send", action="store_true", help="Actually send notifications")
    parser.add_argument(
        "--force-send", action="store_true",
        help="Send even when the same digest was already delivered (manual testing only)",
    )
    parser.add_argument(
        "--channels", choices=("feishu", "email", "both"), default="feishu",
        help="Delivery channels; defaults to Feishu only",
    )
    parser.add_argument("--days", type=int, default=30, help="Lookback window for daily scans")
    parser.add_argument(
        "--digest-day",
        help="Build the digest for this YYYY-MM-DD date instead of today",
    )
    parser.add_argument(
        "--preview-latest", type=int, default=0,
        help="Without scanning, preview this many recent useful stored notices",
    )
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data" / "state" / "notices.sqlite3"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "runs")
    args = parser.parse_args()
    if args.days < 1 or args.preview_latest < 0:
        parser.error("--days must be positive and --preview-latest cannot be negative")
    if args.send and args.preview_latest:
        parser.error("--preview-latest cannot be sent; it contains historical notices")
    if args.force_send and not args.send:
        parser.error("--force-send requires --send")
    if args.digest_day:
        try:
            date.fromisoformat(args.digest_day)
        except ValueError:
            parser.error("--digest-day must be a valid YYYY-MM-DD date")

    load_env_file(ROOT / ".env")
    try:
        ai_config = AIConfig.from_environment()
    except ValueError as exc:
        parser.error(str(exc))
    delivery = None
    if args.send:
        try:
            delivery = DeliveryConfig.from_environment(args.channels)
        except ValueError as exc:
            parser.error(str(exc))

    day = args.digest_day or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    baseline_today = []
    scan_status: dict[str, str] = {}
    health_issues: list[HealthIssue] = []
    if not args.preview_latest:
        for site_id in sorted(SUPPORTED_SITES):
            try:
                result = run_incremental(
                    site_id=site_id,
                    days=args.days,
                    project_root=ROOT,
                    database=args.database,
                )
                scan_status[site_id] = "ok"
                if result.known_before == 0:
                    baseline_today.extend(
                        notice for notice in result.sync.new if notice.published_at == day
                    )
                print(
                    f"{site_id}: fetched={result.fetched_count} "
                    f"new={len(result.sync.new)} updated={len(result.sync.updated)}"
                )
                for notice in result.sync.new + result.sync.updated:
                    health_issues.extend(notice_health_issues(notice))
            except Exception as exc:
                # The other site and both delivery channels remain useful.
                scan_status[site_id] = type(exc).__name__
                health_issues.append(
                    HealthIssue("site", site_id, f"{type(exc).__name__}: {str(exc)[:160]}")
                )
                print(f"{site_id}: scan failed ({type(exc).__name__})", file=sys.stderr)

    with NoticeStore(args.database) as store:
        if args.preview_latest:
            all_recent = store.latest_notices(500)
            full = build_digest(day, all_recent, {"preview": "ok"})
            notices = (full.keep + full.review)[: args.preview_latest]
        else:
            notices = store.first_seen_on(day) + store.published_on(day) + baseline_today
        unique = list({item.notice_id: item for item in notices}.values())
        ai_stats = attach_ai_analyses(
            unique,
            store,
            ai_config,
            project_root=ROOT,
            allow_network=not bool(args.preview_latest),
        )
        print(
            f"ai: analyzed={ai_stats.analyzed} cached={ai_stats.cached} "
            f"failed={ai_stats.failed} disabled={ai_stats.disabled}"
        )
        health_issues.extend(ai_health_issues(ai_config, ai_stats, len(unique)))
        digest = build_digest(day, unique, scan_status)
        plain = render_text(digest)
        html_body = render_html(digest)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "preview" if args.preview_latest else day
        text_path = args.output_dir / f"digest_{suffix}.txt"
        html_path = args.output_dir / f"digest_{suffix}.html"
        text_path.write_text(plain, encoding="utf-8")
        html_path.write_text(html_body, encoding="utf-8")
        print(
            f"digest: keep={len(digest.keep)} review={len(digest.review)} "
            f"filtered={digest.filtered_count} text={text_path} html={html_path}"
        )
        if not args.send:
            print("Dry run: no message was sent. Use --send after configuring .env.")
            return 0 if all(value == "ok" for value in scan_status.values()) else 1

        assert delivery is not None
        failed = False
        if args.channels in {"feishu", "both"}:
            for index, part in enumerate(feishu_parts(digest), 1):
                digest_hash = content_hash(part)
                if not args.force_send and store.was_delivered(day, "feishu", digest_hash):
                    print(f"Feishu part {index}: already sent")
                    continue
                try:
                    send_feishu(delivery, part)
                    store.record_delivery(day, "feishu", digest_hash)
                    print(f"Feishu part {index}: sent")
                except RuntimeError as exc:
                    failed = True
                    health_issues.append(HealthIssue("delivery", "飞书", str(exc)))
                    print(f"Feishu part {index}: {exc}", file=sys.stderr)

            if health_issues:
                alert = render_health_alert(day, health_issues)
                alert_hash = content_hash(alert)
                if args.force_send or not store.was_delivered(day, "feishu-health", alert_hash):
                    try:
                        send_feishu(delivery, alert)
                        store.record_delivery(day, "feishu-health", alert_hash)
                        print("Feishu health alert: sent")
                        github_env = os.getenv("GITHUB_ENV", "")
                        if github_env:
                            with open(github_env, "a", encoding="utf-8") as handle:
                                handle.write("HEALTH_ALERT_SENT=true\n")
                    except RuntimeError as exc:
                        failed = True
                        print(f"Feishu health alert: {exc}", file=sys.stderr)
                else:
                    print("Feishu health alert: already sent")

        if args.channels in {"email", "both"}:
            email_hash = content_hash(digest.title + "\n" + plain)
            if not args.force_send and store.was_delivered(day, "email", email_hash):
                print("Email: already sent")
            else:
                try:
                    send_email(delivery, digest.title, plain, html_body)
                    store.record_delivery(day, "email", email_hash)
                    print("Email: sent")
                except RuntimeError as exc:
                    failed = True
                    print(f"Email: {exc}", file=sys.stderr)
        return 1 if failed or any(value != "ok" for value in scan_status.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

