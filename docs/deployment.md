# 飞书与云端定时运行（邮件可选）

## 本地先收到第一份日报

1. 在飞书群的“群机器人”中添加自定义机器人，复制 Webhook 地址。建议开启“签名校验”，将密钥填到 `FEISHU_SECRET`。若启用了自定义关键词，设置为“武汉大学校园通知日报”（消息标题包含这句话）。不建议对 GitHub 托管运行使用固定 IP 白名单，因为托管执行器的出口 IP 并不固定。
2. 本机 `.env` 填好 `FEISHU_WEBHOOK_URL`；`FEISHU_SECRET` 只有启用签名校验才需填写。邮箱字段可以全部留空。
3. 先运行 `python scripts/daily_digest.py` 查看 `data/runs/` 中的日报，再运行 `python scripts/daily_digest.py --send --channels feishu` 真正发送。未配置飞书地址时程序会在采集前报错。

今后若要恢复邮件：在服务商处开通 SMTP、获取专用授权码（不要使用网页登录密码），填写 `.env` 中的 `SMTP_HOST`、`SMTP_USER`、`SMTP_PASSWORD`、`MAIL_TO`，再使用 `--channels both`。默认 SMTP SSL/465；若服务商要求 STARTTLS/587，设置 `SMTP_SECURITY=starttls`、`SMTP_PORT=587`。

飞书使用[官方自定义机器人 Webhook 和签名格式](https://www.feishu.cn/content/7271149634339422210)。Webhook 本身相当于密钥，不要粘贴到公开仓库、日志或聊天截图。

## GitHub Actions

项目已有 `.github/workflows/daily-digest.yml`，每天北京时间约 22:07 运行，也可在 Actions 页面手动触发。GitHub 的定时任务可能延迟，并非严格的秒级闹钟。[GitHub 官方文档](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)说明了 `timezone` 用法。

1. 将整个项目放进你控制的 GitHub 仓库，建议选私有仓库；确保默认分支包含工作流、代码和 `data/state/notices.sqlite3`。当前本地目录尚未连接 GitHub 仓库，因此**这一步还没有实际部署**。
2. 在仓库 Settings → Secrets and variables → Actions 添加 `FEISHU_WEBHOOK_URL`；若机器人启用了签名校验，再添加 `FEISHU_SECRET`。凭据仅保存在 GitHub Secrets，不要提交 `.env`。[GitHub Secrets 官方说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)。
3. 确认仓库允许 Actions，且工作流有 `contents: write` 权限；首次可用 `workflow_dispatch` 手动触发。运行后查看飞书与 Actions 日志。

工作流会把公开通知的 SQLite 历史库和发送成功记录提交回默认分支，供下一次云端运行去重。若仓库保护规则禁止机器人直接推送，需要为状态库另选持久存储或调整仓库规则。推送状态失败可能导致下一次重发；渠道发送本身不支持原子事务，这是当前 V1 的边界。建议私有仓库，避免公开镜像通知正文和附件地址。

## 当前边界

- 仅电子信息学院和本科生院两个官网；新站点通过独立适配器接入，不需要改邮件和飞书模块。
- `rules_v1` 的研究判定保留原状。实际日报另有少量“防漏报”保护：本科生院学生通知中的录取名单、立项名单、公共教室开放被列为待复核，不直接丢弃。仍需以官网原文核对条件和截止时间。
- 附件目前只提取链接与名称；遇到官网验证码保留标题和原始地址，正文不可用会明确提示。
- 飞书本地发送已经配置；邮件当前关闭。没有 GitHub 仓库时，云端工作流无法自行启动，且本地电脑关闭后不会自动推送。
