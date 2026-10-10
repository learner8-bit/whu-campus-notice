# 采集与验证运行手册

在项目根目录执行，Python 3.11+，仅依赖标准库。当前只读取公开页面和附件链接元数据，不下载附件实体。

## 一次性历史基线

```powershell
python scripts/collect_eis_samples.py --days 180
python scripts/collect_undergraduate_school.py --days 180
python scripts/predict_undergraduate_school.py
python scripts/build_undergraduate_labels.py
python scripts/evaluate_rules_v1.py
python scripts/bootstrap_state.py data/eis/notices.jsonl data/undergraduate_school/notices.jsonl
```

`build_undergraduate_labels.py` 中的标题标签集是当前逐条判读初稿，尚需独立人工复核；若重新采集后出现新标题，脚本会要求先补齐标签，避免把未标注数据混入准确率计算。新数据应单独留作测试，不要直接拿来调整冻结的 `rules_v1` 后再报告同一批评估值。电子信息学院的历史标注和研究见 `docs/eis_research_report.md`。

## 日常增量

```powershell
python scripts/run_incremental.py --site undergraduate_school
python scripts/run_incremental.py --site eis
python scripts/show_new_today.py --site undergraduate_school
python -m unittest discover -s tests -v
```

默认回看 3 天，可用 `--days` 调整。采集器遍历窗口内列表，已成功入库的通知不重新抓取详情；详情失败的记录在回看窗口内可以重试。`data/runs/<site>_new.jsonl` 仅保存最近一次运行新增的记录；完整历史与首次发现时间保存在 `data/state/notices.sqlite3`，默认保留 90 天。`show_new_today.py --day YYYY-MM-DD` 查询北京时间指定日期首次发现的在线新增通知，不包括一次性历史导入。首次运行如无历史库会建立基线，避免把历史通知报为当天新增。

`Notice` 字段统一包含站点、栏目、发布时间、标题、规范 URL、摘要、详情标题/栏目、正文、附件引用、正文链接、抓取时间及抓取错误。新增网站时实现单独适配器，在 `sites.py` 注册；规则和 SQLite 不依赖网站的 HTML 结构。

## 验证限制

本科生院部分旧链接会转到官网验证码页。程序会把这类详情记为 `BlockedPageError`，保留列表标题和原始地址，绝不将验证码页当正文。当前 180 天历史样本中 30/82 条受此影响，因此附件和正文统计是已取得内容的下限。原始误采备份在 `data/undergraduate_school/notices.pre-verification-fix.jsonl` 和 `data/state/notices.pre-verification-fix.sqlite3`；修复脚本 `scripts/repair_uc_challenge_rows.py` 只用于恢复这批旧样本，不绕过验证码。

## 公众号正文失败与补读

公众号发现通道 `healthy` 只表示成功发现文章，**不代表正文已读取**。同步日志另列 `wechat content: complete=... incomplete=...`；正文状态保存在 Notice 的 `content_quality` 和 `fetch_error` 中。

正文失败不再依赖文章发现游标：`wechat_content_attempts` 独立记录尝试，每篇每天最多读取一次、累计最多三次，只补读发现后七天以内的不完整记录，一批最多20条。正常同步自动补读，不回扫历史文章列表，也不绕过验证码。升级前失败记录没有尝试表时可自动纳入补读。其他发现通道重复返回同一篇文章也不能突破重试限制或覆盖已成功读取的正文。

独立修复命令（不发现新文章、不发送飞书、不运行 AI）：

```powershell
python scripts/repair_wechat_content.py --database data/state/notices.sqlite3
```

Cloud Run 包装器支持 `RUN_PHASE=wechat-content-repair`；该阶段可在发送窗口外运行，但仍使用对象锁、状态版本校验和私有凭证存储。不要在不确定状态是否生产的情况下手工覆盖数据库。

补读成功的通知可进入补读当日的筛选集合，AI 根据新的正文内容哈希重新分析。心理活动等现有排除条件保持不变，不因补读成功就自动推送。

## 修复后的更新日报

用户于 2026-10-10 明确授权：影响日报采集、正文、筛选、排版或发送的故障修复并验证后，重新筛选并补发对应日期的更新日报，无需再次询问。该授权是修复运维流程，不改变正常 22:00 调度，不代表关闭日常防重复。

补发应在云端使用既有对象锁和数据库版本校验；以修复后的当日候选与有效补读数据生成日报并重新冻结。只针对补发日期更新发送完成标记，保留已经发送的分片记录，失败时补偿任务能补发未成功部分。避免 `TEST_MODE` 跳过生产状态保存或永久启用 `--force-send`，否则已补读内容可能再次推送。核验飞书成功返回并保存发送记录后，才报告发送完成。

发送阶段从冻结集合及当日失败补读记录重建正文健康告警，先汇总故障再筛选，故障不会因为该通知被过滤而消失。日报包含简短覆盖缺口提示；完整告警另发飞书，并保存为 `data/runs/health_YYYY-MM-DD.txt`。告警发送失败时不标记当日全部完成，补偿任务只补发失败告警，已成功的日报分片不会重复。
