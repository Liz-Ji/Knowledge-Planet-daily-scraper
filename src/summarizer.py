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

# 主题标签固定词表（必须与飞书「主题标签」多选字段的选项保持一致）
TAGS = [
    "宏观经济", "政策解读", "房产楼市", "股市大盘", "个股行业",
    "投资理念", "市场情绪", "创业经商", "认知成长", "其他",
]

_SUMMARY_SYSTEM = (
    "你是知识星球内容的整理助手。给你一条帖子，你需要：\n"
    "1) 用一句话（不超过40个汉字）概括帖子的核心观点，作为摘要；\n"
    "2) 从给定的固定标签词表里，挑选1~3个最贴切的主题标签。\n"
    f"标签只能从这个词表里选，不要自造：{TAGS}\n"
    "只输出一个 JSON 对象，格式为："
    '{"摘要": "一句话摘要", "标签": ["标签1", "标签2"]}，不要输出任何多余内容。'
)


def is_configured() -> bool:
    return bool((config.LLM_PROVIDER or "").strip() and config.LLM_API_KEY.strip())


def chat(system: str, user: str, max_tokens: int = 1500, temperature: float = 0.3) -> str:
    """通用单轮补全，返回纯文本。按 .env 的 provider 选择底层模型。"""
    provider = (config.LLM_PROVIDER or "").strip().lower()
    model = config.LLM_MODEL.strip()
    if provider == "claude":
        import anthropic  # 延迟导入
        client = anthropic.Anthropic(api_key=config.LLM_API_KEY)
        resp = client.messages.create(
            model=model or "claude-opus-4-8", max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return next((b.text for b in resp.content if b.type == "text"), "")
    # 其余一律走 OpenAI 兼容接口（deepseek / openai / 通义 / kimi / 智谱 …）
    from openai import OpenAI  # 延迟导入
    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL or None)
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
    return {"摘要": summary, "标签": clean_tags[:3]}


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
