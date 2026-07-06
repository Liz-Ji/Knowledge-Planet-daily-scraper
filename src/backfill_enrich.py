"""给飞书表里「摘要」还空着的历史记录补做 AI 摘要+标签。

用途：AI 加工是后加的能力，之前抓的历史记录没有摘要/标签。
配好 .env 里的 LLM_* 后运行一次即可补齐：
    .venv\\Scripts\\python.exe src\\backfill_enrich.py
之后每天新抓的内容会在入库时自动加工，无需再跑本脚本。
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.feishu_client import FeishuClient
from src.summarizer import get_enricher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    enricher = get_enricher()
    if not enricher:
        logging.error("未配置 LLM（LLM_PROVIDER/LLM_API_KEY），无法补做加工。请先填 .env")
        return

    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    items = feishu.list_all_records()
    todo = [it for it in items if it["fields"].get("正文") and not it["fields"].get("摘要")]
    logging.info(f"共 {len(items)} 条，其中待补加工 {len(todo)} 条")

    updates = []
    for i, it in enumerate(todo, 1):
        fd = it["fields"]
        content = fd.get("正文", "")
        title = fd.get("标题", "") or ""
        group = fd.get("星球名称", "") or ""
        try:
            result = enricher.enrich(content, title, group)
        except Exception:
            logging.exception(f"  第 {i} 条加工失败，跳过: {it['record_id']}")
            continue
        updates.append({"record_id": it["record_id"], "fields": {"摘要": result["摘要"], "主题标签": result["标签"]}})
        if i % 20 == 0:
            logging.info(f"  已加工 {i}/{len(todo)}")
        # 每 100 条写一批，避免全部失败时前功尽弃
        if len(updates) >= 100:
            feishu.batch_update_records(updates)
            logging.info(f"  已写回 {len(updates)} 条")
            updates = []

    if updates:
        feishu.batch_update_records(updates)
        logging.info(f"  已写回 {len(updates)} 条")
    logging.info("补加工完成")


if __name__ == "__main__":
    main()
