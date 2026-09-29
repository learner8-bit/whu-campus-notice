"""Minimal WeRead public-account client.

Protocol handling is adapted from johamwon/wechrss (MIT License). The local
copy intentionally excludes its Web UI and server so credentials never need to
be exposed through an administration endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .identity import wechat_article_key
from .models import WechatAccount, WechatArticle


WEREAD_BASE = "https://i.weread.qq.com"
WX_APPID = "wxab9b71ad2b90ff34"
WX_SCOPE = "snsapi_userinfo,snsapi_timeline,snsapi_friend"
PROFILE_NAME = "eink-2.1.2"
DEVICE_NAME = "BOOX"
DEVICE_TYPE = 3
VERSION_HEADERS = {
    "baseapi": "30",
    "appver": "2.1.2.10245900",
    "basever": "2.1.2.10245900",
    "osver": "11",
    "channelId": "900",
    "User-Agent": "WeRead/2.1.2 WRBrand/Onyx wr_eink Dalvik/2.1.0 (Linux; Android 11)",
}


class WeReadError(RuntimeError):
    pass


class WeReadAuthExpired(WeReadError):
    pass


class WeReadRateLimited(WeReadError):
    pass


@dataclass
class WeReadCredentials:
    vid: str
    accessToken: str
    refreshToken: str = ""
    deviceId: str = ""
    deviceName: str = DEVICE_NAME
    profile: str = PROFILE_NAME
    skey: str = ""
    wxAccessToken: str = ""
    guestToken: str = ""
    syncKey: int = 0
    name: str = ""
    updatedAt: int = 0

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "WeReadCredentials":
        return cls(
            vid=str(value.get("vid") or value.get("v_id") or "").strip(),
            accessToken=str(value.get("accessToken") or value.get("access_token") or "").strip(),
            refreshToken=str(value.get("refreshToken") or value.get("refresh_token") or "").strip(),
            deviceId=str(value.get("deviceId") or value.get("device_id") or "").strip(),
            deviceName=str(value.get("deviceName") or DEVICE_NAME).strip() or DEVICE_NAME,
            profile=str(value.get("profile") or PROFILE_NAME).strip() or PROFILE_NAME,
            skey=str(value.get("skey") or ""),
            wxAccessToken=str(value.get("wxAccessToken") or ""),
            guestToken=str(value.get("guestToken") or ""),
            syncKey=int(value.get("syncKey") or 0),
            name=str(value.get("name") or ""),
            updatedAt=int(value.get("updatedAt") or 0),
        )

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["updatedAt"] = int(time.time())
        return value

    def validate(self) -> None:
        if not self.vid.isdigit() or not self.accessToken:
            raise WeReadAuthExpired("微信读书凭证不完整，需要重新扫码")


def load_credentials(path: Path) -> WeReadCredentials:
    value = json.loads(path.read_text(encoding="utf-8"))
    credentials = WeReadCredentials.from_mapping(value)
    credentials.validate()
    return credentials


def save_credentials(path: Path, credentials: WeReadCredentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(credentials.as_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _new_device_id() -> str:
    return "eink334691225" + str(secrets.randbits(63)).zfill(19)


def _signature(timestamp_ms: int, device_id: str, random_value: int) -> str:
    return hashlib.sha256(f"{timestamp_ms}{device_id}{random_value}".encode()).hexdigest()


class WeReadAuthClient:
    def __init__(self, *, timeout: float = 25.0, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()

    def _json(self, response: requests.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise WeReadError(f"{label} 返回非 JSON 数据") from exc
        if not isinstance(payload, dict):
            raise WeReadError(f"{label} 返回格式异常")
        return payload

    def request_qr(self) -> tuple[str, str]:
        response = self.session.get(
            f"{WEREAD_BASE}/wxticket",
            params={"nonceStr": "weread"},
            headers=VERSION_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        ticket = self._json(response, "二维码 ticket")
        response = self.session.get(
            "https://open.weixin.qq.com/connect/sdk/qrconnect",
            params={
                "appid": WX_APPID,
                "noncestr": "weread",
                "timestamp": str(ticket.get("timeStamp") or ""),
                "scope": WX_SCOPE,
                "signature": str(ticket.get("signature") or ""),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = self._json(response, "微信二维码")
        uuid = str(payload.get("uuid") or "")
        if int(payload.get("errcode", -1)) != 0 or not uuid:
            raise WeReadError("微信二维码生成失败")
        return uuid, f"https://open.weixin.qq.com/connect/confirm?uuid={quote(uuid, safe='')}"

    def save_qr(self, target: Path, confirm_url: str) -> None:
        import qrcode

        target.parent.mkdir(parents=True, exist_ok=True)
        image = qrcode.make(confirm_url)
        image.save(target)

    def wait_for_login(self, uuid: str, *, deadline_seconds: int = 300) -> WeReadCredentials:
        deadline = time.time() + deadline_seconds
        last: int | None = None
        while time.time() < deadline:
            params = {"f": "json", "uuid": uuid}
            if last is not None:
                params["last"] = str(last)
            response = self.session.get(
                "https://long.open.weixin.qq.com/connect/l/qrconnect",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=25,
            )
            payload = self._json(response, "微信扫码状态")
            last = int(payload.get("wx_errcode", 0))
            if last == 405:
                return self.exchange(str(payload.get("wx_code") or ""))
            if last in {402, 403}:
                raise WeReadError("二维码已过期或登录被取消")
            if last not in {404, 408}:
                raise WeReadError(f"未知扫码状态: {last}")
        raise WeReadError("二维码登录超时")

    def exchange(self, wx_code: str) -> WeReadCredentials:
        device_id = _new_device_id()
        timestamp = int(time.time() * 1000)
        random_value = secrets.randbelow(1000)
        body = {
            "appFirstInstall": 1,
            "code": wx_code,
            "deviceId": device_id,
            "deviceName": DEVICE_NAME,
            "installId": "eink31" + "".join(str(secrets.randbelow(10)) for _ in range(26)),
            "isAutoLogout": 0,
            "isFromQrcode": 1,
            "random": random_value,
            "signature": _signature(timestamp, device_id, random_value),
            "timestamp": timestamp,
            "trackId": "",
            "deviceType": DEVICE_TYPE,
        }
        headers = {**VERSION_HEADERS, "Content-Type": "application/json; charset=UTF-8"}
        response = self.session.post(
            f"{WEREAD_BASE}/login",
            headers=headers,
            data=json.dumps(body, separators=(",", ":")).encode(),
            timeout=self.timeout,
        )
        payload = self._json(response, "微信读书扫码登录")
        credentials = WeReadCredentials(
            vid=str(payload.get("vid") or ""),
            accessToken=str(payload.get("accessToken") or ""),
            refreshToken=str(payload.get("refreshToken") or ""),
            deviceId=device_id,
            skey=str(payload.get("skey") or ""),
            wxAccessToken=str(payload.get("wxAccessToken") or ""),
            name=str((payload.get("user") or {}).get("name") or ""),
            updatedAt=int(time.time()),
        )
        credentials.validate()
        return credentials

    def refresh(self, credentials: WeReadCredentials) -> WeReadCredentials:
        if not credentials.refreshToken or not credentials.deviceId:
            raise WeReadAuthExpired("凭证无法自动续期，需要重新扫码")
        timestamp = int(time.time() * 1000)
        random_value = secrets.randbelow(1000) + 1
        body = {
            "deviceId": credentials.deviceId,
            "deviceName": credentials.deviceName,
            "inBackground": 0,
            "kickType": 1,
            "random": random_value,
            "refCgi": "",
            "refreshToken": credentials.refreshToken,
            "signature": _signature(timestamp, credentials.deviceId, random_value),
            "timestamp": timestamp,
            "trackId": "",
            "deviceType": DEVICE_TYPE,
        }
        response = self.session.post(
            f"{WEREAD_BASE}/login",
            headers={**VERSION_HEADERS, "Content-Type": "application/json; charset=UTF-8"},
            data=json.dumps(body, separators=(",", ":")).encode(),
            timeout=self.timeout,
        )
        payload = self._json(response, "微信读书凭证续期")
        access_token = str(payload.get("accessToken") or "")
        if not response.ok or not access_token:
            raise WeReadAuthExpired(str(payload.get("errmsg") or "微信读书续期失败"))
        if str(payload.get("vid") or credentials.vid) != credentials.vid:
            raise WeReadAuthExpired("续期返回了不同账号，拒绝覆盖凭证")
        credentials.accessToken = access_token
        credentials.refreshToken = str(payload.get("refreshToken") or credentials.refreshToken)
        credentials.updatedAt = int(time.time())
        return credentials


def _decode_biz(value: str) -> str:
    value = unquote(value).strip()
    decoded = base64.b64decode(value + "=" * ((4 - len(value) % 4) % 4)).decode().strip()
    if not decoded.isdigit():
        raise WeReadError("公众号 biz 无效")
    return decoded


def book_id_from_biz(value: str) -> str:
    return f"MP_WXS_{_decode_biz(value)}"


def encode_web_id(value: str | int) -> str:
    """Encode a WeRead ID for the official Web reader route."""
    source = str(value)
    digest = hashlib.md5(source.encode()).hexdigest()
    if source.isdigit():
        code = "3"
        chunks = [format(int(source[index:index + 9]), "x") for index in range(0, len(source), 9)]
    else:
        code = "4"
        chunks = ["".join(format(ord(character), "x") for character in source)]
    result = digest[:3] + code + "2" + digest[-2:]
    result += "g".join(f"{len(chunk):02x}{chunk}" for chunk in chunks)
    if len(result) < 20:
        result += digest[:20 - len(result)]
    return result + hashlib.md5(result.encode()).hexdigest()[:3]


def _check_error(payload: dict[str, Any]) -> None:
    code = int(payload.get("errcode", payload.get("errCode", 0)) or 0)
    if not code:
        return
    message = str(payload.get("errmsg") or payload.get("errMsg") or code)
    if code == -2012:
        raise WeReadAuthExpired(message)
    if code in {-2041, -2010}:
        raise WeReadRateLimited(message)
    raise WeReadError(message)


class WeReadMobileClient:
    def __init__(self, credentials: WeReadCredentials, *, timeout: float = 20.0):
        credentials.validate()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {**VERSION_HEADERS, "vid": credentials.vid, "accessToken": credentials.accessToken}
        )

    def get_book_info(self, book_id: str) -> dict[str, Any]:
        """Return public metadata for a WeRead book/public-account identifier."""
        response = self.session.get(
            f"{WEREAD_BASE}/book/info",
            params={"bookId": str(book_id).strip()},
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise WeReadAuthExpired(f"HTTP {response.status_code}")
        if response.status_code in {429, 499}:
            raise WeReadRateLimited(f"HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise WeReadError("公众号信息返回格式异常")
        _check_error(payload)
        return payload

    def get_articles(self, account: WechatAccount, *, count: int = 30, synckey: int = 0) -> list[WechatArticle]:
        book_id = account.book_id or (book_id_from_biz(account.biz) if account.biz else "")
        if not book_id:
            raise WeReadError(f"{account.display_name} 缺少 book_id/biz")
        response = self.session.get(
            f"{WEREAD_BASE}/mp/chapters",
            params={"bookId": book_id, "count": min(max(count, 1), 50), "synckey": max(synckey, 0)},
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise WeReadAuthExpired(f"HTTP {response.status_code}")
        if response.status_code in {429, 499}:
            raise WeReadRateLimited(f"HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise WeReadError("文章列表返回格式异常")
        _check_error(payload)
        entries = payload.get("data") if isinstance(payload.get("data"), list) else []
        articles: list[WechatArticle] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            review = entry.get("review") if isinstance(entry.get("review"), dict) else entry
            info = review.get("mpInfo") if isinstance(review.get("mpInfo"), dict) else {}
            review_id = str(review.get("reviewId") or entry.get("reviewId") or "")
            title = str(review.get("title") or info.get("title") or "").strip()
            if not review_id or not title:
                continue
            url = next(
                (str(value) for value in (info.get("doc_url"), info.get("docUrl"), info.get("url"), review.get("url")) if isinstance(value, str) and value.startswith("http")),
                "",
            )
            original = str(info.get("originalId") or "").strip()
            if not url and original.startswith("/s"):
                url = "https://mp.weixin.qq.com" + original
            elif not url and original.startswith("http"):
                url = original
            elif not url and original:
                url = "https://mp.weixin.qq.com/s/" + quote(original, safe="._~-")
            published = int(info.get("time") or review.get("createTime") or 0)
            day = datetime_from_timestamp(published)
            article = WechatArticle(
                publisher_id=account.id,
                publisher_name=account.display_name,
                title=title,
                url=url,
                published_at=day,
                summary=str(info.get("content") or review.get("content") or ""),
                provider="weread",
                raw={"review_id": review_id, "book_id": book_id},
            )
            article.article_key = wechat_article_key(
                url,
                publisher_id=account.id,
                title=title,
                published_at=day,
                upstream_id=review_id,
            )
            articles.append(article)
        return articles


def datetime_from_timestamp(value: int) -> str:
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, ZoneInfo("Asia/Shanghai")).date().isoformat()
