"""每日定时任务入口：抓取知识星球(星主+精华) -> 写入飞书多维表格。"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.zsxq_client import ZsxqClient, CookieExpiredError
from src.feishu_client import FeishuClient
from src.notifier import send_alert

SCOPE_LABEL = {"by_owner": "星主", "digests": "精华"}
STATE_FILE = config.STATE_DIR / "seen_topic_ids.json"


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    log_file = config.LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def load_seen_ids() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen_ids(ids: set):
    config.STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def to_epoch_ms(iso_time: str) -> int:
    if not iso_time:
        return int(datetime.now().timestamp() * 1000)
    try:
        return int(datetime.fromisoformat(iso_time).timestamp() * 1000)
    except ValueError:
        return int(datetime.now().timestamp() * 1000)


def topic_to_record(topic: dict, group_name: str) -> dict:
    return {
        "帖子ID": topic["topic_id"],
        "星球名称": group_name,
        "类型": SCOPE_LABEL.get(topic["scope"], topic["scope"]),
        "作者": topic["author"],
        "标题": topic["title"],
        "正文": topic["content"],
        "发布时间": to_epoch_ms(topic["create_time"]),
        "点赞数": topic["likes_count"],
        "评论数": topic["comments_count"],
        "原文链接": {"link": topic["url"], "text": "查看原文"},
        "抓取时间": int(datetime.now().timestamp() * 1000),
    }


def main():
    setup_logging()
    logging.info("=== 开始每日抓取任务 ===")

    groups = config.get_groups()
    if not groups:
        logging.error("未配置 ZSXQ_GROUPS，退出")
        return

    if not config.ZSXQ_COOKIE:
        logging.error("未配置 ZSXQ_COOKIE，退出")
        send_alert(config.FEISHU_ALERT_WEBHOOK, "【星球内容助手】未配置 ZSXQ_COOKIE，任务无法执行，请检查 .env")
        return

    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    zsxq = ZsxqClient(config.ZSXQ_COOKIE)

    seen_ids = load_seen_ids()
    if not seen_ids:
        logging.info("本地去重状态为空，首次运行将从飞书表格同步已有帖子ID")
        try:
            seen_ids = feishu.get_existing_topic_ids()
            logging.info(f"从飞书表格同步到 {len(seen_ids)} 条已有帖子ID")
        except Exception:
            logging.exception("同步飞书已有帖子ID失败，将按空集合处理")

    new_records = []
    try:
        for group_id, group_name in groups:
            for scope in config.SCOPES:
                logging.info(f"抓取 {group_name}({group_id}) scope={scope}")
                topics = zsxq.fetch_topics(group_id, scope)
                new_count = 0
                for topic in topics:
                    if topic["topic_id"] in seen_ids:
                        continue
                    seen_ids.add(topic["topic_id"])
                    new_records.append(topic_to_record(topic, group_name))
                    new_count += 1
                logging.info(f"  新增 {new_count} 条（本次抓取共 {len(topics)} 条）")
    except CookieExpiredError as e:
        logging.error(f"知识星球 Cookie 已失效: {e}")
        send_alert(
            config.FEISHU_ALERT_WEBHOOK,
            f"【星球内容助手】知识星球 Cookie 已失效，请重新登录 wx.zsxq.com 获取新 Cookie 并更新 .env\n详情: {e}",
        )
        return

    if new_records:
        feishu.batch_create_records(new_records)
        logging.info(f"已写入飞书表格 {len(new_records)} 条新记录")
    else:
        logging.info("没有新内容")

    save_seen_ids(seen_ids)
    logging.info("=== 任务结束 ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("任务异常终止")
        send_alert(config.FEISHU_ALERT_WEBHOOK, f"【星球内容助手】任务执行异常: {e}")
        raise
