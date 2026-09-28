"""One-time WeRead Web login for fully automatic public-account discovery."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.wechat.weread import encode_web_id  # noqa: E402


def upload_private(path: Path, bucket_name: str, object_name: str) -> None:
    executable = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / (
            "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
        )
        executable = str(candidate) if candidate.exists() else ""
    if not executable:
        raise RuntimeError("未找到 gcloud，网页会话无法上传到私有云存储")
    subprocess.run(
        [executable, "storage", "cp", str(path), f"gs://{bucket_name}/{object_name}"],
        check=True,
    )
    print("网页会话已上传到私有云存储（不会进入 Git 或日志）。")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化微信读书网页自动采集")
    parser.add_argument(
        "--registry", type=Path, default=ROOT / "config" / "wechat_accounts.json"
    )
    parser.add_argument(
        "--state", type=Path, default=ROOT / "data" / "private" / "weread_web_state.json"
    )
    parser.add_argument("--state-bucket", default=os.getenv("STATE_BUCKET", ""))
    parser.add_argument("--object-name", default="private/weread_web_state.json")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    payload = json.loads(args.registry.read_text(encoding="utf-8"))
    accounts = [
        row
        for row in payload.get("accounts", [])
        if row.get("enabled") and row.get("verified") and row.get("book_id")
    ]
    if not accounts:
        raise RuntimeError("没有已核验且已启用的公众号")
    account = accounts[0]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 Playwright，请先安装项目依赖") from exc

    args.state.parent.mkdir(parents=True, exist_ok=True)
    captured: dict = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False)
        options = {"storage_state": str(args.state)} if args.state.exists() else {}
        context = browser.new_context(**options)
        page = context.new_page()

        def remember(response) -> None:
            if "/web/mp/articles" not in response.url:
                return
            try:
                value = response.json()
            except Exception:
                return
            if isinstance(value, dict) and (
                isinstance(value.get("reviews"), list)
                or int(value.get("errCode", value.get("errcode", 0)) or 0) == 0
            ):
                captured.update(value)

        page.on("response", remember)
        reader_url = (
            "https://weread.qq.com/web/mp/reader/"
            + encode_web_id(str(account["book_id"]))
        )
        page.goto("https://weread.qq.com/", wait_until="domcontentloaded", timeout=60_000)
        initial_cookies = {item["name"]: item["value"] for item in context.cookies()}
        already_logged_in = any(
            initial_cookies.get(name) for name in ("wr_vid", "wr_skey", "wr_rt")
        )
        if not already_logged_in:
            try:
                page.get_by_text("登录", exact=True).click(timeout=15_000)
            except Exception:
                # The login control can be rendered as an icon in some layouts.
                page.locator("[class*='login'], [data-testid*='login']").first.click(timeout=5_000)
            page.wait_for_timeout(1500)
            login_page = context.pages[-1]
            login_screenshot = args.state.with_name("weread_web_login.png")
            login_page.screenshot(path=str(login_screenshot), full_page=False)
            print(f"登录界面截图：{login_screenshot}", flush=True)
            print("请在打开的微信读书网页中扫码一次；登录后程序会自动完成。", flush=True)
        else:
            print("已复用保存的网页授权，无需再次扫码。", flush=True)
        deadline = time.time() + max(args.timeout, 30)
        logged_in = already_logged_in
        while time.time() < deadline:
            page.wait_for_timeout(1000)
            cookies = {item["name"]: item["value"] for item in context.cookies()}
            if cookies.get("wr_vid") or cookies.get("wr_skey") or cookies.get("wr_rt"):
                logged_in = True
                break
        if logged_in:
            # Persist first, before any article-list validation. This makes a
            # successful scan recoverable even if the next network call fails.
            context.storage_state(path=str(args.state))
            if args.state_bucket:
                upload_private(args.state, args.state_bucket, args.object_name)
            page.evaluate(
                """async (bookId) => {
                    await fetch('/mp/shelf/addToShelf', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Content-Type': 'application/json;charset=UTF-8'},
                        body: JSON.stringify({bookIds: [bookId]})
                    });
                }""",
                str(account["book_id"]),
            )
            page.goto(reader_url, wait_until="domcontentloaded", timeout=60_000)
            list_deadline = deadline
            while time.time() < list_deadline and not captured:
                page.wait_for_timeout(1000)
        if not captured:
            screenshot = args.state.with_name("weread_web_timeout.png")
            page.screenshot(path=str(screenshot), full_page=True)
            print(f"诊断截图：{screenshot}", flush=True)
            print(f"最后页面：{page.url}｜{page.title()}", flush=True)
            print(
                "Cookie 名称：" + ", ".join(sorted(item["name"] for item in context.cookies())),
                flush=True,
            )
            browser.close()
            if not logged_in:
                raise RuntimeError("网页授权超时，未检测到登录成功")
            raise RuntimeError("已登录，但未获取到公众号文章列表")
        context.storage_state(path=str(args.state))
        browser.close()

    for row in accounts:
        providers = [str(value) for value in row.get("providers", [])]
        providers = [value for value in providers if value != "weread"]
        if "weread_web" not in providers:
            providers.insert(0, "weread_web")
        row["providers"] = providers
    args.registry.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"网页会话验证成功：{account['display_name']}")
    if args.state_bucket:
        # Upload again because navigation may have refreshed the web session.
        upload_private(args.state, args.state_bucket, args.object_name)
    else:
        print("未提供 STATE_BUCKET；网页会话尚未上传到 Cloud Run。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
