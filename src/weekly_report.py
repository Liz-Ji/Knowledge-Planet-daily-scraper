"""每周精华周报：汇总过去7天入库的内容，用大模型写成周报，推送到飞书群。

由 Windows 任务计划每周日 20:00 触发（见 scripts/setup_task.ps1）。
    .venv\\Scripts\\python.exe src\\weekly_report.py          # 生成并推送
    .venv\\Scripts\\python.exe src\\weekly_report.py --dry    # 只打印不推送（调试）
"""
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # Windows 控制台默认 GBK，避免 print 含 emoji/生僻字时报错
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src import config
from src.feishu_client import FeishuClient
from src.notifier import send_alert
from src import summarizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for _noisy in ("httpx", "openai", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

DAYS = 7
MAX_POSTS = 60  # 喂给模型的上限，按点赞取前 N


def to_int(v) -> int:
    """飞书数字字段读回来是字符串，安全转 int。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

SYSTEM = (
    "你是知识星球内容周报编辑。根据给你的「过去一周帖子清单」（含星球、作者、点赞数、标签、一句话摘要、链接），"
    "写一份简洁、能快速扫读的中文周报：\n"
    "① 开头用一两句话总述本周两个星球的整体看点；\n"
    "② 按主题归类要点（用清单里出现的标签，如宏观经济/房产楼市/投资理念等），每类挑2~4条最有价值的，"
    "每条一句话，尽量点名作者；\n"
    "③ 末尾「值得细读 Top5」：挑点赞最高或信息量最大的5条，每条格式为"
    "『作者：一句话看点 链接』，链接用清单里给的原文链接。\n"
    "控制在600字以内，用纯文本（可用 一、二、①②、— 等符号分层），不要用 markdown 表格或代码块。"
)


def main():
    dry = "--dry" in sys.argv
    if not summarizer.is_configured():
        logging.error("未配置 LLM，无法生成周报。请先在 .env 配好 LLM_*")
        return

    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    items = feishu.list_all_records()

    since = datetime.now() - timedelta(days=DAYS)
    week = []
    for it in items:
        t = it["fields"].get("发布时间")
        if not t:
            continue
        dt = datetime.fromtimestamp(t / 1000)
        if dt >= since:
            week.append((dt, it["fields"]))
    week.sort(key=lambda x: to_int(x[1].get("点赞数")), reverse=True)

    end = datetime.now()
    title = f"【星球精华周报 {since:%m.%d}–{end:%m.%d}】"

    if not week:
        text = f"{title}\n本周两个星球暂无新增星主/精华内容。"
        logging.info("本周无新增内容")
        if not dry:
            send_alert(config.FEISHU_ALERT_WEBHOOK, text)
        else:
            print(text)
        return

    logging.info(f"本周共 {len(week)} 条，取点赞前 {min(len(week), MAX_POSTS)} 条生成周报")
    lines = []
    for i, (dt, fd) in enumerate(week[:MAX_POSTS], 1):
        summary = fd.get("摘要") or (fd.get("正文", "")[:40])
        tags = "/".join(fd.get("主题标签") or [])
        link = (fd.get("原文链接") or {}).get("link", "")
        lines.append(
            f"[{i}] {fd.get('星球名称','')} | {fd.get('作者','')} | 赞{fd.get('点赞数',0)} | {tags} | {summary} | {link}"
        )
    user = f"时间范围：{since:%Y-%m-%d} ~ {end:%Y-%m-%d}\n共 {len(week[:MAX_POSTS])} 条：\n" + "\n".join(lines)

    body = summarizer.chat(SYSTEM, user, max_tokens=1600, temperature=0.4).strip()
    # 飞书文本消息不渲染 markdown，去掉加粗/标题符号，避免显示成星号
    body = body.replace("**", "").replace("__", "")
    body = "\n".join(line.lstrip("# ").rstrip() for line in body.splitlines())
    report = f"{title}\n{body}"

    if dry:
        print(report)
    else:
        send_alert(config.FEISHU_ALERT_WEBHOOK, report)
        logging.info("周报已推送到飞书群")

    # 每周同时全量刷新知识图谱综述（--dry 不刷，避免测试时空跑24次）
    if not dry:
        try:
            from src import build_graph
            build_graph.build(refresh=True)
        except Exception:
            logging.exception("刷新知识图谱失败")


if __name__ == "__main__":
    main()
