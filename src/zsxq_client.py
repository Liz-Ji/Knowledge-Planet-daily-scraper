"""知识星球(ZSXQ)未官方 API 客户端。

访问方式（2026-07 实测有效）：
- 端点用 v2：https://api.zsxq.com/v2/groups/{group_id}/topics
- 请求头需带 Authorization(=zsxq_access_token) + User-Agent + x-version，
  缺 x-version 会被判定为「版本太旧」而拒绝。
- ZSXQ 会对非官方工具做概率性拦截（返回 code=1059），约 1/5 概率，
  并非硬封禁，遇到时退避重试即可。
"""
import re
import time
import uuid
import logging
import urllib.parse
import requests

API_BASE = "https://api.zsxq.com/v2"
X_VERSION = "2.64.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ZSXQ 返回码
CODE_UNOFFICIAL_BLOCK = 1059  # 非官方工具访问，概率性拦截，可重试


class CookieExpiredError(Exception):
    """ZSXQ 登录态失效（Cookie 过期）。"""


def _extract_access_token(cookie_raw: str) -> str:
    """从完整 Cookie 字符串中提取 zsxq_access_token；若本身就是 token 则原样返回。"""
    match = re.search(r"zsxq_access_token=([^;]+)", cookie_raw)
    if match:
        return match.group(1)
    return cookie_raw.strip()


class ZsxqClient:
    def __init__(self, cookie_raw: str, max_retries: int = 4):
        if not cookie_raw:
            raise ValueError("ZSXQ_COOKIE 未配置")
        self._token = _extract_access_token(cookie_raw)
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": self._token,
                "User-Agent": USER_AGENT,
                "x-version": X_VERSION,
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://wx.zsxq.com",
                "Referer": "https://wx.zsxq.com/",
            }
        )

    def _request_topics(self, group_id: str, params: dict) -> dict:
        """发一次请求，处理登录失效与 1059 概率拦截（退避重试）。"""
        last_info = ""
        for attempt in range(self._max_retries):
            resp = self._session.get(
                f"{API_BASE}/groups/{group_id}/topics",
                params=params,
                headers={"x-request-id": str(uuid.uuid4())},
                timeout=15,
            )
            if resp.status_code in (401, 403):
                raise CookieExpiredError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()

            if data.get("succeeded"):
                return data

            code = data.get("code")
            info = str(data.get("info") or data.get("error") or data)
            last_info = info

            if code == CODE_UNOFFICIAL_BLOCK:
                wait = 2 * (attempt + 1)
                logging.warning(f"  命中 1059 概率拦截，{wait}s 后重试 ({attempt + 1}/{self._max_retries})")
                time.sleep(wait)
                continue

            if "登录" in info or "login" in info.lower():
                raise CookieExpiredError(info)

            raise RuntimeError(f"ZSXQ API error(code={code}): {info}")

        raise RuntimeError(f"ZSXQ 多次重试仍被拦截(1059): {last_info}")

    def fetch_topics(self, group_id: str, scope: str, max_pages: int = 5, count: int = 20):
        """抓取指定星球+范围(scope=by_owner/digests)的主题列表，按发布时间分页向后翻。

        返回标准化后的 topic 字典列表（未去重，调用方负责去重）。
        """
        results = []
        end_time = None
        for _ in range(max_pages):
            params = {"scope": scope, "count": count}
            if end_time:
                params["end_time"] = end_time
            data = self._request_topics(group_id, params)

            topics = data.get("resp_data", {}).get("topics", [])
            if not topics:
                break

            for topic in topics:
                results.append(_normalize_topic(topic, group_id, scope))

            end_time = topics[-1].get("create_time")
            if not end_time:
                break
            time.sleep(1)  # 限速，降低触发风控概率

        return results


# ZSXQ 正文里的行内实体标签，形如：
#   <e type="text_bold" title="%E5%8A%A0%E7%B2%97" />
#   <e type="hashtag" hid="..." title="%23%E8%AF%9D%E9%A2%98%23" />
#   <e type="web" href="..." title="%E9%93%BE%E6%8E%A5%E6%96%87%E5%AD%97" />
#   <e type="mention" uid="..." title="%40%E6%9F%90%E4%BA%BA" />
# title 属性是 URL 编码后的展示文字，这里还原成纯文本。
_ENTITY_RE = re.compile(r'<e\b[^>]*?\btitle="([^"]*)"[^>]*/?>')


def clean_text(text: str) -> str:
    if not text:
        return ""

    def _repl(m):
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return m.group(1)

    text = _ENTITY_RE.sub(_repl, text)
    # 去掉残留的其它 <e .../> 标签（无 title 的情况）
    text = re.sub(r"<e\b[^>]*/?>", "", text)
    return text.strip()


def _normalize_topic(topic: dict, group_id: str, scope: str) -> dict:
    topic_id = topic.get("topic_id")
    ttype = topic.get("type", "")

    author = ""
    # 帖子自身可能带 title（q&a、文章类）
    title = topic.get("title", "") or ""
    content = ""

    talk = topic.get("talk")
    if talk:
        author = talk.get("owner", {}).get("name", "") or author
        content = talk.get("text", "") or ""
        article = talk.get("article")
        if article and not title:
            title = article.get("title", "") or ""

    question = topic.get("question")
    if question:
        # q&a：作者取提问者，正文用「问：… 答：…」拼接
        author = question.get("owner", {}).get("name", "") or author
        q_text = question.get("text", "") or ""
        answer = topic.get("answer") or {}
        a_text = answer.get("text", "") or ""
        parts = []
        if q_text:
            parts.append(f"问：{q_text}")
        if a_text:
            parts.append(f"答：{a_text}")
        content = "\n".join(parts) if parts else content

    return {
        "topic_id": str(topic_id),
        "group_id": group_id,
        "scope": scope,
        "type": ttype,
        "author": author,
        "title": clean_text(title),
        "content": clean_text(content),
        "create_time": topic.get("create_time", ""),
        "likes_count": topic.get("likes_count", 0) or 0,
        "comments_count": topic.get("comments_count", 0) or 0,
        "url": f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}",
        "digested": bool(topic.get("digested", False)),
        "raw": topic,
    }
