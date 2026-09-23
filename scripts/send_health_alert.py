"""Send a last-resort Feishu alert when the workflow fails before collection runs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from whu_notice_research.notify import DeliveryConfig, send_feishu  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    message = (
        f"⚠️ 武大校园信息系统运行失败｜{day}\n"
        "云端任务没有正常完成，今天的信息可能没有完整采集或推送。\n"
        f"运行记录：{args.run_url}\n"
        "请到电脑上运行一键测试程序，查看错误并处理。"
    )
    send_feishu(DeliveryConfig.from_environment("feishu"), message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
