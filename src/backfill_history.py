"""一次性深翻历史，补全指定年份的全部内容（星主+精华）。

用途：日常抓取只回看最近约160条/范围，早期历史（如整个2025年）有大量缺口。
本脚本对两星球×(星主+精华)一直往回翻页到 SINCE 之前，筛出年份范围内的帖子，
去重 + AI加工 + 写飞书。跑一次即可，之后日常抓取自动接力。

默认补 2025 全年。改 SINCE/UNTIL 可补其它区间。
    .venv\\Scripts\\python.exe src\\backfill_history.py
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.zsxq_client import ZsxqClient, CookieExpiredError
from src.feishu_client import FeishuClient
from src.summarizer import get_enricher
from src.main import topic_to_record

SINCE = "2025-01-01"   # 含
UNTIL = "2026-01-01"   # 不含（即整个 2025 年）

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for _noisy in ("httpx", "openai", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def main():
    enricher = get_enricher()
    logging.info(f"AI加工: {'开启' if enricher else '关闭(未配LLM)'}")
    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    zsxq = ZsxqClient(config.ZSXQ_COOKIE)

    seen = feishu.get_existing_topic_ids()
    logging.info(f"表内已有 {len(seen)} 条，用于去重")

    pending = []
    total_new = 0

    def flush():
        nonlocal pending, total_new
        if pending:
            feishu.batch_create_records(pending)
            total_new += len(pending)
            logging.info(f"  已写入 {len(pending)} 条（累计 {total_new}）")
            pending = []

    for group_id, group_name in config.get_groups():
        for scope in config.SCOPES:
            logging.info(f"深翻 {group_name} scope={scope} 到 {SINCE} …")
            try:
                topics = zsxq.fetch_topics(group_id, scope, max_pages=300, stop_before=SINCE)
            except CookieExpiredError as e:
                logging.error(f"Cookie 失效，中止: {e}")
                flush()
                return
            year_new = [
                t for t in topics
                if SINCE <= t["create_time"] < UNTIL and t["topic_id"] not in seen
            ]
            logging.info(f"  抓到 {len(topics)} 条，其中 2025 年新增 {len(year_new)} 条，开始加工写入")
            for i, t in enumerate(year_new, 1):
                seen.add(t["topic_id"])
                pending.append(topic_to_record(t, group_name, enricher))
                if i % 20 == 0:
                    logging.info(f"    加工进度 {i}/{len(year_new)}")
                if len(pending) >= 100:
                    flush()

    flush()
    logging.info(f"=== 2025 全年补全完成，共新增 {total_new} 条 ===")


if __name__ == "__main__":
    main()
