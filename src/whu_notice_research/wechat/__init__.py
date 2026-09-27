"""Automatic discovery and parsing for verified WeChat public accounts."""

from .collector import collect_wechat
from .models import CollectionResult, ProviderResult, WechatAccount, WechatArticle

__all__ = [
    "CollectionResult",
    "ProviderResult",
    "WechatAccount",
    "WechatArticle",
    "collect_wechat",
]
