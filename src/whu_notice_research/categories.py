"""Canonical user-facing categories and display preferences."""

from __future__ import annotations

import re


CATEGORY_ORDER = (
    "评奖评优",
    "竞赛",
    "科研",
    "本科事务",
    "讲座",
    "志愿活动",
    "社会实践",
    "国际交流",
)

CATEGORY_ICONS = {
    "评奖评优": "🏅",
    "竞赛": "🏆",
    "科研": "🔬",
    "本科事务": "📚",
    "讲座": "🎤",
    "志愿活动": "🤝",
    "社会实践": "🌱",
    "国际交流": "🌍",
}

CATEGORY_ALIASES = {
    "奖学金": "评奖评优",
    "评奖评优": "评奖评优",
    "评奖": "评奖评优",
    "评优": "评奖评优",
    "竞赛": "竞赛",
    "比赛": "竞赛",
    "大赛": "竞赛",
    "科研": "科研",
    "科研机会": "科研",
    "创新创业": "科研",
    "大创": "科研",
    "学术讲座": "讲座",
    "讲座": "讲座",
    "志愿服务": "志愿活动",
    "志愿活动": "志愿活动",
    "社会实践": "社会实践",
    "本科生事务": "本科事务",
    "本科事务": "本科事务",
    "国际交流": "国际交流",
}

EXCLUDED_TITLE_TERMS = (
    "心理健康",
    "心理咨询",
    "大学生心理健康教育中心",
    "大心",
    "第二课堂",
    "助学金",
    "困难认定",
    "勤工助学",
    "临时困难补助",
)

_TITLE_RULES = (
    ("评奖评优", ("奖学金", "评奖", "评优", "先进个人", "先进班集体")),
    ("竞赛", ("竞赛", "比赛", "大赛", "赛题")),
    ("科研", ("科研", "大创", "创新创业", "实验室", "课题组", "学生招募")),
    ("讲座", ("讲座", "论坛", "学术报告")),
    ("志愿活动", ("志愿", "志愿者")),
    ("社会实践", ("社会实践", "实践队", "实践项目")),
    ("本科事务", ("选课", "考试", "转专业", "培养方案", "学籍", "本科生事务")),
    ("国际交流", ("国际交流", "交流学习", "交换", "访学", "海外学习")),
)


def canonical_category(value: object, title: str = "") -> str:
    """Return one of the digest categories, or an empty string."""
    raw = re.sub(r"\s+", "", str(value or ""))
    if raw in CATEGORY_ORDER:
        return raw
    if raw in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[raw]
    for alias, category in CATEGORY_ALIASES.items():
        if alias and alias in raw:
            return category
    for category, words in _TITLE_RULES:
        if any(word in title for word in words):
            return category
    return ""


def category_rank(value: str) -> int:
    try:
        return CATEGORY_ORDER.index(value)
    except ValueError:
        return len(CATEGORY_ORDER)


def is_excluded_topic(title: str) -> bool:
    return any(term in title for term in EXCLUDED_TITLE_TERMS)
