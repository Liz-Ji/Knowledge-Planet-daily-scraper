"""可切换的大模型「摘要 + 主题标签」适配器。

设计目标：主流程不关心背后用哪个模型，换模型只改 .env、不改代码。

- 对外只暴露 get_enricher()：返回一个 enrich(text)->{"摘要":..,"标签":[..]} 的对象，
  或 None（未配置 key 时，主流程自动跳过加工，不影响抓取入库）。
- 底层由 .env 的 LLM_PROVIDER 选择适配器：
    · deepseek / openai / 其它 OpenAI 兼容服务（通义、Kimi、智谱…）
        → 共用 OpenAI Chat Completions 接口，改 LLM_BASE_URL + LLM_MODEL + LLM_API_KEY 即可切换
    · claude
        → 用 Anthropic 官方 SDK
- 提示词与要求返回的 JSON 结构在所有模型间保持一致，输出稳定、不锁定任何一家。
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

SYSTEM_PROMPT = (
    "你是知识星球内容的整理助手。给你一条帖子，你需要：\n"
    "1) 用一句话（不超过40个汉字）概括帖子的核心观点，作为摘要；\n"
    "2) 从给定的固定标签词表里，挑选1~3个最贴切的主题标签。\n"
    f"标签只能从这个词表里选，不要自造：{TAGS}\n"
    "只输出一个 JSON 对象，格式为："
    '{"摘要": "一句话摘要", "标签": ["标签1", "标签2"]}，不要输出任何多余内容。'
)


def _build_user_prompt(text: str, title: str = "", group: str = "") -> str:
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
    # 只保留词表内的标签，去重、限 3 个
    clean_tags = []
    for t in tags:
        t = str(t).strip()
        if t in TAGS and t not in clean_tags:
            clean_tags.append(t)
    if not clean_tags:
        clean_tags = ["其他"]
    return {"摘要": summary, "标签": clean_tags[:3]}


class _OpenAICompatEnricher:
    """OpenAI 兼容接口适配器：DeepSeek / OpenAI(Codex) / 通义 / Kimi / 智谱 等。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI  # 延迟导入，只有用到才需要装 openai

        self._client = OpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    def enrich(self, text: str, title: str = "", group: str = "") -> dict:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(text, title, group)},
            ],
            temperature=0.2,
        )
        return _parse_result(resp.choices[0].message.content or "")


class _ClaudeEnricher:
    """Anthropic 官方 SDK 适配器。"""

    def __init__(self, api_key: str, model: str):
        import anthropic  # 延迟导入

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model or "claude-opus-4-8"

    def enrich(self, text: str, title: str = "", group: str = "") -> dict:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(text, title, group)}],
        )
        raw = next((b.text for b in resp.content if b.type == "text"), "")
        return _parse_result(raw)


def get_enricher():
    """根据 .env 配置返回一个 enricher；未配置 key 时返回 None（主流程跳过加工）。"""
    provider = (config.LLM_PROVIDER or "").strip().lower()
    api_key = config.LLM_API_KEY.strip()
    if not provider or not api_key:
        logging.info("未配置 LLM_PROVIDER/LLM_API_KEY，本次跳过 AI 摘要+标签加工")
        return None

    try:
        if provider == "claude":
            return _ClaudeEnricher(api_key, config.LLM_MODEL)
        # 其余一律走 OpenAI 兼容接口（deepseek / openai / 通义 / kimi / 智谱 …）
        return _OpenAICompatEnricher(api_key, config.LLM_BASE_URL, config.LLM_MODEL)
    except Exception:
        logging.exception(f"初始化 enricher 失败(provider={provider})，本次跳过加工")
        return None
