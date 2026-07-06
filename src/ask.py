"""命令行语义问答：从飞书里已抓的星球内容中检索相关帖子，用大模型综合回答。

    .venv\\Scripts\\python.exe src\\ask.py "姜胡说怎么看黄金"

检索方式（DeepSeek 无 embedding，用轻量 RAG）：
- 若问题里点了某个星球名，只在该星球内检索；
- 用 jieba 从问题里抽关键词，按关键词在标题/正文/摘要里的命中次数打分（点赞做加权），
  取最相关的若干条，连同原文链接一起交给大模型综合回答并给出引用。
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import jieba.analyse

from src import config
from src.feishu_client import FeishuClient
from src import summarizer

logging.getLogger("jieba").setLevel(logging.WARNING)
for _noisy in ("httpx", "openai", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

TOP_K = 18            # 交给模型的相关帖子条数
SNIPPET = 220         # 每条正文截断长度


def to_int(v) -> int:
    """飞书数字字段读回来是字符串，安全转 int。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

SYSTEM = (
    "你是知识星球内容问答助手。只能依据我提供的『相关帖子』来回答用户问题，不要编造或使用帖子之外的知识。\n"
    "要求：综合归纳这些帖子里的观点直接回答问题；如果这些帖子不足以回答，就明确说"
    "『现有内容里没找到相关观点』。回答末尾列出你引用到的帖子，格式为『作者：链接』。语言简洁、分点。"
)


def _text_of(fd: dict) -> str:
    return " ".join([fd.get("标题", "") or "", fd.get("摘要", "") or "", fd.get("正文", "") or ""])


def retrieve(items, question, group_names):
    # 若问题里点了星球名，先按星球过滤
    target = next((g for g in group_names if g and g in question), None)
    pool = [it for it in items if not target or it["fields"].get("星球名称") == target]

    keywords = [k for k in jieba.analyse.extract_tags(question, topK=6) if len(k) > 1]
    # 去掉星球名本身当关键词
    keywords = [k for k in keywords if k not in group_names]

    scored = []
    for it in pool:
        fd = it["fields"]
        text = _text_of(fd)
        hit = sum(text.count(k) for k in keywords)
        if hit == 0:
            continue
        score = hit + to_int(fd.get("点赞数")) * 0.002
        scored.append((score, fd))
    scored.sort(key=lambda x: x[0], reverse=True)
    return target, keywords, [fd for _, fd in scored[:TOP_K]]


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('用法: python src/ask.py "你的问题"，例如 python src/ask.py "姜胡说怎么看黄金"')
        return
    question = " ".join(sys.argv[1:]).strip()

    if not summarizer.is_configured():
        print("未配置 LLM（.env 里的 LLM_*），无法回答。")
        return

    feishu = FeishuClient(
        config.FEISHU_APP_ID, config.FEISHU_APP_SECRET, config.FEISHU_APP_TOKEN, config.FEISHU_TABLE_ID
    )
    items = feishu.list_all_records()
    group_names = [name for _, name in config.get_groups()]

    target, keywords, hits = retrieve(items, question, group_names)
    scope_note = f"（范围：{target}）" if target else "（范围：全部星球）"
    print(f"问题：{question} {scope_note}")
    print(f"关键词：{keywords}｜命中相关帖子：{len(hits)} 条\n")

    if not hits:
        print("现有内容里没找到相关帖子。可以换个说法，或确认该话题是否被抓取过。")
        return

    ctx = []
    for i, fd in enumerate(hits, 1):
        link = (fd.get("原文链接") or {}).get("link", "")
        body = (fd.get("正文", "") or fd.get("摘要", ""))[:SNIPPET]
        ctx.append(f"[{i}] 星球:{fd.get('星球名称','')} 作者:{fd.get('作者','')} 链接:{link}\n{body}")
    user = f"用户问题：{question}\n\n相关帖子：\n" + "\n\n".join(ctx)

    answer = summarizer.chat(SYSTEM, user, max_tokens=1200, temperature=0.3).strip()
    print(answer)


if __name__ == "__main__":
    main()
