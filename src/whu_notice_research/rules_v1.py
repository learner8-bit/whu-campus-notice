from __future__ import annotations

from dataclasses import dataclass, field


RULESET_VERSION = "rules_v1"


@dataclass
class RuleDecision:
    label: str
    score: int
    reasons: list[str] = field(default_factory=list)
    matched_positive: list[str] = field(default_factory=list)
    matched_negative: list[str] = field(default_factory=list)


STRONG_ACTION = (
    "报名",
    "申请",
    "申报",
    "招募",
    "招聘",
    "征集",
    "选拔",
    "选聘",
    "评选",
    "推荐",
    "参加",
    "资助",
    "奖学金",
)

OPPORTUNITY = (
    "竞赛",
    "大创",
    "创新创业",
    "交换",
    "交流学习",
    "留学",
    "科研项目",
    "志愿",
    "社会实践",
    "讲座",
    "报告会",
    "训练营",
    "夏令营",
)

IMPORTANT_AFFAIRS = (
    "素质综合测评",
    "综测",
    "推免",
    "转专业",
    "选课",
    "考试安排",
    "补考",
    "缓考",
    "毕业",
    "放假安排",
    "放暑假",
    "报到注册",
    "学术导师",
    "导师双选",
    "发展团员",
)

PAST_EVENT = (
    "喜报",
    "圆满举行",
    "圆满举办",
    "成功举办",
    "成功召开",
    "圆满落幕",
    "活动回顾",
    "获奖",
    "获批",
    "来访",
    "调研",
    "再创佳绩",
    "荣获佳绩",
    "斩获",
    "勇夺",
    "再获",
)

RESULT_ONLY = ("名单公示", "拟聘用名单", "获奖名单", "结果公示", "结业学员")
STAFF_ONLY = ("全院教职工", "各位老师", "教师申报", "教职工大会", "内设机构")


def decide(title: str, summary: str = "", body: str = "", source_id: str = "") -> RuleDecision:
    # Title and list summary carry the strongest intent signal. The beginning of
    # the body is used for audience checks, not for accumulating action points:
    # historical news often mentions that somebody previously "报名/申请".
    intent_text = " ".join((title, summary))
    audience_text = " ".join((title, summary, body[:900]))
    score = 0
    reasons: list[str] = []
    positive: list[str] = []
    negative: list[str] = []

    for keyword in STRONG_ACTION:
        if keyword in intent_text:
            score += 3
            positive.append(keyword)
    for keyword in OPPORTUNITY:
        if keyword in intent_text:
            score += 2
            positive.append(keyword)
    for keyword in IMPORTANT_AFFAIRS:
        if keyword in intent_text:
            score += 3
            positive.append(keyword)
    for keyword in PAST_EVENT:
        if keyword in title:
            score -= 5
            negative.append(keyword)
    for keyword in STAFF_ONLY:
        if keyword in audience_text:
            score -= 5
            negative.append(keyword)
    for keyword in RESULT_ONLY:
        if keyword in title:
            score -= 2
            negative.append(keyword)

    if source_id == "student_activity":
        return RuleDecision(
            "filter",
            -10,
            ["学生活动栏目样本均为活动完成后的新闻回顾"],
            [],
            ["完成态栏目"],
        )
    if source_id == "academic_lecture":
        score += 3
        reasons.append("学术讲座栏目对本科生默认有关注价值")
    if source_id == "study_abroad_notice":
        score += 2
        reasons.append("留学通知栏目具有较强机会先验")
    if source_id == "student_activity" and any(word in title for word in PAST_EVENT):
        score -= 2
        reasons.append("学生活动栏目中的完成态报道通常不是待办机会")

    if positive:
        reasons.append("命中行动/机会词：" + "、".join(dict.fromkeys(positive)))
    if negative:
        reasons.append("命中降权词：" + "、".join(dict.fromkeys(negative)))

    has_action = any(keyword in intent_text for keyword in STRONG_ACTION + IMPORTANT_AFFAIRS)
    past_event = any(keyword in title for keyword in PAST_EVENT)
    staff_only = any(keyword in audience_text for keyword in STAFF_ONLY) or any(
        phrase in audience_text
        for phrase in ("选聘范围 我院各系及实验中心教师", "面向全院教师", "邀请全院教师")
    )
    result_only = any(keyword in title for keyword in RESULT_ONLY)

    graduate_only = (
        (
            "研究生" in title
            or (
                any(word in audience_text for word in ("全日制在籍研究生", "面向研究生", "招募对象为研究生"))
                and "本科生" not in audience_text
            )
        )
        and "本科" not in title
        and "推荐免试" not in title
        and "推免" not in title
        and source_id != "academic_lecture"
    )
    excluded_scope = "招聘会" in title or "实习生" in title
    internal_rule = any(word in title for word in ("工作条例", "聘任")) and "助理" not in title
    completed_list = "名单" in title and any(word in title for word in ("公布", "结业"))
    public_result = any(word in title for word in ("名单公示", "拟推荐名单", "拟聘用名单", "推优入党"))
    faculty_fund = "国家自然科学基金" in title and "本科生" not in title
    achievement_news = title.lstrip().startswith("喜报") or any(
        word in title for word in ("再创佳绩", "荣获佳绩", "斩获", "勇夺", "再获多项")
    )

    if staff_only:
        label = "filter"
    elif graduate_only or excluded_scope or internal_rule or completed_list or faculty_fund or achievement_news:
        label = "filter"
    elif public_result:
        label = "review"
    elif past_event and not has_action:
        label = "filter"
    elif score >= 3 and not result_only:
        label = "keep"
    elif source_id == "student_notice" and "代表大会" in title:
        label = "keep"
    else:
        label = "review"
    return RuleDecision(label, score, reasons, list(dict.fromkeys(positive)), list(dict.fromkeys(negative)))
