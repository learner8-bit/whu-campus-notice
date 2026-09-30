"""Discover missing WeChat account identities from exact-author public results."""

from __future__ import annotations

import json
import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.wechat.collector import load_accounts  # noqa: E402
from whu_notice_research.wechat.identity import parse_article_biz  # noqa: E402
from whu_notice_research.wechat.models import WechatAccount  # noqa: E402
from whu_notice_research.wechat.providers import BROWSER_HEADERS, SogouProvider  # noqa: E402
from whu_notice_research.wechat.weread import book_id_from_biz  # noqa: E402


def discover(account) -> dict[str, object]:
    search_url = (
        "https://weixin.sogou.com/weixin?type=2&ie=utf8&query="
        + quote(account.display_name)
    )
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    response = session.get(search_url, timeout=20)
    if response.status_code == 429 or SogouProvider._is_challenge(response.text):
        return {"account": account.display_name, "status": "rate_limited"}
    response.raise_for_status()
    rows = SogouProvider._search_rows(response.text, response.url, account)
    if not rows:
        return {"account": account.display_name, "status": "not_found"}

    # Resolve at most the three newest exact-author rows. Stop immediately if
    # Sogou or WeChat requests verification; this tool never bypasses it.
    diagnostics: list[dict[str, object]] = []
    for row in rows[:3]:
        time.sleep(2)
        redirect = session.get(
            row["redirect_url"],
            headers={**BROWSER_HEADERS, "Referer": response.url},
            timeout=20,
            allow_redirects=True,
        )
        if redirect.status_code == 429 or SogouProvider._is_challenge(redirect.text):
            return {"account": account.display_name, "status": "rate_limited"}
        redirect.raise_for_status()
        final_url = redirect.url if "mp.weixin.qq.com/s" in redirect.url else ""
        if not final_url:
            final_url = SogouProvider._redirect_from_script(redirect.text)
        if "mp.weixin.qq.com/s" not in final_url:
            diagnostics.append(
                {
                    "title": row["title"],
                    "stage": "redirect_missing",
                    "status_code": redirect.status_code,
                    "response_url": redirect.url,
                    "response_size": len(redirect.text),
                }
            )
            continue

        source = redirect.text if "mp.weixin.qq.com" in redirect.url else ""
        biz = parse_article_biz(final_url, source)
        if not biz:
            time.sleep(2)
            article = session.get(
                final_url,
                headers={**BROWSER_HEADERS, "Referer": "https://weixin.sogou.com/"},
                timeout=20,
                allow_redirects=True,
            )
            if article.status_code == 429 or SogouProvider._is_challenge(article.text):
                return {"account": account.display_name, "status": "rate_limited"}
            article.raise_for_status()
            final_url = article.url
            source = article.text
            biz = parse_article_biz(final_url, source)
        if not biz:
            diagnostics.append(
                {
                    "title": row["title"],
                    "stage": "article_identity_missing",
                    "status_code": article.status_code,
                    "response_url": article.url,
                    "response_size": len(article.text),
                    "has_biz_marker": "__biz" in article.text or "biz" in article.text,
                }
            )
            continue

        identity_values: dict[str, str] = {}
        for key, aliases in {
            "mid": ("mid", "appmsgid"),
            "idx": ("idx", "itemidx"),
        }.items():
            for alias in aliases:
                match = re.search(
                    rf"(?<![-\w])(?:window\.)?{alias}\s*[:=]\s*['\"]?(\d+)",
                    source,
                    re.I,
                )
                if match:
                    identity_values[key] = match.group(1)
                    break
        if identity_values.get("mid") and identity_values.get("idx"):
            final_url = "https://mp.weixin.qq.com/s?" + urlencode(
                {
                    "__biz": biz,
                    "mid": identity_values["mid"],
                    "idx": identity_values["idx"],
                }
            )

        nickname = ""
        for pattern in (
            r"(?<![-\w])(?:window\.)?(?:nickname|account_nickname)\s*[:=]\s*['\"]([^'\"]+)",
            r"<strong[^>]*class=['\"][^'\"]*profile_nickname[^'\"]*['\"][^>]*>(.*?)</strong>",
        ):
            match = re.search(pattern, source, re.I | re.S)
            if match:
                nickname = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                break
        return {
            "account": account.display_name,
            "status": "found",
            "title": row["title"],
            "published_at": row["published_at"],
            "seed_url": final_url,
            "biz": biz,
            "book_id": book_id_from_biz(biz),
            "page_nickname": nickname,
        }
    return {
        "account": account.display_name,
        "status": "identity_missing",
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="")
    args = parser.parse_args()
    accounts = load_accounts(ROOT / "config" / "wechat_accounts.json")
    targets = [item for item in accounts if not item.biz or not item.book_id]
    if args.account:
        targets = [item for item in targets if item.display_name == args.account]
        if not targets:
            targets = [WechatAccount(id="adhoc", display_name=args.account)]
    results = []
    for index, account in enumerate(targets):
        if index:
            time.sleep(2)
        result = discover(account)
        results.append(result)
        if result["status"] == "rate_limited":
            break
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
