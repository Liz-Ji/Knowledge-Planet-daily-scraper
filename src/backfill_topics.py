"""给飞书里「专题」还空着的记录批量归类（图谱骨架用）。

一次给大模型一批（默认15条）摘要，让它各挑1个最贴切的专题（只能从 topics.TOPICS 里选）。
配好 .env 的 LLM_* 后跑一次；之后新帖在入库加工时会自动带专题，无需再跑。
    .venv\\Scripts\\python.exe src\\backfill_topics.py
"""
import sys, json, re, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.feishu_client import FeishuClient
from src import summarizer
from src.topics import TOPIC_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for _n in ("httpx", "openai", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)

BATCH = 15

SYS = (
    "你是内容分类助手。给你一批帖子摘要，和一个固定的专题词表。"
    "请为每条帖子挑选【1个】最贴切的专题——只能从词表里选，不要自造。\n"
    f"专题词表：{TOPIC_NAMES}\n"
    '只输出一个 JSON 对象，键是帖子序号、值是专题名，例如 {"1":"黄金与贵金属","2":"成长心态与习惯"}。'
)


def classify_batch(batch):
    listing = "\n".join(f"[{i+1}] {b['sum']}" for i, b in enumerate(batch))
    raw = summarizer.chat(SYS, f"帖子：\n{listing}", max_tokens=800, temperature=0.1)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    mapping = json.loads(m.group(0)) if m else {}
    out = []
    for i, b in enumerate(batch):
        name = str(mapping.get(str(i + 1), "")).strip()
        out.append(name if name in TOPIC_NAMES else "")
    return out


def main():
    if not summarizer.is_configured():
        logging.error("未配置 LLM，无法分类。请先填 .env")
        return
    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    items = feishu.list_all_records()
    todo = [
        {"rid": it["record_id"], "sum": it["fields"].get("摘要") or (it["fields"].get("正文", "")[:50])}
        for it in items
        if (it["fields"].get("摘要") or it["fields"].get("正文")) and not it["fields"].get("专题")
    ]
    logging.info(f"共 {len(items)} 条，待归类 {len(todo)} 条")

    updates, done, miss = [], 0, 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            names = classify_batch(batch)
        except Exception:
            logging.exception(f"  批次 {i} 分类失败，跳过")
            continue
        for b, name in zip(batch, names):
            if name:
                updates.append({"record_id": b["rid"], "fields": {"专题": name}})
            else:
                miss += 1
        done += len(batch)
        if done % 150 == 0:
            logging.info(f"  已处理 {done}/{len(todo)}（待写回 {len(updates)}，未匹配 {miss}）")
        if len(updates) >= 100:
            feishu.batch_update_records(updates)
            logging.info(f"  写回 {len(updates)} 条")
            updates = []
    if updates:
        feishu.batch_update_records(updates)
        logging.info(f"  写回 {len(updates)} 条")
    logging.info(f"=== 分类完成，未能归类 {miss} 条（词表外，已留空）===")


if __name__ == "__main__":
    main()
