"""把表里所有记录的「原文链接」重建为正确的 mweb 格式。

旧格式 dweb2/topic_detail 点开提示没有权限；正确格式是知识星球分享短链
跳转到的 mweb 详情页。链接完全由「帖子ID」推导，幂等，可重复运行。
    .venv\\Scripts\\python.exe src\\fix_links.py
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.feishu_client import FeishuClient
from src.zsxq_client import topic_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    items = feishu.list_all_records()
    updates = []
    for it in items:
        tid = it["fields"].get("帖子ID")
        if not tid:
            continue
        correct = topic_url(tid)
        cur = it["fields"].get("原文链接") or {}
        if cur.get("link") == correct:
            continue  # 已是正确格式，跳过
        updates.append({
            "record_id": it["record_id"],
            "fields": {"原文链接": {"link": correct, "text": "查看原文"}},
        })
    logging.info(f"共 {len(items)} 条，需修正链接 {len(updates)} 条")
    feishu.batch_update_records(updates)
    logging.info("链接修正完成")


if __name__ == "__main__":
    main()
