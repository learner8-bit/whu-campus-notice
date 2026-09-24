# 飞书与云端定时运行（邮件可选）

## 本地先收到第一份日报

1. 在飞书群的“群机器人”中添加自定义机器人，复制 Webhook 地址。建议开启“签名校验”，将密钥填到 `FEISHU_SECRET`。若启用了自定义关键词，设置为“武汉大学校园通知日报”（消息标题包含这句话）。不建议对 GitHub 托管运行使用固定 IP 白名单，因为托管执行器的出口 IP 并不固定。
2. 本机 `.env` 填好 `FEISHU_WEBHOOK_URL`；`FEISHU_SECRET` 只有启用签名校验才需填写。邮箱字段可以全部留空。
3. 先运行 `python scripts/daily_digest.py` 查看 `data/runs/` 中的日报，再运行 `python scripts/daily_digest.py --send --channels feishu` 真正发送。未配置飞书地址时程序会在采集前报错。

今后若要恢复邮件：在服务商处开通 SMTP、获取专用授权码（不要使用网页登录密码），填写 `.env` 中的 `SMTP_HOST`、`SMTP_USER`、`SMTP_PASSWORD`、`MAIL_TO`，再使用 `--channels both`。默认 SMTP SSL/465；若服务商要求 STARTTLS/587，设置 `SMTP_SECURITY=starttls`、`SMTP_PORT=587`。

飞书使用[官方自定义机器人 Webhook 和签名格式](https://www.feishu.cn/content/7271149634339422210)。Webhook 本身相当于密钥，不要粘贴到公开仓库、日志或聊天截图。

## GitHub Actions

项目已有 `.github/workflows/daily-notice-digest.yml`。主任务在北京时间 20:43 排队并等待至 22:00 发送；22:11、22:41、23:11 各有一次补偿任务，23:30 后停止当天发送。任一轮成功后会记录完成标记，其余补偿任务直接跳过。也可在 Actions 页面手动触发。

1. 项目部署在公开 GitHub 仓库，默认分支包含工作流、代码和 `data/state/notices.sqlite3`。数据库仅保存公开通知内容和发送状态；Webhook、API Key 和本地 `.env` 不得提交。
2. 仓库已配置 `FEISHU_WEBHOOK_URL` Actions Secret；若机器人以后启用签名校验，再添加 `FEISHU_SECRET`。凭据仅保存在 GitHub Secrets，不要提交 `.env`。[GitHub Secrets 官方说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。
3. 确认仓库允许 Actions，且工作流有 `contents: write` 权限；首次可用 `workflow_dispatch` 手动触发。运行后查看飞书与 Actions 日志。

工作流会把公开通知的 SQLite 历史库和发送成功记录提交回默认分支，供下一次云端运行去重。历史数据默认保留 90 天。若仓库保护规则禁止机器人直接推送，需要为状态库另选持久存储或调整仓库规则。


## DeepSeek 与政策知识库

每日任务仅对新增或内容变化的通知调用 DeepSeek，发送范围为公开通知标题、正文、附件提取文字、外部页面提取文字，以及与该通知关键词匹配的已核验政策摘录。不会发送飞书密钥、SQLite 数据库、原始 PDF 或本地文件。

在仓库 Settings → Secrets and variables → Actions 中增加 AI_API_KEY。工作流固定使用 AI_PROVIDER=deepseek、AI_MODEL=deepseek-flash；每次请求最多等待 120 秒，失败后最多重试 2 次，最终失败时自动发送无 AI 的规则筛选原文速览。AI 结果按通知内容哈希、模型和提示词版本写入 SQLite 缓存，同一内容不会重复付费。

目标用户画像为武汉大学电子信息学院 2025 级电子信息本科生，目前大二。政策事实保存在 config/policy_rules.json，每条均带来源文件和页码。2024 年综测细则可用于当前比对；现有推免文件仅适用于 2026 届，对 2025 级只能显示为往届参考，不能作为未来确定分值。

## 当前边界

- 已接入 10 个站点组，完整范围见 `docs/sources.md`。学工部官网要求校园 VPN，云端目前通过武大官网通知和学生资助中心兜底。
- `rules_v1` 的研究判定保留原状。实际日报另有少量“防漏报”保护：本科生院学生通知中的录取名单、立项名单、公共教室开放被列为待复核，不直接丢弃。仍需以官网原文核对条件和截止时间。
- 普通官网附件目前提取链接与名称；第二课堂额外提取报名时间、活动时间、地点、名额和学时。遇到官网验证码会保留标题和原始地址，正文不可用时明确提示。
- 飞书本地和云端发送均已配置；邮件当前关闭。GitHub Actions 已完成首次云端运行，本地电脑关闭不影响后续定时推送。
