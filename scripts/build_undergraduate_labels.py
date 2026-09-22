from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.rules_v1 import RULESET_VERSION, decide  # noqa: E402


KEEP_TITLES = {
    # 综合业务：对学生有直接影响或明确开放给学生
    "关于2026年中秋节、国庆节期间教学安排调整的通知",
    "关于2026年7月5日信息学部01教学楼封楼的通知",
    "关于2026年端午节期间教学安排调整的通知",
    "关于2026年6月13日教学楼封楼的通知",
    "关于2026年5月30日上午文理学部教四楼封楼的通知",
    "关于2026年劳动节期间教学安排调整的通知",
    "关于做好2025-2026学年第二学期课堂教学质量评价（学生）工作的通知",
    "关于转发《关于开展2026年度大学生创业扶持项目申报工作的通知》的通知",
    # 学生通知
    "关于举办第十届中国大学生工程实践与创新能力大赛武汉大学选拔赛的通知",
    "关于开展2026-2027学年第一学期本科生学业预警的通知",
    "关于2026年下半年全国大学英语四、六级考试报名的通知",
    "关于组织参加2026年全国大学生嵌入式芯片与系统设计竞赛FPGA创新设计赛道的通知",
    "2026“外研社·国才杯”“理解当代中国”全国大学生外语能力大赛",
    "关于组织报名参加第十八届全国大学生数学竞赛的通知",
    "武汉大学2026年秋季学期《国际化拔尖创新人才科研训练》课程选课通知",
    "关于2026-2027学年第一学期2026级新生选课的通知",
    "关于做好2027届推荐优秀应届本科毕业生免试攻读研究生工作的通知",
    "关于举办2026年全国高校商业精英挑战赛国际贸易竞赛AI外贸产品说明书创作赛道校内选拔赛的通知",
    "关于2026级本科新生英语水平测试的通知",
    "关于做好武汉大学2026年大学先修课成绩认证工作的通知",
    "关于2026-2027学年第一学期重修、缓考选课的通知",
    "关于2025-2026学年暑假期间毕业证明书、学位证明书补办等事项受理的通知",
    "关于普通本科2026级新生学籍照片采集工作的通知",
    "关于2026-2027学年第一学期本科生（老生）报到注册的通知",
    "关于申报武汉大学2027年大学生创新训练计划项目的通知",
    "关于开展2026-2027学年第一学期本研贯通培养课程互选工作的通知",
    "关于开展2026-2027学年第一学期本科生大学英语免修申请工作的通知",
    "关于暑假期间公共教室开放安排的通知",
    "武汉大学关于组织参加2026年高教社杯全国大学生数学建模竞赛的通知",
    "关于2026-2027学年第一学期选课的通知",
    "关于武汉大学2026年秋季学期微专业报名的通知",
    "关于 2025-2026 学年第三学期选课的通知",
    "关于组织参加第八届中华经典诵写讲大赛武汉大学校赛的通知",
    "关于组织参加第九届“外教社杯”全国高校学生跨文化能力大赛校内选拔赛的通知",
    "关于2026年奖励前1%优秀毕业生返还全部课程学分学费的通知",
    "关于做好2026届普通本科毕业生毕业证书、学位证书发放工作的通知",
    "关于组织参加2026年全国高校商业精英挑战赛创新创业竞赛的通知",
    "关于2026年上半年全国大学英语四、六级考试听力放音频率及试音的通知",
    "关于组织参加第六届全国大学生高电压与等离子体科技创新竞赛的通知",
    "关于开展2026年武汉大学大学生GIS应用技能大赛的通知",
    "关于开展2025年立项武汉大学珞珈本科生创新研究基金项目结题验收工作的通知",
    "关于举办武汉大学2026年全国高校商业精英挑战赛国际贸易竞赛（跨境电商赛道）校内选拔赛的通知",
    "武汉大学2026年上半年国家普通话水平测试报名通知",
    "关于报名参加2026年全国大学生测绘学科创新创业智能大赛的通知",
    "关于举办第三届全球数智教育创新大赛空天信息赛道暨第二届大学生遥感学科数智创新大赛遥感数智教育创新竞赛通知",
    "关于公布武汉大学2026年大学生创新训练计划项目立项名单及做好相关管理工作的通知",
    "2026年武汉大学信息安全竞赛暨第十九届全国大学生信息安全竞赛—作品赛的通知",
    "2026年武汉大学创新创业团队入驻选拔通知",
    "关于做好2027届本科毕业生图像采集工作的通知",
    "关于开展2026年大学生创新训练计划重点支持领域项目遴选答辩工作的通知",
    "关于2025-2026学年第二学期中期退课的通知",
    "关于举办2026年“学创杯”全国大学生创业综合模拟大赛武汉大学选拔赛的通知",
    "关于2026届毕业生相关工作安排的通知",
    "2026年全国大学生英语竞赛（NECCS2026）武汉大学赛区初赛通知",
}


REVIEW_TITLES = {
    "2026年第18届全国大学生广告艺术大赛湖北赛区获奖名单公示",
    "关于“武汉大学优秀学生干部”拟推荐人选的公示",
    "关于公布2026年秋季学期微专业录取名单的通知",
    "武汉大学2027届推荐优秀应届本科毕业生免试攻读研究生拟推荐名单公示",
    "关于公布2025年武汉大学珞珈本科生创新研究基金项目结题验收结果的通知",
    "2026年武汉大学工创中心创新创业团队入驻结果公示",
    "关于公布2026年创新学分冲抵课程学分结果的通知",
    "关于2026年创新学分冲抵课程学分结果的公示",
    "关于修订2027届推免工作实施细则的通知",
}


FILTER_TITLES = {
    "关于以民主推荐方式选拔教师教学发展中心办公室主任的公告",
    "关于招聘2026-2027学年第一学期本科课程研究生助教的通知",
    "本科生院2026年暑期放假调休值班的通知",
    "武汉大学关于开展2026年本科课程思政项目申报建设工作的通知",
    "武汉大学关于征集2027年大学生创新训练计划项目选题的通知",
    "关于做好2025-2026学年“大思政实践课”教学安排的通知",
    "武汉大学关于申报第五批“武大通识3.0”课程的通知",
    "关于公布武汉大学2026年数智教育典型案例获奖名单的通知",
    "关于公布2026年省级和校级教学研究项目验收结果的通知",
    "本科生院2026年“五一”放假值班的通知",
    "关于做好2026-2027学年第一学期课表编排工作的通知",
    "关于公布2026年武汉大学本科教育质量建设综合改革项目验收结果的通知",
    "关于做好2025-2026学年第三学期全球课程教学安排的通知",
    "关于做好2025-2026学年第三学期课表编排工作的通知",
    "本科生院2026年“清明节”放假值班的通知",
    "关于开展武汉大学2026年度教育部产学合作协同育人项目结题验收工作的通知",
    "关于公布2026年武汉大学本科教育质量建设综合改革项目（经费自筹类）名单的通知",
    "关于按时提交2025-2026学年第二学期及第三学期考试成绩的通知",
    "关于报送第十二届学士学位评定委员会第十一次会议材料的通知",
}


def manual_reason(title: str, label: str) -> str:
    if label == "keep":
        if any(word in title for word in ("竞赛", "大赛", "报名", "申报", "选拔")):
            return "本科生可报名、申报或参加的机会"
        return "本科生需要执行或直接受影响的重要教学事务"
    if label == "review":
        return "结果、公示或制度信息；无新报名动作，但可能涉及确认、异议或后续安排"
    return "教师/学院管理事务、内部值班或已完成成果，不是本科生当前可行动事项"


def main() -> int:
    input_path = ROOT / "data" / "undergraduate_school" / "notices.jsonl"
    output_path = ROOT / "data" / "undergraduate_school" / "labeled_samples.csv"
    with input_path.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    all_titles = KEEP_TITLES | REVIEW_TITLES | FILTER_TITLES
    actual_titles = {row["title"] for row in rows}
    missing = actual_titles - all_titles
    stale = all_titles - actual_titles
    if missing or stale:
        raise RuntimeError(f"label coverage mismatch; missing={sorted(missing)!r}; stale={sorted(stale)!r}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
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
                "annotation_basis",
                "detail_status",
                "ruleset_version",
                "rule_label",
                "rule_score",
                "rule_reasons",
                "attachment_count",
                "link_count",
                "url",
            ),
        )
        writer.writeheader()
        for row in rows:
            if row["title"] in KEEP_TITLES:
                manual_label = "keep"
            elif row["title"] in REVIEW_TITLES:
                manual_label = "review"
            else:
                manual_label = "filter"
            prediction = decide(
                row["title"], row.get("summary", ""), row.get("body_text", ""), row["source_id"]
            )
            counts[manual_label] += 1
            writer.writerow(
                {
                    "published_at": row["published_at"],
                    "source_id": row["source_id"],
                    "source_name": row["source_name"],
                    "title": row["title"],
                    "manual_label": manual_label,
                    "manual_reason": manual_reason(row["title"], manual_label),
                    "annotation_basis": (
                        "title_only_verification_blocked"
                        if row.get("fetch_error")
                        else "title_body_attachments_links"
                    ),
                    "detail_status": "blocked" if row.get("fetch_error") else "parsed",
                    "ruleset_version": RULESET_VERSION,
                    "rule_label": prediction.label,
                    "rule_score": prediction.score,
                    "rule_reasons": "；".join(prediction.reasons),
                    "attachment_count": len(row.get("attachments", [])),
                    "link_count": len(row.get("links", [])),
                    "url": row["url"],
                }
            )
    print(f"reviewed {len(rows)} samples; wrote {output_path}")
    print("labels:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
