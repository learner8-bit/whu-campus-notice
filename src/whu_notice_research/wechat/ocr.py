from __future__ import annotations

import io
import time
from urllib.parse import urlparse

import requests

from ..storage import NoticeStore


MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_HOST_SUFFIXES = ("qpic.cn", "weixin.qq.com")


def _download_image(url: str, *, timeout: float = 20) -> bytes:
    host = (urlparse(url).hostname or "").lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES):
        raise ValueError("OCR 仅处理微信官方图片域名")
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("图片超过 8 MB OCR 上限")
        chunks.append(chunk)
    return b"".join(chunks)


def _google_ocr(data: bytes, *, timeout: float = 20) -> str:
    from google.cloud import vision

    response = vision.ImageAnnotatorClient().text_detection(
        image=vision.Image(content=data),
        image_context={"language_hints": ["zh-CN", "en"]},
        timeout=timeout, retry=None,
    )
    if response.error.message:
        raise RuntimeError(response.error.message)
    return str(response.full_text_annotation.text or "").strip()


def _local_ocr(data: bytes, *, timeout: float = 20) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return str(pytesseract.image_to_string(image, lang="chi_sim+eng", timeout=timeout) or "").strip()


def ocr_article_images(
    image_urls: list[str],
    store: NoticeStore,
    *,
    cloud_limit: int = 900,
    max_images: int = 5,
    budget_seconds: float = 45,
) -> tuple[str, str]:
    """OCR likely poster images with a hard monthly cloud-image limit.

    Returns text and the effective engine. Failed individual images are
    ignored so one deleted cover never blocks the article.
    """
    texts: list[str] = []
    engines: list[str] = []
    deadline = time.monotonic() + budget_seconds
    def remaining() -> float:
        return max(0, min(20, deadline - time.monotonic()))
    for url in image_urls[:max_images]:
        if remaining() <= 0:
            break
        try:
            data = _download_image(url, timeout=remaining())
        except Exception:
            continue
        text = ""
        if remaining() > 0 and store.reserve_cloud_ocr(limit=cloud_limit):
            try:
                text = _google_ocr(data, timeout=remaining())
                engines.append("google_vision")
            except Exception:
                text = ""
        if not text and remaining() > 0:
            try:
                text = _local_ocr(data, timeout=remaining())
                engines.append("tesseract")
            except Exception:
                text = ""
        if text:
            texts.append(text[:12000])
    return "\n\n".join(texts)[:30000], "+".join(dict.fromkeys(engines))
