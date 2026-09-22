from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules import decide  # noqa: E402


HOMOGENEOUS_SOURCE_LABELS = {
    "academic_lecture": ("keep", "讲座预告包含未来时间、地点和报告主题，属于可参加事项"),
    "student_activity": ("filter", "活动已经举行，正文为新闻回顾而非新的报名或申请机会"),
}


# Exact-title decisions were made after reviewing the title and fetched body.
# Keeping them explicit makes disagreements and future corrections auditable.
MANUAL_LABELS: dict[tuple[str, str], tuple[str, str]] = {
    # 综合通知
    ("general_notice", "关于以民主推荐方式选拔电子信息学院内设机构副主任的公告"):
        ("filter", "面向学院教职工的内部干部选拔，不面向本科生"),
    ("general_notice", "关于召开全院教职工大会的通知"):
        ("filter", "全院教职工会议，与本科生无直接行动关系"),
    ("general_notice", "武汉大学 “卓越芯火计划”（第十七期）报名通知"):
        ("keep", "正文明确面向即将升入大四的学生招生，存在报名动作和截止安排"),
    ("general_notice", "关于公布电子信息学院首批“样板党支部”  培育创建单位名单的通知"):
        ("filter", "仅公布既有评选结果，无新的申请动作"),
    ("general_notice", "关于实施《电子信息学院研究生党支部指导老师工作条例》的通知"):
        ("filter", "研究生党建内部制度，不属于本科生机会或事务"),
    ("general_notice", "关于召开学院2025年度育人工作总结表彰活动的通知"):
        ("filter", "正文邀请对象为全院教职工，且活动为总结表彰"),
    ("general_notice", "关于聘任万显荣等同志为研究生党支部党委联系人、党建导师的决定"):
        ("filter", "内部任命决定，无学生可执行事项"),
    ("general_notice", "关于开展武汉大学电子信息学院首批院级“样板党支部”培育创建工作的通知"):
        ("review", "含学生党支部申报机会，但行动主体是党支部而非普通学生"),

    # 学工通知
    ("student_notice", "电子信息学院关于评选2025-2026学年度奖学金和先进班集体先进个人的通知"):
        ("keep", "本科生奖学金与评奖评优申请"),
    ("student_notice", "电子信息学院关于2026年学生中秋节、国庆节放假安排的通知"):
        ("keep", "重要本科生教学与假期安排"),
    ("student_notice", "武汉大学电子信息学院学生工作办公室本科生学生助理招募通知"):
        ("keep", "面向本科生的岗位招募"),
    ("student_notice", "关于开展电子信息学院2025-2026学年本科生素质综合测评工作的通知"):
        ("keep", "本科生综测，含材料提交和截止时间"),
    ("student_notice", "共青团武汉大学电子信息学院委员会组织部增补工作人员拟聘用名单公示"):
        ("review", "结果公示没有新报名，但异议期可能与当事人相关"),
    ("student_notice", "电子信息学院关于2026-2027学年第一学期学生报到注册的通知"):
        ("keep", "全体学生必须完成的报到注册事务"),
    ("student_notice", "【志愿服务】2026年全国大学生电子设计竞赛模拟电子系统设计专题赛志愿者招募通知"):
        ("keep", "面向学生的志愿者招募"),
    ("student_notice", "关于选聘电子信息学院2026级本科新生助理班主任的通知"):
        ("keep", "学生可申请的助理班主任岗位"),
    ("student_notice", "电子信息学院关于2026年学生放暑假及相关事项的通知"):
        ("keep", "重要本科生假期与离校事务"),
    ("student_notice", "关于选聘电子信息学院2026级本科新生班主任的通知"):
        ("filter", "正文选聘范围仅为学院教师"),
    ("student_notice", "关于组织参加“校园大使走进吴江开发区”产教融合研学活动的通知"):
        ("keep", "学生可报名参加的研学活动"),
    ("student_notice", "电子信息学院关于2026年学生端午节放假安排的通知"):
        ("keep", "重要本科生教学与假期安排"),
    ("student_notice", "关于召开共青团武汉大学电子信息学院第二十次代表大会的通知"):
        ("keep", "各团支部需选举学生代表并提交材料，存在明确行动"),
    ("student_notice", "武汉大学电子信息学院第二十六届学生会部门负责人招聘通知"):
        ("keep", "面向学生的学生组织岗位招聘"),
    ("student_notice", "关于评选电子信息学院2026届“电信骄子”的通知"):
        ("keep", "学生荣誉评选，存在申请/推荐动作"),
    ("student_notice", "关于评选电子信息学院2026届毕业生“我心中的好导师”的通知"):
        ("keep", "学生可参与推荐和评选"),
    ("student_notice", "关于开展2024级、2025级本科生“学术导师面对面”活动的通知"):
        ("keep", "面向本科生的学术导师活动"),
    ("student_notice", "关于做好2026年上半年发展团员工作的通知"):
        ("keep", "本科生可按条件申请入团，含流程与材料"),
    ("student_notice", "电子信息学院关于2026年学生劳动节放假安排的通知"):
        ("keep", "重要本科生教学与假期安排"),
    ("student_notice", "关于召开武汉大学电子信息学院第二十六次学生代表大会的预通知"):
        ("keep", "本科班级需选举代表并参与提案，存在明确行动"),
    ("student_notice", "关于推荐表彰2025年度电信芯榜样的通知"):
        ("keep", "学生荣誉推荐评选"),
    ("student_notice", "关于组织研究生参加第九届中国研究生创“芯”大赛武汉大学选拔赛的通知"):
        ("filter", "明确仅面向研究生，不属于本科生范围"),
    ("student_notice", "关于组织研究生参加武汉大学第二十届研究生电子设计大赛暨第二十一届中国研究生电子设计竞赛校园选拔赛的通知"):
        ("filter", "明确仅面向研究生，不属于本科生范围"),
    ("student_notice", "电子信息学院2026届优秀本科毕业生拟推荐名单公示"):
        ("review", "结果公示没有新申请，但异议期对相关学生重要"),
    ("student_notice", "关于武汉大学电子信息学院第二十六届学生会主席团候选人报名推荐的通知"):
        ("keep", "本科生可报名或被推荐的学生组织岗位"),
    ("student_notice", "电子信息学院关于2026年学生清明节放假安排的通知"):
        ("keep", "重要本科生教学与假期安排"),
    ("student_notice", "关于开展2024级、2025级本科生学术导师双选工作的通知"):
        ("keep", "本科生需参与的导师双选事务"),
    ("student_notice", "武汉大学电子信息类2026届毕业生暨2027届实习生春季专场招聘会邀请函"):
        ("filter", "就业与实习已被当前 V1 明确排除"),

    # 留学、科研、团委
    ("study_abroad_notice", "关于申报2026年下半年本科生全球胜任力培养资助计划的通知"):
        ("keep", "本科生国际交流资助申请"),
    ("study_abroad_notice", "关于开展2026年研究生出国（境）交流学习资助工作的通知"):
        ("filter", "明确仅面向研究生"),
    ("research_notice", "【国家自然科学基金】关于转发非集中期国家自然科学基金项目申报指南的通知（滚动更新）"):
        ("filter", "国家自然科学基金教师科研项目申报，非本科生机会"),
    ("youth_league", "共青团武汉大学电子信息学院委员会组织部增补工作人员拟聘用名单公示"):
        ("review", "结果公示没有新报名，但异议期可能与当事人相关"),
    ("youth_league", "关于增补共青团武汉大学电子信息学院委员会组织部工作人员的通知"):
        ("filter", "正文明确招募对象为全日制在籍研究生"),
    ("youth_league", "关于公布武汉大学学生会第十七期“未来学院”电子信息学院分校结业学员及优秀学员名单的通知"):
        ("filter", "培训已经结束，仅公布结业及优秀名单"),
    ("youth_league", "关于做好2026年上半年发展团员工作的通知"):
        ("keep", "本科生可按条件申请入团，含流程与材料"),
    ("youth_league", "电子信息学院团委2025年度团内评优拟推荐名单的公示"):
        ("review", "结果公示没有新申请，但异议期对相关学生重要"),
    ("youth_league", "关于武汉大学电子信息学院2026年上半年团支部“推优入党”名单的公示(入党积极分子和发展对象候选人)"):
        ("review", "名单公示包含本科生且有异议期，但无新的申请动作"),

    # 本科生教育
    ("undergraduate_education", "喜报！我院本科生首次获批国家自然科学基金青年学生基础研究项目（本科生）"):
        ("filter", "喜报和成果宣传，不是新的项目申报通知"),
    ("undergraduate_education", "关于组织参加2026年全国大学生嵌入式芯片与系统设计竞赛FPGA创新设计赛道的通知"):
        ("keep", "面向本科生的竞赛报名"),
    ("undergraduate_education", "珞珈筑擂，群英逐梦——2026 全国大学生电子设计竞赛 TI 杯模拟电子系统设计专题赛在武大圆满落幕"):
        ("filter", "比赛已经结束，正文为活动报道"),
    ("undergraduate_education", "关于启动电子信息学院2027届推荐免试研究生工作的通知"):
        ("keep", "本科生推免申请与材料提交"),
    ("undergraduate_education", "从“芯”出发，硕果盈枝：我院学子在集创赛中斩获13项国家级奖项"):
        ("filter", "既有竞赛获奖宣传，无新报名动作"),
    ("undergraduate_education", "赋能前沿产业，2026年“瑞萨杯”信息科技前沿专题赛电信学子再创佳绩！"):
        ("filter", "既有竞赛获奖宣传，无新报名动作"),
    ("undergraduate_education", "克难笃行，“AI”之路再奋进 —— 我院学子征战2026英特尔杯AI专题赛再获多项国奖"):
        ("filter", "既有竞赛获奖宣传，无新报名动作"),
    ("undergraduate_education", "关于组织参加第十四届全国大学生光电设计竞赛的通知"):
        ("keep", "面向本科生的竞赛报名"),
}


def main() -> int:
    input_path = ROOT / "data" / "eis" / "notices.jsonl"
    output_path = ROOT / "data" / "eis" / "labeled_samples.csv"
    with input_path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    agreement = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "published_at",
                "source_id",
                "source_name",
                "title",
                "manual_label",
                "manual_reason",
                "rule_label",
                "rule_score",
                "rule_agrees",
                "summary_excerpt",
                "attachment_count",
                "link_count",
                "url",
            ),
        )
        writer.writeheader()
        for row in rows:
            decision = HOMOGENEOUS_SOURCE_LABELS.get(row["source_id"])
            if decision is None:
                decision = MANUAL_LABELS.get((row["source_id"], row["title"]))
            if decision is None:
                raise KeyError(f"unreviewed sample: {row['source_id']} / {row['title']}")
            manual_label, manual_reason = decision
            rule = decide(row["title"], row.get("summary", ""), row.get("body_text", ""), row["source_id"])
            agrees = rule.label == manual_label
            counts[manual_label] += 1
            agreement += int(agrees)
            writer.writerow(
                {
                    "published_at": row["published_at"],
                    "source_id": row["source_id"],
                    "source_name": row["source_name"],
                    "title": row["title"],
                    "manual_label": manual_label,
                    "manual_reason": manual_reason,
                    "rule_label": rule.label,
                    "rule_score": rule.score,
                    "rule_agrees": agrees,
                    "summary_excerpt": row.get("summary", "")[:240],
                    "attachment_count": len(row.get("attachments", [])),
                    "link_count": len(row.get("links", [])),
                    "url": row["url"],
                }
            )

    print(f"reviewed {len(rows)} samples; wrote {output_path}")
    print("labels:", dict(sorted(counts.items())))
    print(f"draft-rule agreement: {agreement}/{len(rows)} ({agreement / len(rows):.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
