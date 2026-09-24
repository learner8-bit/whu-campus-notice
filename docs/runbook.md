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
