"""Feishu custom-bot webhook and SMTP delivery (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def load_env_file(path: Path) -> None:
    """Read a simple local .env without overriding CI/environment secrets."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class DeliveryConfig:
    feishu_webhook_url: str
    feishu_secret: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_to: tuple[str, ...]

    @classmethod
    def from_environment(cls, channels: str = "feishu") -> "DeliveryConfig":
        raw_port = os.getenv("SMTP_PORT") or "465"
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("SMTP_PORT must be a number") from exc
        recipients = tuple(
            value.strip() for value in os.getenv("MAIL_TO", "").replace(";", ",").split(",")
            if value.strip()
        )
        config = cls(
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
            feishu_secret=os.getenv("FEISHU_SECRET", "").strip(),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=port,
            smtp_security=(os.getenv("SMTP_SECURITY") or "ssl").strip().lower(),
            smtp_user=os.getenv("SMTP_USER", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            mail_from=os.getenv("MAIL_FROM", "").strip() or os.getenv("SMTP_USER", "").strip(),
            mail_to=recipients,
        )
        config.validate(channels)
        return config

    def validate(self, channels: str = "feishu") -> None:
        if channels not in {"feishu", "email", "both"}:
            raise ValueError("channels must be feishu, email or both")
        missing = [
            name for name, value in (
                ("FEISHU_WEBHOOK_URL", self.feishu_webhook_url if channels in {"feishu", "both"} else True),
                ("SMTP_HOST", self.smtp_host if channels in {"email", "both"} else True),
                ("SMTP_USER", self.smtp_user if channels in {"email", "both"} else True),
                ("SMTP_PASSWORD", self.smtp_password if channels in {"email", "both"} else True),
                ("MAIL_FROM", self.mail_from if channels in {"email", "both"} else True),
                ("MAIL_TO", self.mail_to if channels in {"email", "both"} else True),
            ) if not value
        ]
        if missing:
            raise ValueError("Missing notification settings: " + ", ".join(missing))
        if channels in {"feishu", "both"}:
            parsed = urlparse(self.feishu_webhook_url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "open.feishu.cn", "open.larksuite.com"
            }:
                raise ValueError("FEISHU_WEBHOOK_URL must be an official HTTPS Feishu/Lark bot URL")
        if channels in {"email", "both"}:
            if self.smtp_security not in {"ssl", "starttls"}:
                raise ValueError("SMTP_SECURITY must be ssl or starttls")
            if not 1 <= self.smtp_port <= 65535:
                raise ValueError("SMTP_PORT out of range")


def feishu_sign(secret: str, timestamp: int) -> str:
    key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def send_feishu(config: DeliveryConfig, message: str) -> None:
    payload: dict[str, object] = {"msg_type": "text", "content": {"text": message}}
    if config.feishu_secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = feishu_sign(config.feishu_secret, timestamp)
    request = Request(
        config.feishu_webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Feishu webhook HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"Feishu webhook network error: {type(exc.reason).__name__}") from None
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Feishu webhook returned invalid JSON") from None
    code = result.get("code", result.get("StatusCode", -1))
    if code != 0:
        raise RuntimeError(f"Feishu webhook rejected message (code {code})")


def send_email(config: DeliveryConfig, subject: str, plain: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    try:
        if config.smtp_security == "ssl":
            with smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=30, context=context
            ) as smtp:
                smtp.login(config.smtp_user, config.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(config.smtp_user, config.smtp_password)
                smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise RuntimeError(f"SMTP delivery failed: {type(exc).__name__}") from None
