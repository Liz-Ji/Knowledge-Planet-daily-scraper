"""可切换的大模型能力层：既做「摘要+标签」，也提供通用 chat() 给周报/问答用。

设计目标：主流程不关心背后用哪个模型，换模型只改 .env、不改代码。
- get_enricher()：返回 enrich(text)->{"摘要":..,"标签":[..]} 的对象，或 None（未配 key）。
- chat(system, user)：通用自由生成，周报汇总、问答综合都用它。
- 底层由 .env 的 LLM_PROVIDER 选择：deepseek/openai/其它 OpenAI 兼容服务走同一接口；claude 走官方 SDK。
"""
import json
import re
import logging

from src import config
from src.topics import TOPIC_NAMES

# 主题标签固定词表（必须与飞书「主题标签」多选字段的选项保持一致）
TAGS = [
    "宏观经济", "政策解读", "房产楼市", "股市大盘", "个股行业",
    "投资理念", "市场情绪", "创业经商", "认知成长", "其他",
]

_SUMMARY_SYSTEM = (
    "你是知识星球内容的整理助手。给你一条帖子，你需要：\n"
    "1) 用一句话（不超过40个汉字）概括帖子的核心观点，作为摘要；\n"
    f"2) 从固定标签词表里挑1~3个最贴切的主题标签（只能从中选，不要自造）：{TAGS}\n"
    f"3) 从固定专题词表里挑【1个】最贴切的专题（只能从中选，不要自造）：{TOPIC_NAMES}\n"
    "只输出一个 JSON 对象，格式为："
    '{"摘要": "一句话摘要", "标签": ["标签1"], "专题": "专题名"}，不要输出任何多余内容。'
)


def is_configured() -> bool:
    return bool((config.LLM_PROVIDER or "").strip() and config.LLM_API_KEY.strip())


def chat(system: str, user: str, max_tokens: int = 1500, temperature: float = 0.3,
         *, provider: str = None, model: str = None, api_key: str = None,
         base_url: str = None) -> str:
    """通用单轮补全，返回纯文本。默认按后台 .env 的 LLM_*；也可显式传入覆盖
    （面板用 PANEL_LLM_* 走 Claude，后台仍用 DeepSeek，互不影响）。"""
    provider = (provider if provider is not None else config.LLM_PROVIDER or "").strip().lower()
    model = (model if model is not None else config.LLM_MODEL or "").strip()
    api_key = api_key if api_key is not None else config.LLM_API_KEY
    base_url = base_url if base_url is not None else config.LLM_BASE_URL
    if provider == "claude":
        import anthropic  # 延迟导入
        client = anthropic.Anthropic(api_key=api_key)
        # 关闭 extended thinking：这些是结构化抽取/生成任务，开着会把 token 预算耗在思考上、
        # 甚至只返回 thinking 没有正文（踩过：15条笔记整理时 6000 token 全花在思考、正文为空）。
        kwargs = dict(model=model or "claude-opus-4-8", max_tokens=max_tokens,
                      system=system, messages=[{"role": "user", "content": user}])
        try:
            resp = client.messages.create(thinking={"type": "disabled"}, **kwargs)
        except Exception:
            resp = client.messages.create(**kwargs)  # 老模型不认 thinking 参数则退回
        return next((b.text for b in resp.content if b.type == "text"), "")
    # 其余一律走 OpenAI 兼容接口（deepseek / openai / 通义 / kimi / 智谱 …）
    from openai import OpenAI  # 延迟导入
    client = OpenAI(api_key=api_key, base_url=base_url or None)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _build_summary_prompt(text: str, title: str = "", group: str = "") -> str:
    parts = []
    if group:
        parts.append(f"星球：{group}")
    if title:
        parts.append(f"标题：{title}")
    parts.append(f"正文：{text}")
    return "\n".join(parts)


def _parse_result(raw: str) -> dict:
    """从模型输出里稳健地解析出 {摘要, 标签}，容忍多余文字/代码块包裹。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data = {}
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = {}
    summary = str(data.get("摘要", "")).strip()[:100]
    tags = data.get("标签", [])
    if isinstance(tags, str):
        tags = [tags]
    clean_tags = []
    for t in tags:
        t = str(t).strip()
        if t in TAGS and t not in clean_tags:
            clean_tags.append(t)
    if not clean_tags:
        clean_tags = ["其他"]
    topic = str(data.get("专题", "")).strip()
    if topic not in TOPIC_NAMES:
        topic = ""  # 词表外则留空，避免污染单选字段
    return {"摘要": summary, "标签": clean_tags[:3], "专题": topic}


class _Enricher:
    def enrich(self, text: str, title: str = "", group: str = "") -> dict:
        raw = chat(_SUMMARY_SYSTEM, _build_summary_prompt(text, title, group),
                   max_tokens=1024, temperature=0.2)
        return _parse_result(raw)


def get_enricher():
    """未配置 key 时返回 None（主流程跳过加工）。"""
    if not is_configured():
        logging.info("未配置 LLM_PROVIDER/LLM_API_KEY，本次跳过 AI 摘要+标签加工")
        return None
    return _Enricher()
