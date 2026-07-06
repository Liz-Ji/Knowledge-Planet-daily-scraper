"""每日抓取任务入口：抓取知识星球(星主+精华) -> 写入飞书多维表格。

由 Windows 任务计划程序在「每次登录」时触发（详见 scripts/setup_task.ps1）。
配合下面几条逻辑，实现「每天第一次开机抓一次、失败自动重试并提醒、缺勤自动补齐」：

1. 每天只成功抓取一次：成功后记 state/last_success_date.json，当天再触发直接跳过。
2. 抓取失败或不完整：进程以非 0 退出码结束，且「不」标记当天已完成，
   任务计划程序会按设置自动重试；同时通过飞书机器人 Webhook 提醒。
3. 缺勤补齐：zsxq 抓取采用「翻到已抓过的帖子为止」，多天没开机时下次开机会自动多翻几页补齐。
"""
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
from src.summarizer import get_enricher

SCOPE_LABEL = {"by_owner": "星主", "digests": "精华"}
STATE_FILE = config.STATE_DIR / "seen_topic_ids.json"
DONE_MARKER = config.STATE_DIR / "last_success_date.json"


def setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    log_file = config.LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )
    # 降低第三方库噪声（openai/httpx 会把每次请求刷到 INFO）
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def today_str() -> str:
    return f"{datetime.now():%Y-%m-%d}"


def already_done_today() -> bool:
    if DONE_MARKER.exists():
        try:
            return json.loads(DONE_MARKER.read_text(encoding="utf-8")).get("date") == today_str()
        except Exception:
            return False
    return False


def mark_done_today():
    config.STATE_DIR.mkdir(exist_ok=True)
    DONE_MARKER.write_text(json.dumps({"date": today_str()}, ensure_ascii=False), encoding="utf-8")


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


def topic_to_record(topic: dict, group_name: str, enricher=None) -> dict:
    record = {
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
    # AI 加工：一句话摘要 + 主题标签（未配置模型或单条失败时跳过，不影响入库）
    if enricher and topic["content"]:
        try:
            result = enricher.enrich(topic["content"], topic["title"], group_name)
            record["摘要"] = result["摘要"]
            record["主题标签"] = result["标签"]
        except Exception:
            logging.exception(f"  AI加工失败，留空: {topic['topic_id']}")
    return record


def run() -> int:
    """执行一次抓取。返回进程退出码：0=成功，1=失败/不完整（供任务计划程序判断是否重试）。"""
    logging.info("=== 开始抓取任务 ===")

    force = "--force" in sys.argv
    if already_done_today() and not force:
        logging.info(f"今天({today_str()})已成功抓取过，跳过本次触发（加 --force 可强制重跑）")
        return 0

    groups = config.get_groups()
    if not groups:
        logging.error("未配置 ZSXQ_GROUPS，退出")
        return 1

    if not config.ZSXQ_COOKIE:
        logging.error("未配置 ZSXQ_COOKIE，退出")
        send_alert(config.FEISHU_ALERT_WEBHOOK, "【星球内容助手】未配置 ZSXQ_COOKIE，任务无法执行，请检查 .env")
        return 1

    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    zsxq = ZsxqClient(config.ZSXQ_COOKIE)
    enricher = get_enricher()  # 未配置 LLM key 时为 None，自动跳过 AI 加工

    seen_ids = load_seen_ids()
    if not seen_ids:
        logging.info("本地去重状态为空，首次运行将从飞书表格同步已有帖子ID")
        try:
            seen_ids = feishu.get_existing_topic_ids()
            logging.info(f"从飞书表格同步到 {len(seen_ids)} 条已有帖子ID")
        except Exception:
            logging.exception("同步飞书已有帖子ID失败，将按空集合处理")

    new_records = []
    failures = []  # [(星球, 范围, 原因)]，任一失败则本次视为不完整，稍后重试

    for group_id, group_name in groups:
        for scope in config.SCOPES:
            logging.info(f"抓取 {group_name}({group_id}) scope={scope}")
            try:
                topics = zsxq.fetch_topics(group_id, scope, known_ids=seen_ids)
            except CookieExpiredError as e:
                # Cookie 失效，后续都会失败，直接提醒并中止（不标记完成，下次开机重试）
                logging.error(f"知识星球 Cookie 已失效: {e}")
                send_alert(
                    config.FEISHU_ALERT_WEBHOOK,
                    "【星球内容助手】知识星球 Cookie 已失效，请重新登录 wx.zsxq.com 获取新 Cookie 更新到 .env。"
                    "更新后下次开机会自动补齐这次没抓到的内容。\n详情: " + str(e),
                )
                # 先把已抓到的写进去，避免浪费
                _write(feishu, new_records)
                save_seen_ids(seen_ids)
                return 1
            except Exception as e:
                logging.exception(f"  抓取失败: {group_name} {scope}")
                failures.append((group_name, SCOPE_LABEL.get(scope, scope), str(e)))
                continue

            new_count = 0
            for topic in topics:
                if topic["topic_id"] in seen_ids:
                    continue
                seen_ids.add(topic["topic_id"])
                new_records.append(topic_to_record(topic, group_name, enricher))
                new_count += 1
            logging.info(f"  新增 {new_count} 条（本次抓取共 {len(topics)} 条）")

    try:
        _write(feishu, new_records)
    except Exception as e:
        logging.exception("写入飞书失败")
        send_alert(config.FEISHU_ALERT_WEBHOOK, f"【星球内容助手】写入飞书表格失败，将在下次开机重试。\n详情: {e}")
        save_seen_ids(seen_ids)
        return 1

    save_seen_ids(seen_ids)

    if failures:
        detail = "\n".join(f"- {g} / {s}：{r[:80]}" for g, s, r in failures)
        logging.warning(f"本次有 {len(failures)} 个范围抓取失败，未标记当天完成，将在下次开机重试")
        send_alert(
            config.FEISHU_ALERT_WEBHOOK,
            f"【星球内容助手】今天有部分内容没抓完（{len(failures)} 项），下次开机会自动补齐重试：\n{detail}",
        )
        return 1

    mark_done_today()
    logging.info("=== 任务完成，已标记当天抓取成功 ===")
    return 0


def _write(feishu: FeishuClient, new_records: list):
    if new_records:
        feishu.batch_create_records(new_records)
        logging.info(f"已写入飞书表格 {len(new_records)} 条新记录")
    else:
        logging.info("没有新内容")


def main() -> int:
    setup_logging()
    try:
        return run()
    except Exception as e:
        logging.exception("任务异常终止")
        send_alert(config.FEISHU_ALERT_WEBHOOK, f"【星球内容助手】任务执行异常，将在下次开机重试。\n详情: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
