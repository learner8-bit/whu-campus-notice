# 武汉大学校园通知日报

现有可运行链路：电子信息学院、本科生院公开通知 → 增量采集与历史去重 → 规则筛选 → 当天日报 → 飞书群机器人。邮件模块保留为可选项，目前关闭。每天即使没有新增候选，也发送“今日暂无新增”。暂不使用 AI API。

## 现在预览

Python 3.11+，无第三方 Python 依赖。在项目目录执行：

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

飞书发送不需要配置邮箱。成功发送记录会写入 SQLite；同一天相同内容再次运行不会重复发送。飞书长消息自动分段。将来需要邮件时可配置 SMTP 并改用 `--channels both`。更多配置、GitHub Actions 22:07 定时运行方法及已知限制见[部署说明](docs/deployment.md)。

当前只覆盖两个已实现的网站；原计划的学工部、资助中心、团委、国际交流部等仍需逐站接入。旧本科生院页面有 30 条被官网验证码阻断，不会伪装成详情抓取成功。研究数据和跨站评估见[本科生院报告](docs/undergraduate_school_report.md)。
