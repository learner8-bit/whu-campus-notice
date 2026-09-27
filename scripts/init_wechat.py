"""One-time WeChat discovery, QR login and private credential upload."""

from __future__ import annotations

import argparse
import json
import os
import re
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
    book_id_from_biz,
    load_credentials,
    save_credentials,
)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _discover_account(account: WechatAccount) -> dict[str, str]:
    """Low-frequency Sogou bootstrap; never retries a challenge response."""
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
    from google.api_core.exceptions import NotFound
    from google.cloud import storage

    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(object_name)
    try:
        blob.reload()
        generation = blob.generation
    except NotFound:
        generation = 0
    blob.upload_from_filename(path, if_generation_match=generation)
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
        load_credentials(args.credentials)
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
        payload = json.loads(args.registry.read_text(encoding="utf-8"))
        rows = payload["accounts"]
        for row in rows:
            account = WechatAccount.from_mapping(row)
            try:
                discovered = _discover_account(account)
            except Exception as exc:
                print(f"{account.display_name}: 暂未启用（{exc}）")
                continue
            row.update(discovered)
            row["verified"] = True
            row["enabled"] = True
            rollout_day = date.today() + timedelta(days=7)
            rank = int(row.get("rollout_rank") or 999)
            row["enabled_after"] = date.today().isoformat() if rank <= 3 else rollout_day.isoformat()
            row["shadow_until"] = rollout_day.isoformat()
            print(f"{account.display_name}: 已核验并启用")
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
