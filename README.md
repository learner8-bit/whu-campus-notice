# 武汉大学校园通知日报

现有链路：10 个武汉大学站点组 → 增量采集与历史去重 → PDF/Word/Excel 附件及校外链接解析 → DeepSeek 结构化筛选与跨站事件去重 → 综测/推免政策比对 → 当天日报 → 飞书群机器人。已覆盖电子信息学院、本科生院、武大官网、学生资助中心、校团委/未来网、第二课堂、国际交流部、综合事务服务中心、信息公开网和科学技术发展研究院。AI 未配置或调用失败时自动退回冻结的规则筛选；邮件模块保留但当前关闭。

## 现在预览

Python 3.11+。先安装轻量 PDF 解析依赖（GitHub Actions 已自动执行）：

```powershell
python -m pip install -r requirements.txt
```

然后在项目目录执行：

```powershell
python scripts/daily_digest.py --preview-latest 5
```

会生成 `data/runs/digest_preview.txt` 和 `.html`，不访问官网、不发送消息。运行当日完整采集和日报预览：

```powershell
python scripts/daily_digest.py
```

日报文件保存在 `data/runs/digest_YYYY-MM-DD.txt` / `.html`。收录今天首次发现的有效候选，以及今天发布但已在历史库中的通知；明确无关内容不发送，疑似有用内容列在“待复核”。历史数据库是 `data/state/notices.sqlite3`。

## 飞书发送

本机 `.env` 已配置飞书 Webhook；`.env` 被 `.gitignore` 排除。先用不发送模式看日报，确认后执行：

```powershell
python scripts/daily_digest.py --send --channels feishu
```

飞书发送不需要配置邮箱。成功发送记录会写入 SQLite；同一天相同内容再次运行不会重复发送。飞书条目只显示类型、截止时间、价值、材料/附件名称、唯一原文入口和末尾摘要，不再单独展示报名或附件链接。将来需要邮件时可配置 SMTP 并改用 `--channels both`。更多配置、GitHub Actions 22:00 定时运行方法及已知限制见[部署说明](docs/deployment.md)。

完整入口、采集范围和已知限制见[信息源清单](docs/sources.md)。附件流量、安全限制和格式支持见[附件与外部链接解析](docs/attachment_enrichment.md)。已核验的电信学院综测与推免计分事实保存在 `config/policy_rules.json`，原始 PDF 不进仓库。学工部官网目前要求校园 VPN，公开通知由武大官网和学生资助中心兜底；验证码阻断的详情不会伪装成抓取成功。研究数据和跨站评估见[本科生院报告](docs/undergraduate_school_report.md)，分站关键词见[分站关键词方案](docs/site_keyword_analysis.md)。
