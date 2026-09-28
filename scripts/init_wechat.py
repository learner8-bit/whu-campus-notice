"""One-time WeChat discovery, QR login and private credential upload."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.wechat.models import WechatAccount  # noqa: E402
from whu_notice_research.wechat.weread import (  # noqa: E402
    WeReadAuthClient,
    WeReadMobileClient,
    book_id_from_biz,
    load_credentials,
    save_credentials,
)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·•_\-—]+", "", str(value or "")).casefold()


def _biz_from_seed(source: str, resolved_url: str) -> str:
    query = parse_qs(urlparse(resolved_url.replace("&amp;", "&")).query)
    biz = str((query.get("__biz") or query.get("biz") or [""])[0]).strip()
    if biz:
        return biz
    patterns = (
        r"(?:window\.)?biz\s*=\s*['\"]([^'\"]+)['\"]",
        r"var\s+biz\s*=\s*['\"]([^'\"]+)['\"]",
        r"__biz\s*=\s*['\"]([^'\"]+)['\"]",
        r"(?:window\.)?msg_link\s*=\s*['\"](.+?)['\"]\s*;",
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.I | re.S)
        if not match:
            continue
        value = html.unescape(match.group(1)).replace(r"\/", "/")
        value = value.replace(r"\x26", "&").replace(r"\u0026", "&")
        nested = parse_qs(urlparse(value).query)
        candidate = str((nested.get("__biz") or nested.get("biz") or [value])[0]).strip()
        try:
            book_id_from_biz(candidate)
        except Exception:
            continue
        return candidate
    return ""


def _discover_from_seed(account: WechatAccount, client: WeReadMobileClient) -> dict[str, str]:
    response = requests.get(account.seed_url, headers=HEADERS, timeout=20, allow_redirects=True)
    response.raise_for_status()
    if any(marker in response.text for marker in ("当前环境异常", "完成验证后即可继续访问", "访问过于频繁")):
        raise RuntimeError("微信种子文章要求人工验证，本次初始化停止")
    biz = _biz_from_seed(response.text, response.url)
    if not biz:
        raise RuntimeError("种子文章中没有解析到公众号 biz")
    book_id = book_id_from_biz(biz)
    info = client.get_book_info(book_id)
    upstream_name = str(info.get("title") or "").strip()
    if _normalize_name(upstream_name) != _normalize_name(account.display_name):
        raise RuntimeError(
            f"微信读书返回账号“{upstream_name or '未知'}”，与配置名称不一致"
        )
    return {
        "biz": biz,
        "book_id": book_id,
        "seed_url": account.seed_url,
        "verified_account_name": upstream_name,
    }


def _discover_account(account: WechatAccount, client: WeReadMobileClient) -> dict[str, str]:
    """Low-frequency Sogou bootstrap; never retries a challenge response."""
    if account.seed_url:
        return _discover_from_seed(account, client)
    session = requests.Session()
    search = "https://weixin.sogou.com/weixin?type=1&query=" + quote(account.display_name)
    response = session.get(search, headers=HEADERS, timeout=20)
    if response.status_code == 429 or "请输入验证码" in response.text:
        raise RuntimeError("搜狗要求验证码，本次初始化停止，不会尝试绕过")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    verified = False
    wechat_id = ""
    for block in soup.select(".wx-rb, li"):
        text = block.get_text(" ", strip=True)
        if account.display_name not in text:
            continue
        verified = "认证" in text and ("武汉大学" in text or "武大" in text)
        match = re.search(r"微信号[：:]\s*([A-Za-z0-9_-]+)", text)
        wechat_id = match.group(1) if match else ""
        if verified:
            break
    if not verified:
        raise RuntimeError("未在公开认证信息中确认该账号属于武汉大学")

    article_search = "https://weixin.sogou.com/weixin?type=2&query=" + quote(account.display_name)
    response = session.get(article_search, headers=HEADERS, timeout=20)
    if response.status_code == 429 or "请输入验证码" in response.text:
        raise RuntimeError("搜狗文章搜索要求验证码，本次初始化停止")
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    seed_url = ""
    for link in soup.select("a[href]"):
        block_text = link.parent.get_text(" ", strip=True)
        if account.display_name not in block_text:
            continue
        candidate = str(link.get("href") or "")
        if candidate.startswith("/"):
            candidate = "https://weixin.sogou.com" + candidate
        if candidate.startswith("http"):
            resolved = session.get(candidate, headers=HEADERS, timeout=20, allow_redirects=True)
            if "mp.weixin.qq.com/s" in resolved.url:
                seed_url = resolved.url
                source = resolved.text
                break
    if not seed_url:
        raise RuntimeError("没有找到该认证账号的公开种子文章")
    query = parse_qs(urlparse(seed_url.replace("&amp;", "&")).query)
    biz = str((query.get("__biz") or [""])[0])
    if not biz:
        match = re.search(r"(?:window\.)?(?:biz|__biz)\s*=\s*['\"]([^'\"]+)", source)
        biz = match.group(1) if match else ""
    if not biz:
        raise RuntimeError("种子文章中没有解析到公众号 biz")
    return {
        "wechat_id": wechat_id,
        "biz": biz,
        "book_id": book_id_from_biz(biz),
        "seed_url": seed_url,
    }


def _upload_private(path: Path, bucket_name: str, object_name: str) -> None:
    try:
        from google.api_core.exceptions import NotFound
        from google.auth.exceptions import DefaultCredentialsError
        from google.cloud import storage

        bucket = storage.Client().bucket(bucket_name)
        blob = bucket.blob(object_name)
        try:
            blob.reload()
            generation = blob.generation
        except NotFound:
            generation = 0
        blob.upload_from_filename(path, if_generation_match=generation)
    except (DefaultCredentialsError, OSError):
        executable = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not executable and os.name == "nt":
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / (
                "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
            )
            executable = str(candidate) if candidate.exists() else ""
        if not executable:
            raise RuntimeError("未找到可用的 Google Cloud 登录或 gcloud 命令") from None
        subprocess.run(
            [executable, "storage", "cp", str(path), f"gs://{bucket_name}/{object_name}"],
            check=True,
        )
    print(f"已上传私有凭证：gs://{bucket_name}/{object_name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化武汉大学公众号自动采集")
    parser.add_argument("--registry", type=Path, default=ROOT / "config" / "wechat_accounts.json")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=ROOT / "data" / "private" / "wechat_credentials.json",
    )
    parser.add_argument("--qr-output", type=Path, default=ROOT / "data" / "private" / "weread-login.png")
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--state-bucket", default=os.getenv("STATE_BUCKET", ""))
    parser.add_argument("--credential-object", default="private/wechat_credentials.json")
    args = parser.parse_args()

    if args.credentials.exists():
        credentials = load_credentials(args.credentials)
        print("微信读书凭证已存在且格式有效，跳过扫码。")
    else:
        client = WeReadAuthClient()
        uuid, confirm_url = client.request_qr()
        client.save_qr(args.qr_output, confirm_url)
        print(f"请用微信扫描二维码：{args.qr_output}")
        try:
            os.startfile(args.qr_output)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
        credentials = client.wait_for_login(uuid)
        save_credentials(args.credentials, credentials)
        print("扫码成功，凭证已保存到不会提交 Git 的私有目录。")

    if not args.skip_discovery:
        mobile_client = WeReadMobileClient(credentials)
        payload = json.loads(args.registry.read_text(encoding="utf-8"))
        rows = payload["accounts"]
        for row in rows:
            account = WechatAccount.from_mapping(row)
            try:
                discovered = _discover_account(account, mobile_client)
            except Exception as exc:
                print(f"{account.display_name}: 暂未启用（{exc}）")
                continue
            row.update(discovered)
            row["verified"] = True
            row["enabled"] = True
            print(f"{account.display_name}: 已核验并启用")
        rollout_day = date.today() + timedelta(days=7)
        verified_rows = sorted(
            (row for row in rows if row.get("verified") and row.get("enabled")),
            key=lambda row: int(row.get("rollout_rank") or 999),
        )
        first_wave_ids = {str(row.get("id")) for row in verified_rows[:3]}
        for row in verified_rows:
            row["enabled_after"] = (
                date.today().isoformat()
                if str(row.get("id")) in first_wave_ids
                else rollout_day.isoformat()
            )
            row["shadow_until"] = rollout_day.isoformat()
        args.registry.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.state_bucket:
        _upload_private(args.credentials, args.state_bucket, args.credential_object)
    else:
        print("未提供 STATE_BUCKET；凭证尚未上传到 Cloud Run 使用的私有存储。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
